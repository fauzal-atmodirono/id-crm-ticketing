"""Read and write the P3 case fields from the conversation sidebar.

The panel renders from `CASE_FIELDS` rather than from a hand-written form, so
adding a field later is one line in `case_fields.py` and nothing here changes.

Values are stored as Chatwoot conversation custom attributes -- the same place
every other consumer in this system already reads -- so nothing downstream
needs a new source. Writes go through the merge-safe attribute writer: the
custom-attributes endpoint REPLACES the whole object, and a bare POST here
would wipe case_category, recording_url and everything else the conversation
carries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.authz.data_scope import (
    Scope,
    build_scope_dependency,
    conversation_dimensions,
)
from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.case_fields import (
    CASE_FIELDS,
    InvalidCaseField,
    validate,
    validate_dealer_slug,
)

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.pic_store import DealerStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class CaseFieldsBody(BaseModel):
    """Only the fields the operator actually edited.

    A partial write, deliberately: the panel sends what changed, and an absent
    key means "leave it alone" rather than "clear it". Clearing is an explicit
    empty string.
    """

    fields: dict[str, Any] = Field(default_factory=dict)


def build_case_fields_router(
    chatwoot_read: Any,
    chatwoot_merge_attributes: Any,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
    dealer_store: DealerStore | None = None,
) -> APIRouter:
    router = APIRouter(tags=["case-fields"])
    may_view = require_permission(
        "cases.view", repo=authz_repo, validator=validator, settings=settings
    )
    may_edit = require_permission(
        "cases.manage", repo=authz_repo, validator=validator, settings=settings
    )

    def _guard() -> None:
        """404 rather than 403 when the feature is off, so the fork panel
        simply does not render instead of showing a permissions error to
        every agent on a tenant that never enabled it."""
        if not getattr(settings, "case_fields_enabled", False):
            raise HTTPException(status_code=404, detail="Case fields not enabled")

    data_scope = build_scope_dependency(settings, repo=authz_repo, validator=validator)

    async def _read_in_scope(conv_id: str, scope: Scope) -> dict[str, Any] | None:
        """The conversation, or 404 when it is outside the caller's data
        scope (Wave 2 Task 28). 404 rather than 403 so an out-of-scope
        caller cannot probe which case ids exist. Unrestricted callers keep
        today's fail-open read; a scoped caller whose read fails gets 404 --
        an unreadable conversation cannot be shown to be theirs."""
        try:
            conversation = await chatwoot_read(conv_id)
        except Exception:
            _log.warning("case_fields_read_failed", conv_id=conv_id)
            conversation = None
        if not scope.unrestricted and not (
            conversation and scope.permits(conversation_dimensions(conversation))
        ):
            raise HTTPException(status_code=404, detail="Case not found")
        return conversation

    @router.get("/cases/{conv_id}/fields", dependencies=[Depends(may_view)])
    async def get_fields(conv_id: str, scope: Scope = Depends(data_scope)) -> dict[str, Any]:
        """Every field in the spec with its current value, so the panel can
        render from one response without knowing the field list itself."""
        _guard()
        conversation = await _read_in_scope(conv_id, scope)
        attrs = (conversation or {}).get("custom_attributes") or {}
        return {
            "fields": [
                {
                    "name": name,
                    "type": spec.type,
                    "choices": list(spec.choices),
                    "value": attrs.get(name),
                }
                for name, spec in CASE_FIELDS.items()
            ]
        }

    @router.patch("/cases/{conv_id}/fields", dependencies=[Depends(may_edit)])
    async def patch_fields(
        conv_id: str, body: CaseFieldsBody, scope: Scope = Depends(data_scope)
    ) -> dict[str, Any]:
        _guard()
        if not scope.unrestricted:
            await _read_in_scope(conv_id, scope)
        updates: dict[str, Any] = {}
        for name, raw in body.fields.items():
            try:
                if name == "purchased_from_dealer":
                    updates[name] = await validate_dealer_slug(raw, dealer_store)
                else:
                    updates[name] = validate(name, raw)
            except InvalidCaseField as exc:
                # 400 with the validator's own sentence: it was written to be
                # shown to the operator who has to fix it.
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Wave 2 Task 28: a dealer-scoped caller may not re-attribute a case
        # to a dealer outside their scope (nor clear it, which would also
        # move it out of their scope). Same rule as RSA's purchased_from.
        # CASE_FIELDS has no department field, so dealer is the only
        # scope-bearing field writable here.
        if (
            "purchased_from_dealer" in updates
            and "dealer_slugs" in scope.restricted_dimensions
            and not scope.permits({"dealer_slugs": updates["purchased_from_dealer"]})
        ):
            raise HTTPException(
                status_code=403, detail="purchased_from_dealer is outside your data scope"
            )
        if updates:
            await chatwoot_merge_attributes(conv_id, updates)
        return {"conversation_id": conv_id, "updated": sorted(updates), "status": "ok"}

    return router
