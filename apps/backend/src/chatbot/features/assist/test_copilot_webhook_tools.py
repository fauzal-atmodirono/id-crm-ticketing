"""Tests for custom webhook tool execution in ToolExecutor.

All HTTP is mocked via respx so no real network calls are made.  Each test
verifies exactly one safety or functional property so failures are easy to
diagnose.
"""

from __future__ import annotations

import json
import socket

import httpx
import pytest
import respx

import chatbot.features.assist.copilot_tools as _ct_module
from chatbot.features.assist.copilot_tools import ToolExecutor
from chatbot.features.chat.adapters.tools_store import CustomTool, InMemoryToolsStore
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import Settings

# ---------------------------------------------------------------------------
# DNS mock fixture: returns a public IP for any hostname so the SSRF check
# passes in tests that use api.example.com (no real DNS available in CI).
# ---------------------------------------------------------------------------

_PUBLIC_IP_ADDRINFO = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def mock_public_dns(monkeypatch):
    """Patch the module-level DNS resolver to return a public IP for test hostnames."""
    monkeypatch.setattr(_ct_module, "_dns_getaddrinfo", lambda _host, _port: _PUBLIC_IP_ADDRINFO)

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


class _FakeCtx:
    async def get_transcript(self, conversation_id: str):
        return []

    async def get_contact(self, conversation_id: str):
        return {}

    async def list_contact_conversations(self, conversation_id: str):
        return []


class _FakeKb:
    async def search_kb(self, query: str, limit: int = 3):
        return [KbArticle(title="T", content="C", url="http://u")]


def _settings(**kw) -> Settings:
    return Settings(chatwoot_enabled=False, proton_backend_key="k", **kw)


def _executor(store: InMemoryToolsStore, settings: Settings | None = None) -> ToolExecutor:
    return ToolExecutor(
        _FakeCtx(),
        _FakeKb(),
        conversation_id="42",
        tools_store=store,
        settings=settings or _settings(),
    )


def _tool(
    slug: str = "custom_check_order",
    endpoint_url: str = "https://api.example.com/orders",
    http_method: str = "POST",
    auth_type: str = "none",
    auth_config: dict | None = None,
    request_template: str = "",
    response_template: str = "",
    enabled: bool = True,
    param_schema: dict | None = None,
) -> CustomTool:
    return CustomTool(
        slug=slug,
        title="Check Order",
        description="Lookup an order",
        endpoint_url=endpoint_url,
        http_method=http_method,
        auth_type=auth_type,
        auth_config=auth_config or {},
        request_template=request_template,
        response_template=response_template,
        param_schema=param_schema or {"type": "object", "properties": {}},
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Happy-path: JSON response returned as {"result": ...}
# ---------------------------------------------------------------------------


@respx.mock
async def test_webhook_tool_happy_path_post() -> None:
    """ToolExecutor issues a POST to the endpoint and returns {"result": ...}."""
    store = InMemoryToolsStore()
    await store.create_custom(_tool())

    respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, json={"status": "shipped"})
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {"order_id": "123"})
    assert result == {"result": {"status": "shipped"}}


