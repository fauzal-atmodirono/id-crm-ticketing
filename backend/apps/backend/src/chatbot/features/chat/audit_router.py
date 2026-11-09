"""Cross-ticket audit list/filter API — the existing GET /cases/{id}/audit
route (ChatRouter) is per-case only; this adds a global admin view. Gated
behind the `audit.view` permission via Phase 1's require_permission.

Read-only by design (RFP 2.2.5 tamper-proof history): GET is the only verb and
the store port has no update or delete. Filters: actor, actor_user_id,
event_type, target and a date range."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.ports import AuditLogPort
    from chatbot.platform.config import Settings


def build_audit_router(
    audit: AuditLogPort,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["audit"])
    view_audit = require_permission(
        "audit.view", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/audit", dependencies=[Depends(view_audit)])
    async def list_audit(
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
        event_type: str | None = None,
        actor_user_id: int | None = None,
        target: str | None = None,
    ) -> dict:
        rows = await audit.list_filtered(
            actor=actor,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=limit,
            event_type=event_type,
            actor_user_id=actor_user_id,
            target=target,
        )
        return {"audit": [asdict(r) for r in rows]}

    return router
