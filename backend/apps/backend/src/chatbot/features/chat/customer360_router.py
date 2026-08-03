"""Customer 360 foundational lookup -- searches by phone number (today's
de facto customer identity) or vehicle number, aggregating what's already
in the CRM (contact, cross-channel conversations, RSA incidents). This is
explicitly NOT a DMS integration -- see the design spec for why phone
number is used as a provisional key pending Proton's final decision.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
    from chatbot.features.rsa.rsa_repository import RsaRepositoryPort
    from chatbot.platform.config import Settings

_PHONE_RE = re.compile(r"^\+?[\d\s-]{6,}$")


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _pick_best_contact(contacts: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Prefer an exact phone-number match (digits-only comparison, so
    formatting differences like spaces/dashes/leading '+' don't matter);
    fall back to the first search result when nothing matches exactly, since
    /contacts/search already did the relevance ranking server-side."""
    if not contacts:
        return None
    wanted = _digits(query)
    if wanted:
        for contact in contacts:
            if _digits(contact.get("phone_number")) == wanted:
                return contact
    return contacts[0]


def _rsa_incident_dict(row: Any) -> dict[str, Any]:
    """Mirror rsa_router.py's ``_incident_dict`` serialization so incidents
    look the same wherever they're returned from this backend."""
    return {
        "id": row.id,
        "incident_date": row.incident_date,
        "vehicle_no": row.vehicle_no,
        "vehicle_model": row.vehicle_model,
        "cause": row.cause,
        "purchased_from": row.purchased_from,
        "breakdown_location": row.breakdown_location,
        "arrived_location": row.arrived_location,
        "customer_called_in_time": row.customer_called_in_time,
        "towing_assigned_time": row.towing_assigned_time,
        "time_arrived_breakdown_area": row.time_arrived_breakdown_area,
        "time_arrived_outlet": row.time_arrived_outlet,
        "total_km": row.total_km,
        "late_reason": row.late_reason,
        "remarks": row.remarks,
        "created_by": row.created_by,
    }


def build_customer360_router(
    chatwoot: ChatwootAdapter,
    rsa_repo: RsaRepositoryPort,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/customer360", tags=["customer360"])
    view_360 = require_permission(
        "customer360.view", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/search", dependencies=[Depends(view_360)])
    async def search(q: str = Query(min_length=2)) -> dict[str, Any]:
        contact: dict | None = None
        conversations: list[dict] = []
        rsa_incidents: list[dict] = []

        if _PHONE_RE.match(q.strip()):
            # Phone-number search path: /contacts/search returns a
            # relevance-ranked list, not a single best match, so pick the
            # exact digits-only match if one exists (see _pick_best_contact).
            # Then pull that contact's full cross-channel conversation
            # history (any status, any inbox -- this is a read-only 360
            # view, not the active-conversation reuse check other adapter
            # callers do).
            contacts = await chatwoot.search_contacts(q.strip())
            contact = _pick_best_contact(contacts, q)
            if contact is not None and contact.get("id") is not None:
                conversations = await chatwoot.list_contact_conversations(int(contact["id"]))
        else:
            # Vehicle-number search path: RSA incidents are matched on their
            # actual vehicle_no field (case-insensitive substring, since
            # staff may search a partial plate). Conversations don't carry a
            # vehicle number at all -- the only vehicle-related field they
            # expose is the vehicle_model custom attribute -- so the
            # conversation side of this branch is a best-effort substring
            # match against vehicle_model, not a true vehicle-number lookup.
            needle = q.strip().lower()
            incidents = await rsa_repo.list_incidents()
            rsa_incidents = [
                _rsa_incident_dict(row)
                for row in incidents
                if needle in (row.vehicle_no or "").lower()
            ]
            all_conversations = await chatwoot.list_conversations()
            conversations = [
                conv
                for conv in all_conversations
                if needle in ((conv.get("custom_attributes") or {}).get("vehicle_model") or "").lower()
            ]

        return {"contact": contact, "conversations": conversations, "rsa_incidents": rsa_incidents}

    return router
