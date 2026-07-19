"""Tenant-level custom-tool registry with built-in tool overrides.

Custom tools are webhook-based tools that the copilot can invoke by making an
HTTP call to a tenant-configured endpoint. The registry stores them per-tenant
and enforces a hard cap (≤ 15) mirroring Captain's limit.

Built-in tools (COPILOT_TOOLS) may have their per-tenant description or enabled
flag overridden via a separate ``builtin_tool_overrides`` document.

Design mirrors ``live_faq.py``:
- ``ToolsStorePort`` protocol defines the interface.
- ``InMemoryToolsStore`` for tests / local dev.
- ``FirestoreToolsStore`` for production (lazy import, sync SDK run in thread).
- ``build_tools_store(settings)`` factory returns the right implementation.

Security note: ``auth_config`` contains secrets (bearer tokens, passwords,
API keys). The dataclass stores them internally but ``to_safe_dict()`` NEVER
returns them — only ``auth_type`` and the boolean ``has_auth`` are exposed.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Slug must match Gemini function-name rules and be ≤ 64 chars.
_SLUG_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_SLUG_MAX = 64

# Hard cap on custom tools per tenant (Captain parity).
CUSTOM_TOOLS_CAP = 15


@dataclass
class CustomTool:
    """Represents a tenant-configured webhook tool."""

    slug: str
    title: str
    description: str
    endpoint_url: str
    http_method: str  # "GET" | "POST"
    auth_type: str  # "none" | "bearer" | "basic" | "api_key"
    auth_config: dict[str, Any] = field(default_factory=dict)
    param_schema: dict[str, Any] = field(default_factory=dict)
    request_template: str = ""
    response_template: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Full serialisation including auth_config (for internal/Firestore use)."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "endpoint_url": self.endpoint_url,
            "http_method": self.http_method,
            "auth_type": self.auth_type,
            "auth_config": dict(self.auth_config),
            "param_schema": dict(self.param_schema),
            "request_template": self.request_template,
            "response_template": self.response_template,
            "enabled": self.enabled,
        }

    def to_safe_dict(self) -> dict[str, Any]:
        """Serialisation safe for API responses — auth_config is NEVER included."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "endpoint_url": self.endpoint_url,
            "http_method": self.http_method,
            "auth_type": self.auth_type,
            "has_auth": bool(self.auth_config),
            "param_schema": dict(self.param_schema),
            "request_template": self.request_template,
            "response_template": self.response_template,
            "enabled": self.enabled,
        }


@runtime_checkable
class ToolsStorePort(Protocol):
    """Async interface for the custom-tool registry."""

    async def list_custom(self) -> list[CustomTool]:
        """Return all custom tools (active and inactive)."""
        ...

    async def get_custom(self, slug: str) -> CustomTool | None:
        """Return the tool with the given slug, or None."""
        ...

    async def create_custom(self, tool: CustomTool) -> str:
        """Persist a new custom tool and return its slug."""
        ...

    async def update_custom(self, slug: str, fields: dict[str, Any]) -> None:
        """Apply a partial update to an existing tool (no-op if missing)."""
        ...

    async def delete_custom(self, slug: str) -> None:
        """Remove a custom tool (no-op if missing)."""
        ...

    async def get_builtin_overrides(self) -> dict[str, dict[str, Any]]:
        """Return per-builtin override map ``{name: {enabled?, description?}}``."""
        ...

    async def set_builtin_override(self, name: str, fields: dict[str, Any]) -> None:
        """Merge ``fields`` into the override entry for built-in ``name``."""
        ...


def _slug_from_title(title: str) -> str:
    """Derive a slug from a human title: lowercase, spaces→underscores, strip
    non-word chars, prefix ``custom_``, truncate to fit slug max."""
    base = re.sub(r"[^a-zA-Z0-9_]", "_", title.strip().lower())
    base = re.sub(r"_+", "_", base).strip("_")
    if not base or not re.match(r"^[a-zA-Z_]", base):
        base = "tool_" + base
    slug = f"custom_{base}"
    # Leave room for a suffix if needed: 6 chars + 1 underscore = 7
    return slug[: _SLUG_MAX - 7]


def make_slug(title: str, existing: set[str]) -> str:
    """Return a unique slug derived from *title*, appending a random suffix on
    collision. The caller owns the uniqueness constraint — this function ensures
    the generated slug doesn't clash with *existing*."""
    candidate = _slug_from_title(title)
    if not _SLUG_RE.match(candidate):
        candidate = "custom_tool"
    if candidate not in existing:
        return candidate
    suffix = uuid.uuid4().hex[:6]
    return f"{candidate}_{suffix}"[: _SLUG_MAX]


