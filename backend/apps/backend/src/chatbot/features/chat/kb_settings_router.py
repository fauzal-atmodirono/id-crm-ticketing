"""GET/PUT /kb/settings — tenant-level editable settings with env-default fallback.

`GET /kb/settings` returns all eight managed keys as ``{key: {value, source}}``
where source is "override" if the tenant has set that key, or "env" if falling
back to the env default. Never raises — store failures degrade gracefully.

`PUT /kb/settings` accepts a partial override body, validates types (422 on
bad input), merges into the store, and returns the fresh effective settings.
Setting a key to null/"" clears the override and reverts to env.

Both endpoints require the same `x-api-key` guard as the faq-admin and
kb-documents routers (compared constant-time against faq_admin_api_key /
proton_backend_key).
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.chat.settings_facade import get_effective_settings, validate_overrides

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class SettingsOverrideRequest(BaseModel):
    """Partial map of setting keys to new values (or null/"" to clear)."""

    model_config = {"extra": "allow"}

    def overrides(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


def build_kb_settings_router(
    store: TenantSettingsStorePort,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(tags=["kb"])

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        candidates = [settings.faq_admin_api_key, settings.proton_backend_key]
        supplied = x_api_key.encode("utf-8")
        for key in candidates:
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")

    @router.get("/kb/settings")
    async def get_settings_endpoint(
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        try:
            effective = await get_effective_settings(store, settings)
        except Exception as exc:
            _log.error("kb_settings_get_failed", error=str(exc))
            effective = {}
        return {"settings": effective}

    @router.put("/kb/settings")
    async def put_settings_endpoint(
        payload: SettingsOverrideRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        raw = payload.overrides()
        try:
            validated = validate_overrides(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await store.set_overrides(validated)
        try:
            effective = await get_effective_settings(store, settings)
        except Exception as exc:
            _log.error("kb_settings_get_after_put_failed", error=str(exc))
            effective = {}
        return {"settings": effective}

    return router
