"""Tenant-level custom-tool registry CRUD + built-in tool overrides.

Endpoints (all guarded by ``x-api-key``):
  GET    /kb/tools                     — list built-ins (merged with overrides) + custom tools
  POST   /kb/tools                     — create a custom tool
  PUT    /kb/tools/{slug}              — partial update a custom tool
  DELETE /kb/tools/{slug}              — delete a custom tool
  PUT    /kb/tools/builtins/{name}     — set enabled/description override on a built-in

Security rules enforced here:
- ``auth_config`` is NEVER returned in any response (``to_safe_dict()``).
- ``endpoint_url`` must be HTTPS (422 otherwise).
- ``param_schema`` must be a dict with a ``"type"`` key (422 otherwise).
- Slug must match ``^[a-zA-Z_][a-zA-Z0-9_]*$`` and be ≤ 64 chars.
- Slug must be unique across custom tools AND built-in tool names (409).
- Hard cap: ≤ 15 custom tools per tenant (409 if exceeded).
- Built-in override name must exist in COPILOT_TOOLS (404 otherwise).
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.assist.copilot_tools import COPILOT_TOOLS
from chatbot.features.chat.adapters.tools_store import (
    _SLUG_MAX,
    _SLUG_RE,
    CUSTOM_TOOLS_CAP,
    CustomTool,
    make_slug,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Set of names already occupied by built-in tools.
_BUILTIN_NAMES: frozenset[str] = frozenset(t["name"] for t in COPILOT_TOOLS)


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class ToolCreateRequest(BaseModel):
    slug: str | None = None
    title: str = Field(min_length=1)
    description: str = Field(default="")
    endpoint_url: str = Field(min_length=1)
    http_method: str = Field(default="GET")
    auth_type: str = Field(default="none")
    auth_config: dict[str, Any] = Field(default_factory=dict)
    param_schema: dict[str, Any] = Field(default_factory=dict)
    request_template: str = Field(default="")
    response_template: str = Field(default="")
    enabled: bool = True


class ToolUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    endpoint_url: str | None = None
    http_method: str | None = None
    auth_type: str | None = None
    auth_config: dict[str, Any] | None = None
    param_schema: dict[str, Any] | None = None
    request_template: str | None = None
    response_template: str | None = None
    enabled: bool | None = None


class BuiltinOverrideRequest(BaseModel):
    enabled: bool | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_kb_tools_router(  # noqa: PLR0915 — one builder, many route closures
    store: Any,
    settings: Settings,
) -> APIRouter:
    """Build the /kb/tools router.

    *store* is any object implementing ``ToolsStorePort`` (InMemory or
    Firestore). *settings* provides the API key candidates.
    """
    router = APIRouter(tags=["kb"])

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        candidates = [settings.faq_admin_api_key, settings.proton_backend_key]
        supplied = x_api_key.encode("utf-8")
        for key in candidates:
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_slug(slug: str, context: str = "slug") -> None:
        if not _SLUG_RE.match(slug):
            raise HTTPException(
                status_code=422,
                detail=f"{context} must match ^[a-zA-Z_][a-zA-Z0-9_]*$ (got {slug!r})",
            )
        if len(slug) > _SLUG_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"{context} must be ≤ {_SLUG_MAX} characters",
            )

    def _validate_endpoint_url(url: str) -> None:
        if not url.lower().startswith("https://"):
            raise HTTPException(
                status_code=422,
                detail="endpoint_url must start with https://",
            )

    def _validate_param_schema(schema: dict[str, Any]) -> None:
        if "type" not in schema:
            raise HTTPException(
                status_code=422,
                detail="param_schema must be a dict containing a 'type' key",
            )

    # ------------------------------------------------------------------
    # GET /kb/tools
    # ------------------------------------------------------------------

    @router.get("/kb/tools")
    async def list_tools(
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)

        # Built-ins: merge COPILOT_TOOLS with per-tool overrides.
        try:
            overrides = await store.get_builtin_overrides()
        except Exception as exc:
            _log.error("kb_tools_get_overrides_failed", error=str(exc))
            overrides = {}

        builtins: list[dict[str, Any]] = []
        for tool in COPILOT_TOOLS:
            name = tool["name"]
            override = overrides.get(name, {})
            builtins.append(
                {
                    "name": name,
                    "enabled": override.get("enabled", True),
                    "description": override.get("description", tool.get("description", "")),
                }
            )

        # Custom tools: never raise, degrade to empty list.
        try:
            customs = await store.list_custom()
        except Exception as exc:
            _log.error("kb_tools_list_custom_failed", error=str(exc))
            customs = []

        return {
            "builtins": builtins,
            "custom": [t.to_safe_dict() for t in customs],
        }

    # ------------------------------------------------------------------
    # POST /kb/tools
    # ------------------------------------------------------------------

    @router.post("/kb/tools")
    async def create_tool(
        payload: ToolCreateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)

        # Validate endpoint_url HTTPS.
        _validate_endpoint_url(payload.endpoint_url)

        # Validate param_schema.
        _validate_param_schema(payload.param_schema)

        # Resolve slug.
        try:
            existing_tools = await store.list_custom()
        except Exception as exc:
            _log.error("kb_tools_create_list_failed", error=str(exc))
            existing_tools = []

        existing_slugs: set[str] = {t.slug for t in existing_tools} | _BUILTIN_NAMES

        if payload.slug:
            slug = payload.slug
            _validate_slug(slug)
            if slug in existing_slugs:
                raise HTTPException(
                    status_code=409,
                    detail=f"A tool with slug {slug!r} already exists",
                )
        else:
            slug = make_slug(payload.title, existing_slugs)

        # Enforce cap.
        if len(existing_tools) >= CUSTOM_TOOLS_CAP:
            raise HTTPException(
                status_code=409,
                detail=f"Custom tool cap of {CUSTOM_TOOLS_CAP} reached",
            )

        tool = CustomTool(
            slug=slug,
            title=payload.title,
            description=payload.description,
            endpoint_url=payload.endpoint_url,
            http_method=payload.http_method,
            auth_type=payload.auth_type,
            auth_config=payload.auth_config,
            param_schema=payload.param_schema,
            request_template=payload.request_template,
            response_template=payload.response_template,
            enabled=payload.enabled,
        )
        await store.create_custom(tool)
        return {"slug": slug}

    # ------------------------------------------------------------------
    # PUT /kb/tools/{slug}
    # ------------------------------------------------------------------

    @router.put("/kb/tools/{slug}")
    async def update_tool(
        slug: str,
        payload: ToolUpdateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)

        existing = await store.get_custom(slug)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Tool {slug!r} not found")

        fields = payload.model_dump(exclude_none=True)

        # Validate changed fields.
        if "endpoint_url" in fields:
            _validate_endpoint_url(fields["endpoint_url"])
        if "param_schema" in fields:
            _validate_param_schema(fields["param_schema"])

        await store.update_custom(slug, fields)
        return {"slug": slug, "status": "ok"}

    # ------------------------------------------------------------------
    # DELETE /kb/tools/{slug}
    # ------------------------------------------------------------------

    @router.delete("/kb/tools/{slug}")
    async def delete_tool(
        slug: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)

        existing = await store.get_custom(slug)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Tool {slug!r} not found")

        await store.delete_custom(slug)
        return {"slug": slug, "status": "ok"}

    # ------------------------------------------------------------------
    # PUT /kb/tools/builtins/{name}
    # ------------------------------------------------------------------

    @router.put("/kb/tools/builtins/{name}")
    async def set_builtin_override(
        name: str,
        payload: BuiltinOverrideRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)

        if name not in _BUILTIN_NAMES:
            raise HTTPException(
                status_code=404,
                detail=f"Built-in tool {name!r} not found",
            )

        fields = payload.model_dump(exclude_none=True)
        await store.set_builtin_override(name, fields)
        return {"name": name, "status": "ok"}

    return router