class InMemoryToolsStore:
    """Volatile custom-tool registry for tests/local dev."""

    def __init__(self) -> None:
        self._tools: dict[str, CustomTool] = {}
        self._overrides: dict[str, dict[str, Any]] = {}

    async def list_custom(self) -> list[CustomTool]:
        return list(self._tools.values())

    async def get_custom(self, slug: str) -> CustomTool | None:
        return self._tools.get(slug)

    async def create_custom(self, tool: CustomTool) -> str:
        self._tools[tool.slug] = tool
        return tool.slug

    async def update_custom(self, slug: str, fields: dict[str, Any]) -> None:
        current = self._tools.get(slug)
        if current is None:
            return
        data = current.to_dict()
        data.update(fields)
        self._tools[slug] = CustomTool(
            slug=data["slug"],
            title=data["title"],
            description=data["description"],
            endpoint_url=data["endpoint_url"],
            http_method=data["http_method"],
            auth_type=data["auth_type"],
            auth_config=data.get("auth_config", {}),
            param_schema=data.get("param_schema", {}),
            request_template=data.get("request_template", ""),
            response_template=data.get("response_template", ""),
            enabled=data.get("enabled", True),
        )

    async def delete_custom(self, slug: str) -> None:
        self._tools.pop(slug, None)

    async def get_builtin_overrides(self) -> dict[str, dict[str, Any]]:
        return dict(self._overrides)

    async def set_builtin_override(self, name: str, fields: dict[str, Any]) -> None:
        current = self._overrides.get(name, {})
        merged = {**current, **fields}
        self._overrides[name] = merged


class FirestoreToolsStore:
    """Firestore-backed custom-tool registry.

    Custom tools live in ``custom_tools/<slug>`` (one doc per tool).
    Built-in overrides live in a single doc ``builtin_tool_overrides/current``
    as a map ``{name: {enabled, description}}``.

    The Firestore SDK is synchronous; all calls run in a worker thread via
    ``asyncio.to_thread``. Every read degrades cleanly to empty on failure.
    """

    _OVERRIDES_COLLECTION = "builtin_tool_overrides"
    _OVERRIDES_DOC = "current"

    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = "custom_tools"
        _log.info(
            "firestore_tools_store_initialized",
            project=settings.firestore_project_id,
            collection=self._collection_name,
        )

    def _col(self) -> Any:
        return self._client.collection(self._collection_name)

    @staticmethod
    def _to_tool(slug: str, data: dict[str, Any]) -> CustomTool:
        return CustomTool(
            slug=slug,
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            endpoint_url=str(data.get("endpoint_url", "")),
            http_method=str(data.get("http_method", "GET")),
            auth_type=str(data.get("auth_type", "none")),
            auth_config=dict(data.get("auth_config") or {}),
            param_schema=dict(data.get("param_schema") or {}),
            request_template=str(data.get("request_template", "")),
            response_template=str(data.get("response_template", "")),
            enabled=bool(data.get("enabled", True)),
        )

    async def list_custom(self) -> list[CustomTool]:
        def _read() -> list[CustomTool]:
            return [
                self._to_tool(doc.id, doc.to_dict() or {}) for doc in self._col().stream()
            ]

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("tools_store_list_failed", error=str(e))
            return []

    async def get_custom(self, slug: str) -> CustomTool | None:
        def _read() -> CustomTool | None:
            snap = self._col().document(slug).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            return self._to_tool(slug, data if isinstance(data, dict) else {})

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("tools_store_get_failed", slug=slug, error=str(e))
            return None

    async def create_custom(self, tool: CustomTool) -> str:
        def _write() -> None:
            self._col().document(tool.slug).set(tool.to_dict())

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("tools_store_create_failed", slug=tool.slug, error=str(e))
        return tool.slug

    async def update_custom(self, slug: str, fields: dict[str, Any]) -> None:
        def _write() -> None:
            self._col().document(slug).update(fields)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("tools_store_update_failed", slug=slug, error=str(e))

    async def delete_custom(self, slug: str) -> None:
        def _delete() -> None:
            self._col().document(slug).delete()

        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            _log.error("tools_store_delete_failed", slug=slug, error=str(e))

    async def get_builtin_overrides(self) -> dict[str, dict[str, Any]]:
        def _read() -> dict[str, dict[str, Any]]:
            snap = (
                self._client
                .collection(self._OVERRIDES_COLLECTION)
                .document(self._OVERRIDES_DOC)
                .get()
            )
            if not snap.exists:
                return {}
            data = snap.to_dict()
            return data if isinstance(data, dict) else {}

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("tools_store_get_overrides_failed", error=str(e))
            return {}

    async def set_builtin_override(self, name: str, fields: dict[str, Any]) -> None:
        def _write() -> None:
            doc_ref = (
                self._client
                .collection(self._OVERRIDES_COLLECTION)
                .document(self._OVERRIDES_DOC)
            )
            # Use set(merge=True) so other names are not wiped.
            doc_ref.set({name: fields}, merge=True)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("tools_store_set_override_failed", name=name, error=str(e))


def build_tools_store(settings: Settings) -> InMemoryToolsStore | FirestoreToolsStore:
    """Return the right tools-store implementation for *settings*.

    Falls back to ``InMemoryToolsStore`` when ``firestore_project_id`` is empty
    or Firestore initialisation fails, so boot never breaks.
    """
    if not settings.firestore_project_id:
        _log.info("tools_store_inmemory_no_project_id")
        return InMemoryToolsStore()
    try:
        return FirestoreToolsStore(settings)
    except Exception as e:
        _log.warning("tools_store_firestore_init_failed", error=str(e))
        return InMemoryToolsStore()
