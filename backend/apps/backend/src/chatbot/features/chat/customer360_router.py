"""Customer 360 foundational lookup -- searches by phone number (today's
de facto customer identity) or vehicle number, aggregating what's already
in the CRM (contact, cross-channel conversations, RSA incidents), plus an
optional DMS/TSP block (Package F) when that integration is configured and
enabled. This is explicitly NOT a DMS integration -- see the design spec for
why phone number is used as a provisional key pending Proton's final
decision.

Both DMS params are optional and default to `None`. `main.py` DOES pass
them (since c85fa89): `dms_config_store` is always wired, `dms_client` only
when `DMS_MOCK_CLIENT_ENABLED` is on. So the wired configuration -- not the
`None` one -- is what production runs, and the store-based tests below are
what cover it. The `None` case remains reachable by any other caller and by
the tests that omit the params.

The `dms` block is additive and fails open by construction:
  - `dms_config_store` is `None` (any caller that omits it, and every
    existing test that doesn't pass it) -- the block is never even
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
    mistaken for a working integration. (This was previously attributed
    to demo-feedback item #26; that item is actually about customer-sent
    video. The principle stands on its own -- no design decision here
    rested on the citation -- but the attribution was wrong.)
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

from chatbot.features.authz.data_scope import (
    Scope,
    apply_scope,
    build_scope_dependency,
    conversation_dimensions,
)
from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.dms_client import MockDmsClient
from chatbot.features.chat.dms_config_store import MAX_TIMEOUT_SECONDS
from chatbot.features.privacy.access import PiiPolicy, build_pii_dependency

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

# ...and the ceiling guards the opposite degenerate case. `DmsConfigBody` now
# rejects an out-of-range timeout_seconds on write, but that constraint post-
# dates the field: a document saved before it existed (or by anything that
# doesn't go through the admin API) can still hold 600, and the read path is
# where a human actually waits. Clamping here rather than trusting the write
# validator is what makes "an operator cannot hang this page" true of the
# data as it exists, not merely of new writes.
_DMS_BUDGET_CEILING_SECONDS = MAX_TIMEOUT_SECONDS

# Vehicles are fetched for service history concurrently (asyncio.gather), not
# one-by-one, and capped -- a customer with many vehicles on file must not
# turn into an unbounded fan-out of concurrent DMS calls on every lookup.
_MAX_VEHICLES_FOR_HISTORY = 5


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _plate_key(value: str | None) -> str:
    """Normalised plate for comparison: upper-case alphanumerics only, so
    ``"bmt 5518"``, ``"BMT-5518"`` and ``"BMT5518"`` are the same plate."""
    return re.sub(r"[^0-9A-Z]", "", (value or "").upper())


def _plate_variants(query: str) -> list[str]:
    """Spellings of one plate to OR together in Chatwoot's ``contains``
    filter: compact (``BMT5518``) and letter/digit-spaced (``BMT 5518``),
    the two ways operators type plates into the contact attribute."""
    compact = _plate_key(query)
    spaced = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", compact)
    dashed = spaced.replace(" ", "-")
    return list(dict.fromkeys(v for v in (compact, spaced, dashed) if v))


# Plate-shaped: 2-3 letters, digits, optional trailing letter (BMT5518,
# "BMT 5518", BMT-5518, W1234A). One-letter model names like "X50"/"S70" and
# anything with punctuation ("e.MAS 7") are deliberately NOT plate-like.
_PLATE_LIKE_RE = re.compile(r"^[A-Za-z]{2,3}[\s-]?\d{1,4}[A-Za-z]?$")

# Total budget for the whole Chatwoot side of a vehicle search (contacts
# filter + per-contact conversations). Past it we return what we have.
_VEHICLE_CRM_BUDGET_SECONDS = 2.5


_MIN_PHONE_SUFFIX_DIGITS = 8


def _phones_match(a: str | None, b: str | None) -> bool:
    """Digits-only phone equality, tolerant of a country-code/trunk-prefix
    difference (``0123456789`` vs ``+60123456789``): one must be a suffix of
    the other, with at least 8 digits shared so short numbers can't collide."""
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    short, long_ = sorted((da, db), key=len)
    short = short.lstrip("0")
    return len(short) >= _MIN_PHONE_SUFFIX_DIGITS and long_.endswith(short)


# Fallback conversation scan (vehicle_model substring) is the slow path -- it
# pages the Chatwoot conversation list. Bounded so a plate that matched no
# contact can't stall the page.
_MODEL_SCAN_BUDGET_SECONDS = 2.0
_MAX_PLATE_CONTACTS = 5


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
        "customer_phone": getattr(row, "customer_phone", None),
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
    # return_exceptions=True is deliberate: the default (False) propagates
    # the first exception the instant it happens and leaves the OTHER
    # in-flight list_service_history calls running, uncancelled -- outside
    # this function's return, outside the caller's asyncio.wait_for window,
    # free to emit "Task exception was never retrieved" noise and hold
    # resources with no timeout of their own. return_exceptions=True makes
    # gather wait for every call to actually finish (success or failure)
    # before this function looks at any of them -- no orphans, whatever the
    # outcome.
    results: list[list[DmsServiceRecord] | BaseException] = await asyncio.gather(
        *(client.list_service_history(vehicle.vehicle_no) for vehicle in bounded_vehicles),
        return_exceptions=True,
    )
    histories: list[list[DmsServiceRecord]] = []
    for result in results:
        if isinstance(result, BaseException):
            # A single failed vehicle in the fan-out must not be presented
            # as a customer's complete history -- degrade the whole block,
            # same as any other DMS failure (caught by the caller's
            # try/except around asyncio.wait_for).
            raise result
        histories.append(result)
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
    budget_seconds = min(
        max(config.timeout_seconds, _DMS_BUDGET_FLOOR_SECONDS),
        _DMS_BUDGET_CEILING_SECONDS,
    )
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


