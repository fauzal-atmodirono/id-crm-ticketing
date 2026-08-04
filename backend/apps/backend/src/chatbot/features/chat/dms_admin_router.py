"""DMS/TSP integration shell admin API -- CRUD for the operator-configured
DMS/TSP connection plus a "Test connection" endpoint, backing the
Integrations "DMS / TSP" card. Gated behind the `integration.manage`
permission via Phase 1's `require_permission`, matching
`pic_admin_router.py`'s wiring exactly.

The credential stays write-only end to end here, same as in
`dms_config_store.py`: PUT accepts an optional `credential` field and hands
it straight to `DmsConfigStore.save()`; GET builds its response from
`public_dict()`, which has no credential field to omit in the first place;
and the connection-test response is built only from `ProbeResult` (a status
plus `probe()`'s own sanitised message) -- never from `DmsConfigBody`, so
nothing the operator just typed can round-trip back to them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.dms_client import probe as dms_probe
from chatbot.features.chat.dms_config_store import DmsConfig, public_dict

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.dms_config_store import DmsConfigStore
    from chatbot.platform.config import Settings

_NOT_CONFIGURED = "unexpected_status"


def _empty_config() -> DmsConfig:
    """What GET returns before an operator has ever saved anything -- an
    "off" config, not a 404, so the admin form always has something to
    render.
    """
    return DmsConfig(
        enabled=False,
        provider_label="",
        base_url="",
        auth_type="",
        extra_header_name="",
        extra_header_value="",
        timeout_seconds=10.0,
        retries=0,
    )


class DmsConfigBody(BaseModel):
    enabled: bool = False
    provider_label: str = ""
    base_url: str = ""
    auth_type: str = ""
    extra_header_name: str = ""
    extra_header_value: str = ""
    timeout_seconds: float = 10.0
    retries: int = 0
    # Write-only: accepted here, never returned by GET. `None` (the default,
    # and what a form re-PUTting unrelated fields sends) means "keep
    # whatever credential is already stored" -- see DmsConfigStore.save().
    credential: str | None = None


def build_dms_admin_router(
    config_store: DmsConfigStore,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/integrations/dms", tags=["dms-admin"])
    manage_integration = require_permission(
        "integration.manage", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("", dependencies=[Depends(manage_integration)])
    async def get_config() -> dict:
        config = await config_store.get()
        return public_dict(config if config is not None else _empty_config())

    @router.put("", dependencies=[Depends(manage_integration)])
    async def put_config(body: DmsConfigBody) -> dict:
        if body.base_url and not body.base_url.startswith("https://"):
            raise HTTPException(status_code=400, detail="base_url must start with https://")
        config = DmsConfig(
            enabled=body.enabled,
            provider_label=body.provider_label,
            base_url=body.base_url,
            auth_type=body.auth_type,
            extra_header_name=body.extra_header_name,
            extra_header_value=body.extra_header_value,
            timeout_seconds=body.timeout_seconds,
            retries=body.retries,
        )
        await config_store.save(config, credential=body.credential)
        return public_dict(config)

    @router.post("/test", dependencies=[Depends(manage_integration)])
    async def test_connection() -> dict:
        config = await config_store.get()
        if config is None or not config.base_url:
            return {
                "status": _NOT_CONFIGURED,
                "message": "DMS/TSP integration is not configured yet.",
            }

        credential = await config_store.get_credential()
        if not credential:
            return {
                "status": _NOT_CONFIGURED,
                "message": "No credential is stored for this integration.",
            }

        async with httpx.AsyncClient() as client:
            result = await dms_probe(config, credential, client)
        return {"status": result.status, "message": result.message}

    return router
