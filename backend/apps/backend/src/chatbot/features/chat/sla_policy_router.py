"""SLA policy admin API — read/write the operator-editable SLA policy store.

Gated behind the `sla.manage` permission via Phase 1's `require_permission`,
matching authz/router.py's pattern exactly (constant-time shared-secret
fallback when RBAC is off, fail-closed permission check when RBAC is on).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.sla_policy_db import SlaPolicyValues

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository
    from chatbot.platform.config import Settings


class SlaPolicyBody(BaseModel):
    response_hours: float | None = None
    resolution_hours: float | None = None
    ack_minutes_by_channel_json: str | None = None
    pic_whatsapp: str | None = None
    engine_enabled: bool | None = None


def _to_dict(values) -> dict:
    return {
        "response_hours": values.response_hours,
        "resolution_hours": values.resolution_hours,
        "ack_minutes_by_channel_json": values.ack_minutes_by_channel_json,
        "pic_whatsapp": values.pic_whatsapp,
        "engine_enabled": values.engine_enabled,
    }


def _empty() -> SlaPolicyValues:
    return SlaPolicyValues()


def build_sla_policy_router(
    repo: SlaPolicyRepository,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/sla-policy", tags=["sla-policy"])
    manage_sla = require_permission(
        "sla.manage", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/default", dependencies=[Depends(manage_sla)])
    async def get_default() -> dict:
        values = await repo.get_tenant_default()
        return _to_dict(values) if values is not None else _to_dict(_empty())

    @router.put("/default", dependencies=[Depends(manage_sla)])
    async def put_default(body: SlaPolicyBody) -> dict:
        values = await repo.upsert_tenant_default(**body.model_dump())
        return _to_dict(values)

    @router.get("/inbox/{inbox_id}", dependencies=[Depends(manage_sla)])
    async def get_inbox(inbox_id: int) -> dict:
        values = await repo.get_for_inbox(inbox_id)
        return _to_dict(values) if values is not None else _to_dict(_empty())

    @router.put("/inbox/{inbox_id}", dependencies=[Depends(manage_sla)])
    async def put_inbox(inbox_id: int, body: SlaPolicyBody) -> dict:
        values = await repo.upsert_for_inbox(inbox_id, **body.model_dump())
        return _to_dict(values)

    return router
