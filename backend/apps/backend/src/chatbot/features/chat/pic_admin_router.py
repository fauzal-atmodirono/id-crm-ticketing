"""Escalation Routing admin API -- CRUD for PIC (department -> contact) and
dealer (slug -> email) mappings, backing the Escalation Routing admin page.
Gated behind the `escalation.manage` permission via Phase 1's
`require_permission`, matching sla_policy_router.py's pattern exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.pic_store import DealerStore, PicStore
    from chatbot.platform.config import Settings


class PicUpsertBody(BaseModel):
    pic_name: str = Field(min_length=1)
    pic_email: str = Field(min_length=1)
    pic_whatsapp: str = ""
    cc_emails: list[str] = Field(default_factory=list)


class DealerUpsertBody(BaseModel):
    """`emails` is the group's member list. `email` is accepted for
    compatibility with the pre-groups UI and is folded into `emails`."""

    emails: list[str] = Field(default_factory=list)
    email: str | None = None

    def members(self) -> list[str]:
        merged = [e for e in self.emails if e]
        if self.email and self.email not in merged:
            merged.append(self.email)
        return merged


def build_pic_admin_router(
    pic_store: PicStore,
    dealer_store: DealerStore,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/escalation", tags=["escalation-admin"])
    manage_escalation = require_permission(
        "escalation.manage", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/pics", dependencies=[Depends(manage_escalation)])
    async def list_pics() -> dict:
        records = await pic_store.list_all()
        return {"pics": [r.__dict__ for r in records]}

    @router.put("/pics/{department}", dependencies=[Depends(manage_escalation)])
    async def upsert_pic(department: str, body: PicUpsertBody) -> dict:
        await pic_store.set(
            department,
            pic_name=body.pic_name,
            pic_email=body.pic_email,
            pic_whatsapp=body.pic_whatsapp,
            cc_emails=body.cc_emails,
        )
        return {"department": department, "status": "ok"}

    @router.delete("/pics/{department}", dependencies=[Depends(manage_escalation)])
    async def delete_pic(department: str) -> dict:
        await pic_store.delete(department)
        return {"department": department, "status": "ok"}

    @router.get("/dealers", dependencies=[Depends(manage_escalation)])
    async def list_dealers() -> dict:
        records = await dealer_store.list_all()
        return {"dealers": [r.__dict__ for r in records]}

    @router.put("/dealers/{dealer}", dependencies=[Depends(manage_escalation)])
    async def upsert_dealer(dealer: str, body: DealerUpsertBody) -> dict:
        await dealer_store.set(dealer, emails=body.members())
        return {"dealer": dealer, "status": "ok"}

    @router.delete("/dealers/{dealer}", dependencies=[Depends(manage_escalation)])
    async def delete_dealer(dealer: str) -> dict:
        await dealer_store.delete(dealer)
        return {"dealer": dealer, "status": "ok"}

    return router
