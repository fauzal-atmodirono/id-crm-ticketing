"""Customer 360 foundational lookup -- searches by phone number (today's
de facto customer identity) or vehicle number, aggregating what's already
in the CRM (contact, cross-channel conversations, RSA incidents), plus an
optional DMS/TSP block (Package F) when that integration is configured and
enabled. This is explicitly NOT a DMS integration -- see the design spec for
why phone number is used as a provisional key pending Proton's final
decision.

The `dms` block is additive and fails open by construction:
  - `dms_config_store` is `None` (how `main.py` builds this router today,
    and every existing test that doesn't pass it) -- the block is never even
    computed. The response is byte-identical to before this package
    existed.
  - `dms_config_store` is wired but the stored config is absent or
    `enabled=False` -- same result: no `dms` key in the response at all,
    not `null`. "Unchanged" means unchanged, not "unchanged plus an extra
    key holding an empty value".
  - Enabled, but nobody wired a concrete `dms_client` -- Phase 1 ships no
    real adapter (see the package design doc); a caller has to deliberately
    construct `MockDmsClient` and pass it in to get anything but this. The
    block is present with `status: "unreachable"`. This is deliberate: an
    operator who flips "enabled" before a real adapter exists must see
    "not connected", never a silent "no records found" that could be
    mistaken for a working integration (demo-feedback item #26).
  - Enabled, a client is wired, and the lookup raises or runs past its time
    budget -- also `status: "unreachable"`, never a 500 and never partial
    data presented as complete.
  - Enabled, a client is wired, and it succeeds -- `status: "ok"`, with
    `customer`/`vehicles`/`service_history` built from
    `DmsCustomer`/`DmsVehicle`/`DmsServiceRecord` -- our field names, never
    a vendor's. `mock: true` is set automatically whenever the wired client
    is `MockDmsClient`, so a UI rendering this block always has an explicit
    signal that the data is a demo, not live.

Note `status: "ok"` with empty `customer`/`vehicles`/`service_history` is a
different, equally valid outcome: the DMS was reached and genuinely has
nothing on file for this customer. That is the property this module is
tested hardest on -- "no records" and "couldn't reach the DMS" must never
collapse into the same shape.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Query

from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.dms_client import MockDmsClient

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
    from chatbot.features.chat.dms_client import (
        DmsClient,
        DmsCustomer,
        DmsServiceRecord,
        DmsVehicle,
    )
    from chatbot.features.chat.dms_config_store import DmsConfigStore
    from chatbot.features.rsa.rsa_repository import RsaRepositoryPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_PHONE_RE = re.compile(r"^\+?[\d\s-]{6,}$")

# A Customer 360 lookup is interactive -- an operator is waiting on this page
# to load. The whole DMS side-trip (find_customer + list_vehicles + however
# many list_service_history calls it fans out to) is bounded to ONE timeout
# window via asyncio.wait_for, not one window per call: three-plus sequential
# calls at the operator-configured timeout could otherwise make the page hang
# for multiples of it. The floor guards the degenerate case where a stored
# config has a near-zero/zero timeout_seconds -- without it, a misconfigured
# budget would make even an instant client always read as "unreachable".
_DMS_BUDGET_FLOOR_SECONDS = 1.0

# Vehicles are fetched for service history concurrently (asyncio.gather), not
# one-by-one, and capped -- a customer with many vehicles on file must not
# turn into an unbounded fan-out of concurrent DMS calls on every lookup.
_MAX_VEHICLES_FOR_HISTORY = 5


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


def _dms_customer_dict(customer: DmsCustomer) -> dict[str, Any]:
    return {"ref": customer.ref, "name": customer.name, "phone": customer.phone}


def _dms_vehicle_dict(vehicle: DmsVehicle) -> dict[str, Any]:
    return {
        "vehicle_no": vehicle.vehicle_no,
        "model": vehicle.model,
        "purchased_from": vehicle.purchased_from,
    }


def _dms_service_record_dict(record: DmsServiceRecord) -> dict[str, Any]:
    return {"date": record.date, "description": record.description, "dealer": record.dealer}


def _unreachable_dms_block(*, mock: bool) -> dict[str, Any]:
    return {
        "status": "unreachable",
        "mock": mock,
        "customer": None,
        "vehicles": [],
        "service_history": [],
    }


async def _fetch_dms_records(
    client: DmsClient, *, phone: str | None, vehicle_no: str | None
) -> tuple[DmsCustomer | None, list[DmsVehicle], list[DmsServiceRecord]]:
    customer = await client.find_customer(phone=phone, vehicle_no=vehicle_no)
    if customer is None:
        return None, [], []
    vehicles = await client.list_vehicles(customer.ref)
    # Cap the fan-out, don't just cap what's rendered -- a customer with many
    # vehicles on file must not turn into many concurrent DMS calls.
    bounded_vehicles = vehicles[:_MAX_VEHICLES_FOR_HISTORY]
    histories = await asyncio.gather(
        *(client.list_service_history(vehicle.vehicle_no) for vehicle in bounded_vehicles)
    )
    service_history = [record for records in histories for record in records]
    return customer, vehicles, service_history


async def _build_dms_block(
    dms_config_store: DmsConfigStore | None,
    dms_client: DmsClient | None,
    *,
    phone: str | None,
    vehicle_no: str | None,
) -> dict[str, Any] | None:
    """The optional `dms` block, or `None` when it must be entirely absent
    from the response -- see the module docstring for the full state table.
    """
    if dms_config_store is None:
        return None

    try:
        config = await dms_config_store.get()
    except Exception as exc:
        _log.warning("dms_customer360_config_lookup_failed", error_type=type(exc).__name__)
        return None

    if config is None or not config.enabled:
        return None

    if dms_client is None:
        # Enabled, but nothing is actually wired to serve it. This must read
        # as "not connected", never as a silent "no records" -- exactly the
        # shell-mistaken-for-a-working-integration failure this package
        # exists to avoid.
        return _unreachable_dms_block(mock=False)

    is_mock = isinstance(dms_client, MockDmsClient)
    budget_seconds = max(config.timeout_seconds, _DMS_BUDGET_FLOOR_SECONDS)
    try:
        customer, vehicles, service_history = await asyncio.wait_for(
            _fetch_dms_records(dms_client, phone=phone, vehicle_no=vehicle_no),
            timeout=budget_seconds,
        )
    except Exception as exc:
        _log.warning("dms_customer360_lookup_failed", error_type=type(exc).__name__)
        return _unreachable_dms_block(mock=is_mock)

    return {
        "status": "ok",
        "mock": is_mock,
        "customer": _dms_customer_dict(customer) if customer is not None else None,
        "vehicles": [_dms_vehicle_dict(v) for v in vehicles],
        "service_history": [_dms_service_record_dict(r) for r in service_history],
    }


def build_customer360_router(
    chatwoot: ChatwootAdapter,
    rsa_repo: RsaRepositoryPort,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
    dms_config_store: DmsConfigStore | None = None,
    dms_client: DmsClient | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/admin/customer360", tags=["customer360"])
    view_360 = require_permission(
        "customer360.view", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/search", dependencies=[Depends(view_360)])
    async def search(q: str = Query(min_length=2)) -> dict[str, Any]:
        contact: dict[str, Any] | None = None
        conversations: list[dict[str, Any]] = []
        rsa_incidents: list[dict[str, Any]] = []

        q_stripped = q.strip()
        is_phone = bool(_PHONE_RE.match(q_stripped))
        phone = q_stripped if is_phone else None
        vehicle_no = q_stripped if not is_phone else None

        # Kicked off now, awaited at the end: the DMS side-trip runs
        # concurrently with the CRM lookups below rather than after them, so
        # it adds at most max(dms_time, crm_time) to the page, not the sum
        # of both.
        dms_task = asyncio.ensure_future(
            _build_dms_block(dms_config_store, dms_client, phone=phone, vehicle_no=vehicle_no)
        )

        if is_phone:
            # Phone-number search path: /contacts/search returns a
            # relevance-ranked list, not a single best match, so pick the
            # exact digits-only match if one exists (see _pick_best_contact).
            # Then pull that contact's full cross-channel conversation
            # history (any status, any inbox -- this is a read-only 360
            # view, not the active-conversation reuse check other adapter
            # callers do).
            contacts = await chatwoot.search_contacts(q_stripped)
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
            needle = q_stripped.lower()
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
                if needle
                in ((conv.get("custom_attributes") or {}).get("vehicle_model") or "").lower()
            ]

        result: dict[str, Any] = {
            "contact": contact,
            "conversations": conversations,
            "rsa_incidents": rsa_incidents,
        }
        dms_block = await dms_task
        if dms_block is not None:
            result["dms"] = dms_block
        return result

    return router
