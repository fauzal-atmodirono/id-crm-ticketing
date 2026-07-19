"""Copilot tool declarations (for Gemini function calling) + their executor.

The tools are read-only lookups. `conversation_id` is fixed per Copilot turn and
injected by the executor, so the model never has to pass it — this prevents the
model from probing arbitrary conversations.

Custom webhook tools are executed by ToolExecutor when a model-requested function
name is not a built-in.  Safety invariants (HTTPS-only, 10 s timeout, 16 KB cap,
optional host allowlist) are enforced here; any violation returns a model-visible
error dict and never raises.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
import structlog

if TYPE_CHECKING:
    from chatbot.features.assist.chatwoot_context import ChatwootContextClient
    from chatbot.features.chat.adapters.tools_store import ToolsStorePort
    from chatbot.features.chat.ports import KnowledgePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_BUILTIN_NAMES: frozenset[str] = frozenset(
    [
        "get_conversation_transcript",
        "get_contact_details",
        "list_past_conversations",
        "search_knowledge_base",
    ]
)

# Maximum response body size read from a custom webhook (bytes).
_RESPONSE_CAP = 16 * 1024  # 16 KB
_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300


def _is_private_address(addr: str) -> bool:
    """Return True if *addr* is a loopback, private, link-local, or reserved IP."""
    try:
        ip = ipaddress.ip_address(addr)
        return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


# Module-level DNS resolver — patched in tests via unittest.mock.patch.
_dns_getaddrinfo = socket.getaddrinfo


def _ssrf_check(host: str, slug: str) -> str | None:
    """Return an error string if *host* is a private/reserved address, else None.

    Handles both IP literals and hostname resolution. Fail-closed: if DNS
    resolution fails the host is rejected (the request would fail anyway).
    Module-level ``_dns_getaddrinfo`` is used so tests can patch it.
    """
    # IP literal check — fast path; no DNS needed.
    try:
        ip_obj = ipaddress.ip_address(host)
        if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local or ip_obj.is_reserved:
            _log.warning("custom_tool_private_ip_literal", slug=slug, host=host)
            return f"host '{host}' resolves to a private/reserved address"
        return None  # Public IP literal — ok.
    except ValueError:
        pass  # Not an IP literal; fall through to DNS.
    # DNS resolution check (fail-closed on error).
    try:
        results = _dns_getaddrinfo(host, None)
        for _family, _type, _proto, _canonname, sockaddr in results:
            if _is_private_address(sockaddr[0]):
                _log.warning(
                    "custom_tool_resolved_private_ip", slug=slug, host=host, resolved=sockaddr[0]
                )
                return f"host '{host}' resolves to a private/reserved address"
    except Exception as exc:
        _log.warning("custom_tool_dns_resolution_failed", slug=slug, host=host, error=str(exc))
        return f"host '{host}' could not be resolved"
    return None


def _check_url(url: str, allowed_hosts: list[str], slug: str) -> str | None:
    """Return an error string if *url* fails safety checks, else None.

    Checks (in order):
    1. URL must parse and use HTTPS.
    2. If an allowlist is configured and the host is NOT in it, reject.
    3. If the host IS in the allowlist, skip SSRF checks (admin permitted it).
    4. Otherwise run SSRF checks via ``_ssrf_check``.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "invalid endpoint URL"
    if parsed.scheme != "https":
        _log.warning("custom_tool_non_https", slug=slug, url=url)
        return "custom tool endpoint must use HTTPS"
    host = parsed.hostname or ""
    if allowed_hosts:
        if host not in allowed_hosts:
            _log.warning("custom_tool_host_blocked", slug=slug, host=host, allowed=allowed_hosts)
            return f"host '{host}' is not in the allowed hosts list"
        # Admin explicitly allowlisted — skip SSRF checks.
        return None
    return _ssrf_check(host, slug)


def _build_auth(auth_type: str, auth_config: dict[str, Any]) -> tuple[dict[str, str], tuple[str, str] | None]:
    """Return (extra_headers, basic_auth_tuple) for the given auth config."""
    headers: dict[str, str] = {}
    auth: tuple[str, str] | None = None
    kind = (auth_type or "none").lower()
    if kind == "bearer":
        headers["Authorization"] = f"Bearer {auth_config.get('token', '')}"
    elif kind == "basic":
        auth = (auth_config.get("username", ""), auth_config.get("password", ""))
    elif kind == "api_key":
        headers[auth_config.get("header", "X-API-Key")] = auth_config.get("value", "")
    return headers, auth


def _build_body(template: str, args: dict[str, Any]) -> bytes:
    """Render *template* with *args* substitutions, or JSON-encode *args*."""
    if template:
        body = template
        for key, value in args.items():
            body = body.replace(f"{{{{{key}}}}}", str(value))
        return body.encode()
    return json.dumps(args).encode()


