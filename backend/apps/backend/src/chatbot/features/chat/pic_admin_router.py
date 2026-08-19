"""Escalation Routing admin API -- CRUD for PIC (department -> contact) and
dealer (slug -> email) mappings, backing the Escalation Routing admin page.
Gated behind the `escalation.manage` permission via Phase 1's
`require_permission`, matching sla_policy_router.py's pattern exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.pic_store import DealerStore, PicStore, ProtonNetStore
    from chatbot.platform.config import Settings


class PicUpsertBody(BaseModel):
    pic_name: str = Field(min_length=1)
    pic_email: str = Field(min_length=1)
    pic_whatsapp: str = ""
    cc_emails: list[str] = Field(default_factory=list)
    # P2 task 7: who tier-2 wakes up when the first alert went unanswered.
    # Optional -- an unset manager falls back to the PIC themselves.
    escalation_manager_email: str = ""
    escalation_manager_whatsapp: str = ""


class DealerUpsertBody(BaseModel):
    """`emails` is the group's member list. `email` is accepted for
    compatibility with the pre-groups UI and is folded into `emails`.

    A dealer row must always name at least one member: before groups, `email`
    was `Field(min_length=1)`, so a body naming nobody was already a 422 and
    could never wipe a stored dealer's recipients. `DealerStore.set` does a
    bare Firestore `.set()` (no `merge=True`), so without this validator an
    empty-looking PUT (`{}` or `{"emails": []}`) would 200 and silently
    replace a working group's member list with an empty one.
    """

    emails: list[str] = Field(default_factory=list)
    email: str | None = None
    cc_emails: list[str] | None = None
    # The escalation ladder's named roles. A dealer configured purely by role
    # is valid -- that is the shape the ladder wants -- so the "name at least
    # somebody" rule below is satisfied by either the group or the roles.
    contacts: dict[str, str] | None = None
    region: str = ""

    def members(self) -> list[str]:
        merged = [e for e in self.emails if e]
        if self.email and self.email not in merged:
            merged.append(self.email)
        return merged

    def named_contacts(self) -> dict[str, str]:
        return {k: v for k, v in (self.contacts or {}).items() if v}

    @model_validator(mode="after")
    def _require_at_least_one_recipient(self) -> DealerUpsertBody:
        if not self.members() and not self.named_contacts():
            raise ValueError(
                "at least one email (in `emails`/`email`) or one named contact is required"
            )
        return self


class ProtonNetUpsertBody(BaseModel):
    """PRO-NET's own contacts for one region. Both roles optional: a region
    with only an HOD configured is a normal intermediate state, and the
    ladder simply CCs whoever is filled in."""

    area_regional_mgr: str = ""
    hod: str = ""


def build_pic_admin_router(
    pic_store: PicStore,
    dealer_store: DealerStore,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
    pronet_store: ProtonNetStore | None = None,
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
            escalation_manager_email=body.escalation_manager_email,
            escalation_manager_whatsapp=body.escalation_manager_whatsapp,
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
        await dealer_store.set(
            dealer,
            emails=body.members(),
            cc_emails=body.cc_emails,
            contacts=body.named_contacts() if body.contacts is not None else None,
            region=body.region,
        )
        return {"dealer": dealer, "status": "ok"}

    @router.delete("/dealers/{dealer}", dependencies=[Depends(manage_escalation)])
    async def delete_dealer(dealer: str) -> dict:
        await dealer_store.delete(dealer)
        return {"dealer": dealer, "status": "ok"}

    # --- PRO-NET regional contacts ------------------------------------------
    # Their own resource rather than fields on a dealer: the same Area/
    # Regional Manager and HOD are CC'd on every dealer in their region, so
    # one job change should edit one row.
    if pronet_store is not None:

        @router.get("/pronet", dependencies=[Depends(manage_escalation)])
        async def list_pronet() -> dict:
            records = await pronet_store.list_all()
            return {"regions": [r.__dict__ for r in records]}

        @router.put("/pronet/{region}", dependencies=[Depends(manage_escalation)])
        async def upsert_pronet(region: str, body: ProtonNetUpsertBody) -> dict:
            await pronet_store.set(
                region, area_regional_mgr=body.area_regional_mgr, hod=body.hod
            )
            return {"region": region, "status": "ok"}

        @router.delete("/pronet/{region}", dependencies=[Depends(manage_escalation)])
        async def delete_pronet(region: str) -> dict:
            await pronet_store.delete(region)
            return {"region": region, "status": "ok"}

    return router