@respx.mock
async def test_webhook_tool_happy_path_get() -> None:
    """GET method is used when http_method is GET."""
    store = InMemoryToolsStore()
    await store.create_custom(_tool(http_method="GET"))

    respx.get("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})
    assert result == {"result": {"ok": True}}


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------


@respx.mock
async def test_bearer_auth_header_sent() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(
        _tool(auth_type="bearer", auth_config={"token": "mysecret"})
    )

    route = respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    ex = _executor(store)
    await ex.run("custom_check_order", {})

    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer mysecret"


@respx.mock
async def test_api_key_auth_header_sent() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(
        _tool(auth_type="api_key", auth_config={"header": "X-Token", "value": "abc"})
    )

    route = respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    ex = _executor(store)
    await ex.run("custom_check_order", {})

    request = route.calls.last.request
    assert request.headers["x-token"] == "abc"


@respx.mock
async def test_basic_auth_credentials_sent() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(
        _tool(auth_type="basic", auth_config={"username": "user", "password": "pass"})
    )

    route = respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    ex = _executor(store)
    await ex.run("custom_check_order", {})

    request = route.calls.last.request
    # httpx encodes Basic auth in the Authorization header
    assert "Authorization" in request.headers or "authorization" in request.headers


# ---------------------------------------------------------------------------
# Safety: non-HTTPS URL → error, no HTTP call
# ---------------------------------------------------------------------------


async def test_non_https_url_returns_error_no_call() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(_tool(endpoint_url="http://evil.example.com/hook"))

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert "error" in result
    assert "HTTPS" in result["error"] or "https" in result["error"].lower()


# ---------------------------------------------------------------------------
# Safety: host allowlist
# ---------------------------------------------------------------------------


async def test_disallowed_host_returns_error() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(_tool(endpoint_url="https://blocked.example.com/hook"))

    settings = _settings(custom_tool_allowed_hosts=["allowed.example.com"])
    ex = _executor(store, settings=settings)
    result = await ex.run("custom_check_order", {})

    assert "error" in result
    assert "blocked.example.com" in result["error"] or "allowed" in result["error"].lower()


@respx.mock
async def test_allowed_host_succeeds() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(_tool(endpoint_url="https://allowed.example.com/hook"))

    respx.post("https://allowed.example.com/hook").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    settings = _settings(custom_tool_allowed_hosts=["allowed.example.com"])
    ex = _executor(store, settings=settings)
    result = await ex.run("custom_check_order", {})

    assert result == {"result": {"ok": True}}


# ---------------------------------------------------------------------------
# Safety: timeout → error no raise
# ---------------------------------------------------------------------------


@respx.mock
async def test_timeout_returns_error_no_raise() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(_tool())

    respx.post("https://api.example.com/orders").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert "error" in result
    assert "timed out" in result["error"] or "timeout" in result["error"].lower()


# ---------------------------------------------------------------------------
# Safety: non-2xx → error no raise
# ---------------------------------------------------------------------------


@respx.mock
async def test_non_2xx_returns_error() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(_tool())

    respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert "error" in result
    assert "500" in result["error"]


# ---------------------------------------------------------------------------
# Safety: oversized response is capped (not errored)
# ---------------------------------------------------------------------------


@respx.mock
async def test_oversized_response_is_capped() -> None:
    """A response larger than 16 KB is truncated; executor still returns a result."""
    store = InMemoryToolsStore()
    # Return plain text so we can check truncation without JSON parse issues.
    await store.create_custom(_tool(response_template=""))

    big_text = "x" * (20 * 1024)  # 20 KB
    respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, text=big_text)
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    # Should succeed (result key), and the value must not exceed the cap.
    assert "result" in result
    # The result is the truncated text (or JSON-parsed; either way len ≤ 16 KB).
    raw = result["result"]
    assert len(str(raw)) <= 16 * 1024 + 50  # small margin for JSON escaping


# ---------------------------------------------------------------------------
# Disabled tool → falls through to unknown-tool error
# ---------------------------------------------------------------------------


async def test_disabled_tool_returns_unknown_tool_error() -> None:
    store = InMemoryToolsStore()
    await store.create_custom(_tool(enabled=False))

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert "error" in result


# ---------------------------------------------------------------------------
# No tools_store → unknown tool falls through as before
# ---------------------------------------------------------------------------


async def test_no_tools_store_unknown_tool_error() -> None:
    ex = ToolExecutor(_FakeCtx(), _FakeKb(), conversation_id="42")
    result = await ex.run("custom_check_order", {})
    assert result == {"error": "unknown tool: custom_check_order"}


# ---------------------------------------------------------------------------
# Fix 4: Private / link-local IP SSRF protection in _check_url
# ---------------------------------------------------------------------------


async def test_link_local_ip_literal_rejected() -> None:
    """https://169.254.169.254/... must be rejected (link-local IP literal)."""
    from chatbot.features.assist.copilot_tools import _check_url  # noqa: PLC0415

    err = _check_url("https://169.254.169.254/latest/meta-data", [], slug="test")
    assert err is not None
    assert "private" in err.lower() or "reserved" in err.lower() or "169.254" in err


async def test_loopback_ip_literal_rejected() -> None:
    """https://127.0.0.1/... must be rejected (loopback IP literal)."""
    from chatbot.features.assist.copilot_tools import _check_url  # noqa: PLC0415

    err = _check_url("https://127.0.0.1/internal", [], slug="test")
    assert err is not None
    assert "private" in err.lower() or "loopback" in err.lower() or "127.0.0.1" in err