def _parse_response(text: str, response_template: str) -> Any:
    """Apply *response_template* (if set) then JSON-parse; fall back to text."""
    if response_template:
        rendered = response_template.replace("{{body}}", text)
        try:
            return json.loads(rendered)
        except json.JSONDecodeError:
            return rendered
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text

COPILOT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_conversation_transcript",
        "description": (
            "Return the full transcript of the CURRENT conversation, including "
            "internal private notes (flagged private=true). Use to see what the "
            "customer said and what the team has already discussed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_contact_details",
        "description": (
            "Return the current customer's profile and custom attributes "
            "(e.g. plan, customer type, tags)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_past_conversations",
        "description": (
            "List this customer's OTHER (past) conversations with status and "
            "labels. Use to check history before answering."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_knowledge_base",
        "description": "Search the product/FAQ knowledge base for grounding facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
                "limit": {"type": "integer", "description": "max hits (1-10)"},
            },
            "required": ["query"],
        },
    },
]


class ToolExecutor:
    def __init__(
        self,
        context: ChatwootContextClient,
        knowledge: KnowledgePort,
        conversation_id: str,
        tools_store: ToolsStorePort | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._ctx = context
        self._kb = knowledge
        self._conv_id = conversation_id
        self._tools_store = tools_store
        self._settings = settings

    async def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_conversation_transcript":
            return {"turns": await self._ctx.get_transcript(self._conv_id)}
        if name == "get_contact_details":
            return {"contact": await self._ctx.get_contact(self._conv_id)}
        if name == "list_past_conversations":
            return {"conversations": await self._ctx.list_contact_conversations(self._conv_id)}
        if name == "search_knowledge_base":
            query = str(args.get("query", "")).strip()
            limit = min(max(int(args.get("limit", 3)), 1), 10)
            articles = await self._kb.search_kb(query, limit)
            return {
                "articles": [
                    {"title": a.title, "content": a.content[:500], "url": a.url}
                    for a in articles
                ]
            }
        # --- Custom webhook tool dispatch ---
        if self._tools_store is not None:
            tool = await self._tools_store.get_custom(name)
            if tool is not None and tool.enabled:
                return await self._run_webhook(tool, args)
        return {"error": f"unknown tool: {name}"}

    async def _run_webhook(self, tool: Any, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a custom webhook tool safely.

        Returns a result dict on success or ``{"error": "..."}`` on any
        failure — never raises so the model always gets a response.
        """
        allowed_hosts = self._settings.custom_tool_allowed_hosts if self._settings else []
        url_error = _check_url(tool.endpoint_url, allowed_hosts, tool.slug)
        if url_error:
            return {"error": url_error}

        method = tool.http_method.upper()
        auth_headers, auth = _build_auth(tool.auth_type, tool.auth_config or {})
        headers = {**auth_headers, "Content-Type": "application/json"}
        body_bytes = _build_body(tool.request_template, args)

        try:
            return await self._issue_request(tool, method, headers, auth, body_bytes)
        except httpx.TimeoutException:
            _log.warning("custom_tool_timeout", slug=tool.slug, url=tool.endpoint_url)
            return {"error": "webhook timed out"}
        except Exception as exc:
            _log.warning("custom_tool_error", slug=tool.slug, error=str(exc))
            return {"error": f"webhook error: {exc!s}"}

    async def _issue_request(
        self,
        tool: Any,
        method: str,
        headers: dict[str, str],
        auth: tuple[str, str] | None,
        body_bytes: bytes,
    ) -> dict[str, Any]:
        """Issue the HTTP request and return the parsed result dict.

        The response is streamed and reading stops as soon as _RESPONSE_CAP
        bytes have been accumulated, so a hostile endpoint cannot force
        unbounded memory use.
        """
        kwargs: dict[str, Any] = {"headers": headers, "auth": auth}
        if method != "GET":
            kwargs["content"] = body_bytes

        buf = bytearray()
        truncated = False
        client = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
        async with client, client.stream(method, tool.endpoint_url, **kwargs) as response:
            if not (_HTTP_OK_MIN <= response.status_code < _HTTP_OK_MAX):
                _log.warning(
                    "custom_tool_non_2xx", slug=tool.slug, status=response.status_code
                )
                return {"error": f"webhook returned HTTP {response.status_code}"}
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) >= _RESPONSE_CAP:
                    truncated = True
                    break

        if truncated:
            _log.info("custom_tool_response_truncated", slug=tool.slug, cap=_RESPONSE_CAP)

        raw = bytes(buf[:_RESPONSE_CAP])
        text = raw.decode("utf-8", errors="replace")
        return {"result": _parse_response(text, tool.response_template)}
