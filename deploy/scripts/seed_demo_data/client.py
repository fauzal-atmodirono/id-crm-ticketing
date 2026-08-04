"""Thin async I/O layer over the Chatwoot Application API and the backend's
`/rsa/incidents` endpoint (Package D's demo-data seeder).

Every `create_*` stamps a batch marker so `purge()` can find and delete
exactly what `seed` created and nothing else:

- Chatwoot contacts/conversations get `custom_attributes.demo_seed =
  batch_id`, matched by `selectable_for_purge` (pure, verbatim from the task
  brief — this is the tested surface).
- RSA incidents have no `custom_attributes` column (see `rsa_db.py`'s
  `RsaIncident` model) and a server-assigned id, so their marker instead
  lives in `created_by == f"demo-seed:{batch_id}"` (already encoded there by
  `generator.py::_make_rsa_incident`), matched by `selectable_rsa_for_purge`
  — a second pure selector with the same strictness.

`create_*`/`purge` do real network I/O against a live tenant and are
deliberately kept thin enough to review by inspection rather than exercised
by a mock HTTP harness — that's exactly why the two selectors above are
pinned as the tested surface instead.

This is a manual operator script, not a background task in `agent/app`: it
does NOT follow that codebase's "never raise for expected failures" rule.
Here, an HTTP failure should stop the run loudly (`raise_for_status()`) so
an operator sees it immediately, rather than silently skipping a write
against a tenant a client will see.

**Bot-safety invariant:** seeded conversations are created `open` or
`resolved` — NEVER `pending`. `agent/app/services/orchestrator.py`'s
agent-bot only acts on an incoming customer message on a `pending`
conversation; a seeded `pending` conversation on a bot-enabled inbox could
fire up to ~100 AI replies and escalation emails against a live tenant. See
`_safe_status`.

Endpoint shapes (headers, path prefixes, payload keys, response envelopes)
follow `agent/app/clients/chatwoot.py` and
`backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`, the
two existing call sites for this API in this repo — read those before
changing this file. `/conversations/{id}` and `/contacts/{id}` DELETE and
`/contacts/search` pagination are not exercised anywhere else in this repo,
so they are inferred from Chatwoot's own REST conventions rather than
confirmed against a live tenant; Task 4's `default`-tenant rehearsal is what
actually proves them.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from generator import DemoCase, DemoContact

# Delay between API calls. A burst of ~1,500 requests at a tenant's Rails
# app in one go is a self-inflicted outage (plan's Global Constraints).
RATE_LIMIT_DELAY_SECONDS = 0.3

# Chatwoot conversation statuses the agent-bot ignores (see module
# docstring). generator.py draws DemoCase.status from
# {"resolved", "open", "pending"}; "pending" is remapped in _safe_status.
_BOT_SAFE_STATUSES = {"open", "resolved"}

# Upper bound on /contacts/search pages purge() will walk. A demo batch is
# ~100 contacts; Chatwoot's default page size is far smaller, so this is
# generous headroom without risking an unbounded loop against a live tenant.
_MAX_SEARCH_PAGES = 50


@dataclass(frozen=True)
class TenantConfig:
    """Everything this module needs to reach one tenant.

    Task 3's CLI builds this from the tenant's env file
    (`deploy/tenants/<tenant>.env`), reusing the exact variable names
    `agent/app/config.py` / `deploy/tenants/example.env` already document:

    - `chatwoot_base_url` — the tenant's Chatwoot origin (internal or
      public; the Application API is reachable on either).
    - `chatwoot_api_access_token` — `CHATWOOT_API_TOKEN` (an agent token,
      sent as the `api_access_token` header — see `ChatwootClient`).
    - `chatwoot_account_id` — `CHATWOOT_ACCOUNT_ID`.
    - `chatwoot_inbox_id` — an API-channel inbox id to create demo contacts
      and conversations in. Not currently in `example.env`; Task 3 needs to
      either add a `CHATWOOT_DEMO_INBOX_ID` var or look one up from
      `GET /inboxes` before seeding.
    - `backend_base_url` — `PROTON_BACKEND_URL` (internal, docker-network
      only) if the CLI runs from the VM, else `PROTON_BACKEND_PUBLIC_URL`
      if it runs from an operator's machine. Only `/rsa/incidents` is used.
    - `backend_api_key` — `PROTON_BACKEND_KEY` or `FAQ_ADMIN_API_KEY`;
      `rsa_router.py::_authorize` accepts either.
    """

    chatwoot_base_url: str
    chatwoot_api_access_token: str
    chatwoot_account_id: int
    chatwoot_inbox_id: int
    backend_base_url: str
    backend_api_key: str


@dataclass
class PurgeReport:
    """What a human needs after a purge: counts deleted per object type,
    plus anything skipped — so "these still need manual attention" is
    visible instead of silently swallowed."""

    contacts_deleted: int = 0
    conversations_deleted: int = 0
    rsa_incidents_deleted: int = 0
    skipped: list[str] = field(default_factory=list)


_config: TenantConfig | None = None
_chatwoot: httpx.AsyncClient | None = None
_backend: httpx.AsyncClient | None = None


def configure(config: TenantConfig) -> None:
    """Point this module at one tenant. Must be called once before any
    other function here. Re-callable — a fresh call replaces the target
    tenant and clients, so a REPL session can retarget without a second
    module import; production use (Task 3's CLI) only ever calls it once
    per process, right after parsing `--tenant`."""
    global _config, _chatwoot, _backend
    _config = config
    _chatwoot = httpx.AsyncClient(
        base_url=config.chatwoot_base_url,
        headers={"api_access_token": config.chatwoot_api_access_token},
        timeout=30.0,
    )
    _backend = httpx.AsyncClient(
        base_url=config.backend_base_url,
        headers={"x-api-key": config.backend_api_key},
        timeout=30.0,
    )


async def aclose() -> None:
    """Release the HTTP clients `configure()` opened. Task 3's CLI should
    call this once at the end of a run (success or failure)."""
    if _chatwoot is not None:
        await _chatwoot.aclose()
    if _backend is not None:
        await _backend.aclose()


def _require_config() -> TenantConfig:
    if _config is None or _chatwoot is None or _backend is None:
        raise RuntimeError("client.configure(TenantConfig(...)) must be called before use")
    return _config


def _account_path(path: str) -> str:
    return f"/api/v1/accounts/{_require_config().chatwoot_account_id}{path}"


async def _throttle() -> None:
    await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)


def _payload_list(data: Any) -> list[dict]:
    """Normalize a Chatwoot list response to a list of dicts. The account
    API wraps results as `{"payload": [...]}`; some endpoints return a bare
    array. Tolerate both — same normalization
    `backend/.../adapters/chatwoot.py::_conversations_from` uses."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        payload = data.get("payload")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    return []


def _slugify(value: str) -> str:
    """Lowercase, Chatwoot-label-safe slug for the `division_<slug>` /
    `dealer_<slug>` label convention `agent/app/services/sync.py`
    (`_DEALER_LABEL`) and `backend/.../metrics/mapping.py` (`_DIVISION_TAG`,
    `_DEALER_TAG`) already read back off conversation labels."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _safe_status(status: str) -> str:
    """Map a DemoCase's status to one the agent-bot ignores. See the
    module's bot-safety invariant docstring — this is the single point
    seeded conversations are guaranteed never to end up `pending`."""
    return status if status in _BOT_SAFE_STATUSES else "open"


# --- pure selectors (the tested surface) ------------------------------------


def selectable_for_purge(objects: list[dict], batch_id: str) -> list[dict]:
    """Objects carrying exactly this batch marker, and nothing else.

    Deliberately strict: an empty batch_id selects nothing, and a missing or
    null marker is never a match. Purge runs against a live tenant, so the
    failure mode of deleting too little is recoverable and deleting too much
    is not.
    """
    if not batch_id:
        return []
    return [o for o in objects if (o.get("custom_attributes") or {}).get("demo_seed") == batch_id]


def selectable_rsa_for_purge(incidents: list[dict], batch_id: str) -> list[dict]:
    """RSA incidents' purge selector. `rsa_incidents` has no
    `custom_attributes` column and its `id` is server-assigned, so this
    can't reuse `selectable_for_purge` — the only handle is exact string
    equality against `created_by == f"demo-seed:{batch_id}"`
    (`generator.py::_make_rsa_incident` writes exactly that value).

    Same strictness as `selectable_for_purge`: an empty batch_id selects
    nothing, a missing or null `created_by` never matches, and a
    `created_by` that merely *contains* the marker (e.g. a staff note that
    happens to mention a batch id) never matches — equality only. A real
    staff-entered incident's `created_by` is a user identity, which can
    never collide with this format.
    """
    if not batch_id:
        return []
    marker = f"demo-seed:{batch_id}"
    return [i for i in incidents if i.get("created_by") == marker]


# --- create_* (real network I/O) --------------------------------------------


async def create_contact(contact: DemoContact, batch_id: str) -> int:
    """Create one Chatwoot contact, stamped with the purge marker and the
    vehicle fields Customer 360 / the Cases list read off contacts."""
    config = _require_config()
    payload = {
        "inbox_id": config.chatwoot_inbox_id,
        "name": contact.name,
        "email": contact.email,
        "phone_number": contact.phone,
        "custom_attributes": {
            "demo_seed": batch_id,
            "vehicle_no": contact.vehicle_no,
            "vehicle_model": contact.vehicle_model,
            "purchased_from": contact.purchased_from,
        },
    }
    response = await _chatwoot.post(_account_path("/contacts"), json=payload)
    response.raise_for_status()
    data = response.json()
    # The account-level create returns {"payload": {"contact": {"id": ...}}};
    # tolerate a bare {"id": ...} too (backend/.../adapters/chatwoot.py's
    # _contact_id_from does the same for the same endpoint).
    contact_obj = data.get("payload", {}).get("contact") if isinstance(data.get("payload"), dict) else None
    contact_id = (contact_obj or data).get("id")
    if contact_id is None:
        raise RuntimeError(f"contact create returned no id: {data!r}")
    await _throttle()
    return int(contact_id)


async def create_case(case: DemoCase, contact_id: int, batch_id: str) -> int:
    """Create one Chatwoot conversation for `contact_id`, post its seeded
    message thread, and stamp the purge marker + case fields.

    Status is forced through `_safe_status` — see the module's bot-safety
    invariant. Even if Chatwoot's conversation-create endpoint silently
    ignores the `status` field (its default for an API-channel conversation
    is `open`), the fallback is still on the bot-ignored list, so this is
    safe either way.
    """
    config = _require_config()
    status = _safe_status(case.status)

    create_payload = {
        "contact_id": contact_id,
        "inbox_id": config.chatwoot_inbox_id,
        "status": status,
    }
    response = await _chatwoot.post(_account_path("/conversations"), json=create_payload)
    response.raise_for_status()
    data = response.json()
    conversation_id = data.get("id")
    if conversation_id is None:
        raise RuntimeError(f"conversation create returned no id: {data!r}")
    conversation_id = int(conversation_id)
    await _throttle()

    for sender_role, body in case.messages:
        message_payload = (
            {"content": body, "message_type": "incoming"}
            if sender_role == "customer"
            else {"content": body, "message_type": "outgoing", "private": False}
        )
        message_response = await _chatwoot.post(
            _account_path(f"/conversations/{conversation_id}/messages"), json=message_payload
        )
        message_response.raise_for_status()
        await _throttle()

    # vehicle_no/purchased_from are NOT duplicated here: DemoCase carries no
    # vehicle fields (those live on DemoContact), and Chatwoot's
    # conversation-list response already embeds the contact's own
    # custom_attributes at `meta.sender.custom_attributes` — the Cases list
    # (Task 5) should join through that rather than this module stamping a
    # second, driftable copy onto every conversation.
    custom_attributes = {
        "demo_seed": batch_id,
        "case_type": case.case_type,
        "division": case.division,
        "concern": case.concern,
        "channel": case.channel,
        "dealer": case.dealer,
    }
    attrs_response = await _chatwoot.post(
        _account_path(f"/conversations/{conversation_id}/custom_attributes"),
        json={"custom_attributes": custom_attributes},
    )
    attrs_response.raise_for_status()
    await _throttle()

    # division_<slug> / dealer_<slug> labels — the convention
    # agent/app/services/sync.py and backend/.../metrics/mapping.py already
    # read back for division derivation and dealer TAT reporting.
    labels_response = await _chatwoot.post(
        _account_path(f"/conversations/{conversation_id}/labels"),
        json={"labels": [f"division_{_slugify(case.division)}", f"dealer_{_slugify(case.dealer)}"]},
    )
    labels_response.raise_for_status()
    await _throttle()

    return conversation_id


async def create_rsa_incident(payload: dict) -> str:
    """POST one RSA incident payload as-is (generator.py builds it to match
    `rsa_router.py::_IncidentRequest` field-for-field, timestamps
    pre-`.isoformat()`-ed).

    Returns the server-assigned incident id. Note: `rsa_db.py`'s
    `RsaIncident.id` is a UUID string (`rsa_repository.py` does
    `str(uuid.uuid4())`), not an int — the plan's Interfaces section lists
    this function as `-> int`, which doesn't match the backend's actual id
    type. Corrected here to `-> str`; see the task report for detail. The
    purge marker already lives in `payload["created_by"]` (set by
    `generator.py::_make_rsa_incident`), so this return value has no
    downstream purge consumer — it's purely informational for a summary.
    """
    _require_config()
    response = await _backend.post("/rsa/incidents", json=payload)
    response.raise_for_status()
    data = response.json()
    incident_id = data.get("id")
    if incident_id is None:
        raise RuntimeError(f"rsa incident create returned no id: {data!r}")
    await _throttle()
    return str(incident_id)


# --- purge (real network I/O) -----------------------------------------------


async def _search_demo_contacts() -> list[dict]:
    """Page `/contacts/search?q=[DEMO]` to gather purge candidates.

    Chatwoot has no server-side filter on `custom_attributes`, so this
    narrows to the least-broad search that can plausibly contain seeded
    records (every seeded contact's name is `[DEMO]`-prefixed —
    `generator.py::_make_contact`) before `selectable_for_purge` applies the
    strict marker check. A contact matching this search but not the marker
    is left alone and reported as skipped, never force-deleted.
    """
    contacts: list[dict] = []
    for page in range(1, _MAX_SEARCH_PAGES + 1):
        response = await _chatwoot.get(_account_path("/contacts/search"), params={"q": "[DEMO]", "page": page})
        response.raise_for_status()
        page_contacts = _payload_list(response.json())
        await _throttle()
        if not page_contacts:
            break
        contacts.extend(page_contacts)
    return contacts


async def purge(batch_id: str) -> PurgeReport:
    """Delete every Chatwoot contact/conversation and RSA incident carrying
    this batch's marker, and nothing else.

    Chatwoot contacts+conversations: search for `[DEMO]`-named contacts,
    then each matched contact's own conversations, applying
    `selectable_for_purge` at both levels before any delete. RSA incidents:
    list all incidents and apply `selectable_rsa_for_purge`. Anything that
    matches the broad search/list but not the strict marker check is
    recorded in `PurgeReport.skipped` and left untouched — deleting too
    little here is recoverable, deleting too much is not.
    """
    if not batch_id:
        return PurgeReport(skipped=["empty batch_id: refused to search for or delete anything"])

    _require_config()
    report = PurgeReport()

    candidate_contacts = await _search_demo_contacts()
    matched_contacts = selectable_for_purge(candidate_contacts, batch_id)
    matched_contact_ids = {c["id"] for c in matched_contacts}
    for contact in candidate_contacts:
        if contact.get("id") not in matched_contact_ids:
            report.skipped.append(
                f"contact {contact.get('id')}: name matched '[DEMO]' but demo_seed marker did not match batch {batch_id!r}"
            )

    for contact in matched_contacts:
        contact_id = contact["id"]

        conv_response = await _chatwoot.get(_account_path(f"/contacts/{contact_id}/conversations"))
        conv_response.raise_for_status()
        conversations = _payload_list(conv_response.json())
        await _throttle()

        matched_conversations = selectable_for_purge(conversations, batch_id)
        matched_conversation_ids = {c["id"] for c in matched_conversations}
        for conv in conversations:
            if conv.get("id") not in matched_conversation_ids:
                report.skipped.append(
                    f"conversation {conv.get('id')} (contact {contact_id}): demo_seed marker did not match batch {batch_id!r}"
                )

        for conv in matched_conversations:
            conv_id = conv["id"]
            delete_response = await _chatwoot.delete(_account_path(f"/conversations/{conv_id}"))
            await _throttle()
            if delete_response.status_code not in (200, 204):
                report.skipped.append(f"conversation {conv_id}: delete failed (HTTP {delete_response.status_code})")
            else:
                report.conversations_deleted += 1

        delete_response = await _chatwoot.delete(_account_path(f"/contacts/{contact_id}"))
        await _throttle()
        if delete_response.status_code not in (200, 204):
            report.skipped.append(f"contact {contact_id}: delete failed (HTTP {delete_response.status_code})")
        else:
            report.contacts_deleted += 1

    rsa_response = await _backend.get("/rsa/incidents")
    rsa_response.raise_for_status()
    rsa_data = rsa_response.json()
    incidents = rsa_data.get("incidents") if isinstance(rsa_data, dict) else None
    incidents = [i for i in (incidents or []) if isinstance(i, dict)]
    await _throttle()

    matched_incidents = selectable_rsa_for_purge(incidents, batch_id)
    for incident in matched_incidents:
        incident_id = incident.get("id")
        if incident_id is None:
            report.skipped.append(f"rsa incident matched batch {batch_id!r} but had no id, cannot delete")
            continue
        delete_response = await _backend.delete(f"/rsa/incidents/{incident_id}")
        await _throttle()
        if delete_response.status_code not in (200, 204):
            report.skipped.append(f"rsa incident {incident_id}: delete failed (HTTP {delete_response.status_code})")
        else:
            report.rsa_incidents_deleted += 1

    return report