async def test_public_host_allowed_with_mock_resolution() -> None:
    """A normal public HTTPS host passes _check_url when DNS resolves to a public IP.

    The autouse mock_public_dns fixture already returns a public IP, so no extra
    patching is needed here.
    """
    from chatbot.features.assist.copilot_tools import _check_url  # noqa: PLC0415

    # mock_public_dns fixture returns 93.184.216.34 (public) for any hostname.
    err = _check_url("https://api.example.com/hook", [], slug="test")
    assert err is None


async def test_private_rfc1918_ip_literal_rejected() -> None:
    """https://10.0.0.1/... must be rejected (private RFC 1918 address)."""
    from chatbot.features.assist.copilot_tools import _check_url  # noqa: PLC0415

    err = _check_url("https://10.0.0.1/internal", [], slug="test")
    assert err is not None


async def test_allowlisted_host_skips_ip_check() -> None:
    """When a host is explicitly allowlisted, the IP safety check is bypassed."""
    from chatbot.features.assist.copilot_tools import _check_url  # noqa: PLC0415

    # Even a link-local IP literal is allowed when it's in the allowlist.
    err = _check_url(
        "https://169.254.169.254/meta",
        allowed_hosts=["169.254.169.254"],
        slug="test",
    )
    assert err is None


# ---------------------------------------------------------------------------
# Streaming: oversized response is truncated at the cap boundary
# ---------------------------------------------------------------------------


@respx.mock
async def test_streaming_oversized_response_capped_at_16kb() -> None:
    """A response larger than 16 KB is truncated to exactly _RESPONSE_CAP bytes."""
    from chatbot.features.assist.copilot_tools import _RESPONSE_CAP  # noqa: PLC0415

    store = InMemoryToolsStore()
    await store.create_custom(_tool(response_template=""))

    big_body = b"A" * (32 * 1024)  # 32 KB — well over the 16 KB cap
    respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, content=big_body)
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert "result" in result
    # The raw bytes fed to _parse_response were capped; since the body is not
    # valid JSON, the fallback is the decoded text string.
    raw_text: str = result["result"]
    assert isinstance(raw_text, str)
    assert len(raw_text) == _RESPONSE_CAP  # exactly 16 KB of "A"s
    assert all(c == "A" for c in raw_text)


@respx.mock
async def test_streaming_small_json_response_unaffected() -> None:
    """A small well-formed JSON response is streamed and parsed without truncation."""
    store = InMemoryToolsStore()
    await store.create_custom(_tool(response_template=""))

    respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "42", "status": "delivered"})
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert result == {"result": {"order_id": "42", "status": "delivered"}}


@respx.mock
async def test_streaming_non_2xx_returns_error_no_raise() -> None:
    """Non-2xx status from a streamed response still yields {"error": ...}."""
    store = InMemoryToolsStore()
    await store.create_custom(_tool(response_template=""))

    respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert "error" in result
    assert "503" in result["error"]


@respx.mock
async def test_streaming_timeout_returns_error_no_raise() -> None:
    """Timeout during streaming still returns {"error": ...} and never raises."""
    store = InMemoryToolsStore()
    await store.create_custom(_tool(response_template=""))

    respx.post("https://api.example.com/orders").mock(
        side_effect=httpx.TimeoutException("stream timed out")
    )

    ex = _executor(store)
    result = await ex.run("custom_check_order", {})

    assert "error" in result
    assert "timed out" in result["error"] or "timeout" in result["error"].lower()


# ---------------------------------------------------------------------------
# request_template rendering
# ---------------------------------------------------------------------------


@respx.mock
async def test_request_template_rendered_with_args() -> None:
    """request_template placeholders {{key}} are replaced with arg values."""
    store = InMemoryToolsStore()
    template = '{"id": "{{order_id}}"}'
    await store.create_custom(_tool(request_template=template))

    route = respx.post("https://api.example.com/orders").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    ex = _executor(store)
    await ex.run("custom_check_order", {"order_id": "ABC-123"})

    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == {"id": "ABC-123"}
