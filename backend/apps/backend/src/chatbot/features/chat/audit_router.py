"""Cross-ticket audit list/filter API — the existing GET /cases/{id}/audit
route (ChatRouter) is per-case only; this adds a global admin view. Gated
behind the `audit.view` permission via Phase 1's require_permission."""

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
    ) -> dict:
        rows = await audit.list_filtered(actor=actor, from_ts=from_ts, to_ts=to_ts, limit=limit)
        return {"audit": [asdict(r) for r in rows]}

    return router