async def _plate_lookup(
    chatwoot: ChatwootAdapter,
    query: str,
    needle: str,
    plate_contacts: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
) -> None:
    """Fill ``plate_contacts``/``conversations`` in place (so whatever
    finished survives a wait_for timeout): one contacts/filter call, then the
    matched contacts' conversations fetched concurrently."""
    found = await chatwoot.find_contacts_by_vehicle_no(_plate_variants(query))
    found = [c for c in found if c.get("id") is not None]
    # exact plate first, so `contact` is the real owner, not a substring
    # neighbour
    found.sort(
        key=lambda c: _plate_key((c.get("custom_attributes") or {}).get("vehicle_no")) != needle
    )
    plate_contacts.extend(found[:_MAX_PLATE_CONTACTS])
    per_contact = await asyncio.gather(
        *(chatwoot.list_contact_conversations(int(c["id"])) for c in plate_contacts)
    )
    seen: set[Any] = set()
    for convs in per_contact:
        for conv in convs:
            if conv.get("id") not in seen:
                seen.add(conv.get("id"))
                conversations.append(conv)


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

    data_scope = build_scope_dependency(settings, repo=authz_repo, validator=validator)
    # Wave 2 Task 29: the whole result (contact, conversations, RSA incidents,
    # DMS block) is masked for a caller without pii.view. The search itself
    # still runs on the raw query -- the caller typed it, so it reveals nothing.
    pii_policy = build_pii_dependency(settings, repo=authz_repo, validator=validator)

    @router.get("/search", dependencies=[Depends(view_360)])
    async def search(
        q: str = Query(min_length=2),
        scope: Scope = Depends(data_scope),
        pii: PiiPolicy = Depends(pii_policy),
    ) -> dict[str, Any]:
        if scope.deny_all:
            # Unreadable scope (data_scope.py rule 7): nothing at all -- not
            # even the contact or the DMS block, and no lookups are made.
            return {"contact": None, "conversations": [], "rsa_incidents": []}
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
            incidents = await rsa_repo.list_incidents()
            rsa_incidents = [
                _rsa_incident_dict(row)
                for row in incidents
                if _phones_match(q_stripped, getattr(row, "customer_phone", None))
            ]
        else:
            # Vehicle-number search path. RSA incidents match on the
            # normalised plate (spaces/case/punctuation ignored, substring so
            # staff may search a partial plate). Conversations are found the
            # way the Cases list "Car Plate" column reads them: the contact's
            # ``vehicle_no`` custom attribute, via ONE Chatwoot contacts/filter
            # call plus one conversations call per matched contact -- not a
            # scan of every conversation, which took 4-5 s.
            needle = _plate_key(q_stripped)
            incidents = await rsa_repo.list_incidents()
            rsa_incidents = [
                _rsa_incident_dict(row)
                for row in incidents
                if needle and needle in _plate_key(row.vehicle_no)
            ]
            plate_contacts: list[dict[str, Any]] = []
            if needle:
                try:
                    await asyncio.wait_for(
                        _plate_lookup(chatwoot, q_stripped, needle, plate_contacts, conversations),
                        timeout=_VEHICLE_CRM_BUDGET_SECONDS,
                    )
                except Exception as exc:
                    _log.warning("customer360_plate_lookup_failed", error_type=type(exc).__name__)
            if plate_contacts:
                contact = plate_contacts[0]
            elif not _PLATE_LIKE_RE.match(q_stripped):
                # Legacy best-effort for model-like queries ("camry", "X50"):
                # the free-text vehicle_model attribute, time-boxed because it
                # pages the inbox. Skipped for plate-shaped queries -- a plate
                # that matched no contact has nothing to find there.
                lowered = q_stripped.lower()
                try:
                    all_conversations = await asyncio.wait_for(
                        chatwoot.list_conversations(), timeout=_MODEL_SCAN_BUDGET_SECONDS
                    )
                except Exception as exc:
                    _log.warning("customer360_model_scan_failed", error_type=type(exc).__name__)
                    all_conversations = []
                conversations = [
                    conv
                    for conv in all_conversations
                    if lowered
                    in ((conv.get("custom_attributes") or {}).get("vehicle_model") or "").lower()
                ]

        # Wave 2 Task 28: this backend reads Chatwoot with its own admin
        # token, so Chatwoot inbox membership does NOT confine what it
        # returns -- the data scope has to. The contact itself stays visible
        # (Chatwoot contacts are account-wide for every agent anyway); its
        # conversations and RSA incidents are narrowed. An RSA incident only
        # carries a dealer (`purchased_from`), so under an inbox/team/
        # department scope it is hidden (data_scope.py rule 5).
        conversations = apply_scope(conversations, scope, conversation_dimensions)
        rsa_incidents = apply_scope(
            rsa_incidents, scope, lambda i: {"dealer_slugs": i.get("purchased_from")}
        )
        result: dict[str, Any] = {
            "contact": contact,
            "conversations": conversations,
            "rsa_incidents": rsa_incidents,
        }
        dms_block = await dms_task
        if dms_block is not None:
            result["dms"] = dms_block
        return pii.apply(result)

    return router
