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

**Bot-safety invariant:** seeded conversations must never end up `pending`.
`agent/app/services/orchestrator.py`'s agent-bot only acts on an incoming
customer message on a `pending` conversation; a seeded `pending`
conversation on a bot-enabled inbox would fire an AI reply (and possibly an
escalation email) per seeded case against a live tenant. Sending a
non-`pending` `status` on create is NOT sufficient on its own: Chatwoot's
`Conversation` has `before_create :determine_conversation_status`, whose
body is `self.status = :pending if inbox.active_bot?` — it silently
overrides whatever the API caller asked for on any inbox with an agent bot
attached. Three layers therefore enforce the invariant:

1. `_safe_status` maps the generator's status onto the bot-ignored set, so
   the request never *asks* for `pending`;
2. `assert_inbox_is_safe_to_seed` refuses, before any write, an inbox that
   has an agent bot or that isn't an API channel (`__main__.py` calls it
   ahead of the confirmation prompt);
3. `create_case` reads the created conversation's `status` back off the
   create response and raises immediately if Chatwoot returned `pending`
   anyway — so a bot-enabled inbox that slipped past (2) costs one
   conversation, not the whole batch.

**Webhook-safety invariant:** the conversation `custom_attributes` endpoint
REPLACES the whole object (`ConversationsController#custom_attributes` does
`@conversation.custom_attributes = params[...]`), and posting `labels`
fires `conversation_updated`, which reaches
`agent/app/services/sync.py::maybe_stamp_dealer_escalation`. That handler
writes `dealer_escalated_at` via the same replacing endpoint, which used to
wipe `demo_seed` (and everything else) off every seeded conversation the
instant its labels were posted — defeating purge, backdate, the metrics
exclusion flag and the Cases list at once. `create_case` therefore writes
`dealer_escalated_at` itself, in its own `custom_attributes` POST, on every
conversation that gets a `dealer_<slug>` label: the handler short-circuits
on `if existing.get("dealer_escalated_at"): return` before it ever writes.
(The agent-side root cause — `set_custom_attributes` documenting itself as
"Merge-set" while replacing — is fixed separately in
`agent/app/clients/chatwoot.py`; this file does not depend on that fix
having been deployed.)

Endpoint shapes (headers, path prefixes, payload keys, response envelopes)
follow `agent/app/clients/chatwoot.py` and
`backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`, the
two existing call sites for this API in this repo — read those before
changing this file, including the reference's `source_id` on conversation
create (`create_case` sends a deterministic one — see its docstring).
`/conversations/{id}` and `/contacts/{id}` DELETE and `/contacts/search`
pagination are not exercised anywhere else in this repo, so they are
inferred from Chatwoot's own REST conventions rather than confirmed against
a live tenant; Task 4's `default`-tenant rehearsal is what actually proves
them. `GET /inboxes/{id}` mirrors `agent/app/clients/chatwoot.py::get_inbox`;
`GET /inboxes/{id}/agent_bot` is read straight off Chatwoot's own
`config/routes.rb` (`get :agent_bot, on: :member`) and its
`inboxes/agent_bot.json.jbuilder`, which renders `{"agent_bot": {}}` when no
bot is attached.

**Verified, not assumed, post-conditions:** the reference call sites never
send `custom_attributes` on contact *create* (only on conversation update,
via a dedicated `.../custom_attributes` endpoint), so whether Chatwoot's
contact-create endpoint actually persists an unrecognised `custom_attributes`
key was unconfirmed. `create_contact` no longer assumes it does: it reads
the marker back off the create response and, if absent, stamps it with an
explicit follow-up `PATCH .../contacts/{id}` call. Silently trusting the
assumption would have failed safe (never a wrong delete) but could have left
seeded contacts permanently unpurgeable — exactly what `purge` exists to
prevent.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from generator import DemoCase, DemoContact, canonical_division
from nasabah import DemoNasabah

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
    """Map a DemoCase's status to one the agent-bot ignores. Layer 1 of the
    module's bot-safety invariant — the request never *asks* for `pending`.
    Not sufficient alone; see the docstring for layers 2 and 3."""
    return status if status in _BOT_SAFE_STATUSES else "open"


# --- pure selectors (the tested surface) ------------------------------------


def conversation_source_id(channel: str, batch_id: str, case_index: int) -> str:
    """The deterministic, obviously-synthetic `source_id` a seeded
    conversation is created with.

    The channel token comes FIRST because that is the only place the
    warehouse can learn a conversation's channel from:
    `backend/.../metrics/mapping.py::channel_from_external_id` takes
    `external_id.split("-", 1)[0]` and looks it up in a fixed prefix table
    ({whatsapp, email, phone, sim, zendesk, chatwoot}). A `source_id` of
    `demo-seed:...` prefix-matches as `demo`, i.e. "Other" for 100% of rows,
    which flattens the deck-derived 73/16/9/2 channel split into a single
    bar. Prefixing costs nothing: `purge` keys on the `demo_seed` custom
    attribute, never on `source_id`.

    `whatsapp`/`email`/`phone` land in their real buckets; `social` isn't in
    the prefix table and correctly reports as "Other" (~2% of cases), which
    is what the warehouse would do with a real social conversation too.

    Deterministic in `(batch_id, case_index)` so a re-run with the same pair
    addresses the same synthetic conversation identity, and unmistakably
    non-real so it can never collide with a genuine conversation's
    `source_id`.
    """
    return f"{channel}-demo-seed:{batch_id}:case-{case_index}"


# --- pre-flight (real network I/O) ------------------------------------------


class UnsafeInboxError(RuntimeError):
    """The target inbox is not safe to seed into. Raised *before* any write.

    Deliberately its own type so `__main__.py` can report it as an operator
    error (a wrong `--inbox-id`) rather than as a crash.
    """


def inbox_seeding_refusal_reason(inbox_id: int, inbox: dict, agent_bot_response: dict) -> str | None:
    """Pure decision for layer 2 of the bot-safety invariant: given an
    inbox's own payload and its `GET /inboxes/{id}/agent_bot` response
    (already unwrapped from a Chatwoot response, or `{}` if that call
    failed/returned something unexpected), decide whether it is safe to seed
    into. Returns `None` when safe, or a human-readable refusal reason
    otherwise -- `assert_inbox_is_safe_to_seed` raises `UnsafeInboxError`
    with it.

    Two independent reasons to refuse, both fatal:

    - **An agent bot is attached.** Chatwoot's `before_create
      :determine_conversation_status` forces `status = :pending` when
      `inbox.active_bot?`, discarding the `status` `create_case` sends. A
      `pending` conversation carrying an incoming customer message is exactly
      what `agent/app/services/orchestrator.py` acts on, so a 100-contact
      seed would be ~140 Gemini calls and ~140 AI replies posted into a
      tenant a client can see.
    - **The channel isn't `Channel::Api`.** Every other channel type is wired
      to a real transport (WhatsApp, email, a website widget). Creating
      conversations and posting `outgoing` messages there risks actually
      *delivering* demo content to whatever that inbox is connected to.

    The channel check runs first, matching the order the network wrapper
    makes its two GETs in (so the operator error names the cheaper-to-fix
    problem first when both are wrong).

    `agent_bot_response` renders `{"agent_bot": {...}}` when a bot is
    attached and `{"agent_bot": {}}` when not
    (`app/views/api/v1/accounts/inboxes/agent_bot.json.jbuilder`). Note
    `Inbox#active_bot?` is broader than this endpoint (it also counts an
    enabled `dialogflow` hook), so this check is necessary, not sufficient —
    which is why `create_case` still verifies the status it got back.
    """
    channel_type = inbox.get("channel_type")
    if channel_type != "Channel::Api":
        return (
            f"inbox {inbox_id} has channel_type {channel_type!r}, not 'Channel::Api'. "
            "Seeding is only allowed into a dedicated API-channel inbox -- any other "
            "channel is wired to a real transport and could deliver demo content to "
            "real recipients. Create an API inbox for demo data and pass its id."
        )

    agent_bot = agent_bot_response.get("agent_bot") if isinstance(agent_bot_response, dict) else None
    if isinstance(agent_bot, dict) and agent_bot.get("id") is not None:
        return (
            f"inbox {inbox_id} has agent bot {agent_bot.get('id')} "
            f"({agent_bot.get('name')!r}) attached. Chatwoot forces every conversation "
            "on a bot-enabled inbox to status 'pending' (Conversation's "
            "before_create :determine_conversation_status), which is exactly what the "
            "agent-bot orchestrator acts on -- seeding here would fire an AI reply per "
            "case against a live tenant. Use an inbox with no agent bot."
        )

    return None


async def assert_inbox_is_safe_to_seed(inbox_id: int) -> dict:
    """Layer 2 of the bot-safety invariant: refuse an inbox that would turn a
    seed run into a live AI conversation.

    Thin I/O wrapper: fetches the inbox and its agent-bot attachment, then
    defers the actual decision to `inbox_seeding_refusal_reason` (pure, and
    the tested surface -- see this module's docstring for why the network
    calls themselves aren't mock-harnessed).

    Returns the inbox payload so the caller can show the operator which inbox
    it just validated. Raises `UnsafeInboxError` if either check fails; HTTP
    failures propagate (an inbox we cannot read is an inbox we cannot clear).
    """
    _require_config()
    response = await _chatwoot.get(_account_path(f"/inboxes/{inbox_id}"))
    response.raise_for_status()
    inbox = response.json()
    inbox = inbox if isinstance(inbox, dict) else {}
    await _throttle()

    bot_response = await _chatwoot.get(_account_path(f"/inboxes/{inbox_id}/agent_bot"))
    bot_response.raise_for_status()
    bot_data = bot_response.json()
    bot_data = bot_data if isinstance(bot_data, dict) else {}
    await _throttle()

    refusal = inbox_seeding_refusal_reason(inbox_id, inbox, bot_data)
    if refusal is not None:
        raise UnsafeInboxError(refusal)

    return inbox


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
    vehicle fields Customer 360 / the Cases list read off contacts.

    The `custom_attributes` sent on the create *request* are a request, not
    a confirmed outcome — the reference call sites for this endpoint never
    exercise that field on create, so it's unverified whether Chatwoot
    persists an unrecognised custom-attributes key at create time rather
    than silently dropping it. This function therefore checks the create
    *response* for the marker and, if it's missing, stamps it with an
    explicit follow-up call — see the module docstring's "Verified, not
    assumed" note. That turns an assumption into a verified post-condition:
    a contact this function returns is guaranteed marked, or the call
    itself raised.
    """
    config = _require_config()
    demo_attributes = {
        "demo_seed": batch_id,
        "vehicle_no": contact.vehicle_no,
        "vehicle_model": contact.vehicle_model,
        "purchased_from": contact.purchased_from,
    }
    payload = {
        "inbox_id": config.chatwoot_inbox_id,
        "name": contact.name,
        "email": contact.email,
        "phone_number": contact.phone,
        "custom_attributes": demo_attributes,
    }
    response = await _chatwoot.post(_account_path("/contacts"), json=payload)
    if response.status_code == 422:
        # Chatwoot enforces phone/email uniqueness per ACCOUNT, not per batch.
        # `generate()` mixes batch_id into its RNG seed precisely so two
        # batches don't collide -- but a re-run with an explicit `--batch-id`
        # that was already seeded, or a genuine collision with a real contact,
        # still lands here. Raw HTTP 422 text ("Phone number has already been
        # taken") tells an operator nothing about which knob fixes it.
        raise RuntimeError(
            f"Chatwoot rejected demo contact {contact.name!r} (phone {contact.phone}, "
            f"email {contact.email}) with HTTP 422: {response.text.strip()[:300]}. "
            "This is almost always a uniqueness collision -- a contact with that phone "
            "or email already exists in this account, i.e. this batch's generated "
            "identities have been seeded here before. Purge the earlier batch, or "
            "re-run with a different --rng-seed (or a different --batch-id, which is "
            "also mixed into the generator's seed) to generate fresh identities."
        )
    response.raise_for_status()
    data = response.json()
    # The account-level create returns {"payload": {"contact": {...}}};
    # tolerate a bare {...} too (backend/.../adapters/chatwoot.py's
    # _contact_id_from does the same for the same endpoint).
    contact_obj = data.get("payload", {}).get("contact") if isinstance(data.get("payload"), dict) else None
    contact_obj = contact_obj if isinstance(contact_obj, dict) else data
    contact_id = contact_obj.get("id")
    if contact_id is None:
        raise RuntimeError(f"contact create returned no id: {data!r}")
    contact_id = int(contact_id)
    await _throttle()

    returned_attributes = contact_obj.get("custom_attributes")
    marker_confirmed = isinstance(returned_attributes, dict) and returned_attributes.get("demo_seed") == batch_id
    if not marker_confirmed:
        stamp_response = await _chatwoot.patch(
            _account_path(f"/contacts/{contact_id}"), json={"custom_attributes": demo_attributes}
        )
        stamp_response.raise_for_status()
        await _throttle()

    return contact_id


def build_nasabah_custom_attributes(nasabah: DemoNasabah, batch_id: str) -> dict[str, str]:
    """The full contact `custom_attributes` object one seeded nasabah carries.

    Pure and public for the same reason `build_case_custom_attributes` is: the
    exact key set is a contract with two consumers that never import this
    module -- Chatwoot's contact attribute *definitions*, which is what makes
    these render in the agent sidebar, and the agent service's
    `customer_context` formatter, which reads them back to build the AI's
    prompt. Renaming a key here silently empties both rather than failing.

    Every value is a string. Chatwoot round-trips custom attribute values as
    strings, and a list sent here comes back in a shape the sidebar renders
    as an object literal. Empty lists become an explicit phrase rather than
    an empty string: the AI reads these verbatim, and a blank field is
    ambiguous between "holds no equities" and "we have no data", which are
    very different things to say to a customer.
    """
    return {
        "demo_seed": batch_id,
        "risk_profile": nasabah.risk_profile,
        "aum_band": nasabah.aum_band,
        "rdn_balance": f"Rp {nasabah.rdn_balance:,}",
        "holdings": ", ".join(nasabah.holdings) if nasabah.holdings else "Tidak ada",
        "days_since_last_transaction": str(nasabah.days_since_last_transaction),
        "product_gaps": ", ".join(nasabah.product_gaps) if nasabah.product_gaps else "Tidak ada",
        "next_best_offer": nasabah.next_best_offer,
        "offer_rationale": nasabah.offer_rationale,
    }


async def create_nasabah_contact(nasabah: DemoNasabah, batch_id: str) -> int:
    """Create one Chatwoot contact carrying a synthetic nasabah profile.

    Deliberately a sibling of `create_contact` rather than a parameter on it:
    that function writes the automotive attribute set (`vehicle_no`,
    `vehicle_model`, `purchased_from`) that Customer 360 and the Cases list
    read, and those two attribute sets have no overlap and no shared consumer.
    Merging them would mean every contact carrying both vocabularies.

    The create-then-verify-then-PATCH shape is copied from `create_contact`
    for the same reason it exists there: it is unverified whether Chatwoot
    persists unrecognised custom-attribute keys at create time, so the marker
    is confirmed on the response and stamped explicitly if absent. A contact
    this function returns is guaranteed marked, or the call raised.
    """
    config = _require_config()
    demo_attributes = build_nasabah_custom_attributes(nasabah, batch_id)
    payload = {
        "inbox_id": config.chatwoot_inbox_id,
        "name": nasabah.name,
        "email": nasabah.email,
        "phone_number": nasabah.phone,
        "custom_attributes": demo_attributes,
    }
    response = await _chatwoot.post(_account_path("/contacts"), json=payload)
    if response.status_code == 422:
        raise RuntimeError(
            f"Chatwoot rejected demo nasabah {nasabah.name!r} (phone {nasabah.phone}, "
            f"email {nasabah.email}) with HTTP 422: {response.text.strip()[:300]}. "
            "This is almost always a uniqueness collision -- a contact with that phone "
            "or email already exists in this account. Purge the earlier batch, or "
            "re-run with a different --batch-id. If the collision is on the PINNED "
            "phone, the demo handset is already a contact in this account: either "
            "purge it or drop --pinned-phone and edit that contact's attributes by hand."
        )
    response.raise_for_status()
    data = response.json()
    contact_obj = data.get("payload", {}).get("contact") if isinstance(data.get("payload"), dict) else None
    contact_obj = contact_obj if isinstance(contact_obj, dict) else data
    contact_id = contact_obj.get("id")
    if contact_id is None:
        raise RuntimeError(f"nasabah contact create returned no id: {data!r}")
    contact_id = int(contact_id)
    await _throttle()

    returned_attributes = contact_obj.get("custom_attributes")
    marker_confirmed = isinstance(returned_attributes, dict) and returned_attributes.get("demo_seed") == batch_id
    if not marker_confirmed:
        stamp_response = await _chatwoot.patch(
            _account_path(f"/contacts/{contact_id}"), json={"custom_attributes": demo_attributes}
        )
        stamp_response.raise_for_status()
        await _throttle()

    return contact_id


def build_case_custom_attributes(case: DemoCase, contact: DemoContact, batch_id: str, now: datetime) -> dict:
    """The full `custom_attributes` object one seeded conversation carries.

    Pure, and separated out because getting this object wrong is what broke
    the package end to end: it is simultaneously the purge marker, the
    backdate guard, the metrics-exclusion key, the Cases list's data source
    and the warehouse's category/vehicle dimensions — and the endpoint that
    writes it REPLACES rather than merges, so there is exactly one chance to
    get every key in.

    Keys, and who reads them:

    - `demo_seed` — `selectable_for_purge`, `select_backdate_targets`,
      `backdate_conversation`'s SQL guard, and the metrics sync's
      `METRICS_EXCLUDE_DEMO_SEED` pre-filter.
    - `dealer_escalated_at` — the warehouse's dealer-TAT view, AND (the
      reason it is written here at all) the short-circuit that stops
      `agent/app/services/sync.py::maybe_stamp_dealer_escalation` from
      REPLACING this whole object with `{"dealer_escalated_at": ...}` when
      the `dealer_<slug>` label POST fires `conversation_updated`. Written
      only when the case actually has a dealer — a case with no dealer gets
      no dealer label, so the handler never fires for it, and inventing a
      timestamp would put a dealer-less case into the dealer-TAT view.
      Its value is wall-clock *now* (not `case.created_at`) because
      `backdate` shifts it by the same delta it shifts the row's `created_at`
      by; anchoring it to seed-time is what makes it land beside the
      backdated `created_at` instead of weeks after it.
    - `case_category` / `case_subcategory` —
      `backend/.../metrics/mapping.py` reads exactly these two keys; without
      them every demo row's category and subcategory are NULL. `case_category`
      is the *canonical* division so `CATEGORY_TO_DIVISION` resolves it, and
      `case_subcategory` uses the flattened `"<Label>: <Subcategory>"` shape
      `agent/app/services/categorize.py` writes for real conversations.
    - `vehicle_model` — also read by `mapping.py` off the CONVERSATION, which
      has no `meta.sender` to join through.
    - `vehicle_no` — spec §3 puts it on both the contact and its
      conversations. The Cases list can join through `meta.sender`, but the
      warehouse cannot, so the conversation-level copy is not redundant.
    - `case_type`, `division`, `concern`, `channel`, `dealer` — the Cases
      list's own columns, in the deck's display vocabulary.
    """
    attributes = {
        "demo_seed": batch_id,
        "case_type": case.case_type,
        "division": case.division,
        "concern": case.concern,
        "channel": case.channel,
        "case_category": canonical_division(case.division),
        "case_subcategory": f"{canonical_division(case.division)}: {case.concern}",
        "vehicle_no": contact.vehicle_no,
        "vehicle_model": contact.vehicle_model,
    }
    if case.dealer:
        attributes["dealer"] = case.dealer
        attributes["dealer_escalated_at"] = now.isoformat()
    return attributes


def build_case_labels(case: DemoCase) -> list[str]:
    """The `division_<slug>` / `dealer_<slug>` labels one seeded conversation
    gets. Pure, so the exact slug is pinned by a test rather than by a live
    tenant's report looking wrong.

    The division slug is built from the CANONICAL division (see
    `generator.canonical_division`), matching byte-for-byte what the live
    writer `backend/.../adapters/chatwoot.py::_classification_labels` emits
    for real traffic — `mapping.py` reads this suffix back raw, so
    `division_after_sales` would sit in its own bucket next to real traffic's
    `division_aftersales`.

    The dealer label is present only for the minority of cases that actually
    have a dealer; posting one is what makes the conversation appear in the
    dealer-TAT view, and (via `conversation_updated`) what wakes
    `maybe_stamp_dealer_escalation`.
    """
    labels = [f"division_{_slugify(canonical_division(case.division))}"]
    if case.dealer:
        labels.append(f"dealer_{_slugify(case.dealer)}")
    return labels


def created_conversation_status_refusal_reason(
    display_id: int, requested_status: str, created_status: str | None
) -> str | None:
    """Pure decision for layer 3 of the bot-safety invariant: given the
    status `create_case` asked for and what Chatwoot's create response
    reported back, decide whether it is safe to continue seeding this
    conversation (its custom_attributes, message thread and labels) or must
    be aborted first. Returns `None` when safe, or a human-readable refusal
    reason otherwise -- `create_case` raises `RuntimeError` with it before
    posting anything else.

    Two refusal cases, both fail-closed:

    - `created_status is None`: this Chatwoot renders no `status` on create
      (v4.15.1 does), so the readback this layer depends on cannot be
      performed at all. Refusing here, rather than assuming the requested
      status stuck, is what makes this layer fail closed instead of silently
      degrading to "layer 2 only".
    - `created_status` is a status the agent-bot does NOT ignore (i.e. not in
      `_BOT_SAFE_STATUSES`) -- in practice `"pending"`, forced by Chatwoot's
      `before_create :determine_conversation_status` on any inbox with an
      active bot, discarding whatever `requested_status` asked for.
    """
    if created_status is None:
        return (
            f"conversation {display_id} create response carried no 'status' field, so "
            "the bot-safety readback cannot be performed. Refusing to continue -- "
            "verify manually that this Chatwoot renders conversation status on create."
        )
    if created_status not in _BOT_SAFE_STATUSES:
        return (
            f"conversation {display_id} came back with status {created_status!r} after "
            f"requesting {requested_status!r}. Chatwoot forces 'pending' on an inbox with "
            "an active bot (Conversation's before_create :determine_conversation_status), "
            "and a 'pending' conversation with an incoming customer message is exactly "
            "what the agent-bot orchestrator answers. Aborting before any message is "
            "posted. Use an inbox with no agent bot and no enabled dialogflow hook."
        )
    return None


async def create_case(case: DemoCase, contact: DemoContact, contact_id: int, batch_id: str, case_index: int) -> int:
    """Create one Chatwoot conversation for `contact_id`, stamp its
    `custom_attributes`, post its seeded message thread, and label it.
    Returns the conversation's **display id** (what `POST /conversations`
    renders as `id` — see below).

    Order matters and is not the obvious one. The `custom_attributes` POST
    happens IMMEDIATELY after create, before any message: `create_payload`
    carries only `contact_id`/`inbox_id`/`status`/`source_id`, so between the
    create and that POST the conversation exists with NO `demo_seed` marker
    and is invisible to `purge`. Doing the messages first would stretch that
    unmarked-orphan window across every message POST in the thread; doing it
    first shrinks it to a single request.

    **Status.** Forced through `_safe_status` (layer 1), then verified
    against what Chatwoot actually created (layer 3). Chatwoot's
    `before_create :determine_conversation_status` overrides the requested
    status with `pending` on any inbox with an active bot, so "we asked for
    `open`" proves nothing — this reads `status` off the create response
    (`_conversation.json.jbuilder` renders it) and raises before posting a
    single message if it came back `pending`. Raising here is the point: one
    orphaned empty conversation is cheap, a batch of AI-answered ones is not.
    `assert_inbox_is_safe_to_seed` (layer 2) should already have made this
    unreachable; it stays because `Inbox#active_bot?` is broader than the
    `agent_bot` endpoint that check can see (it also counts an enabled
    dialogflow hook).

    **The returned id is a display id, not a primary key.** `POST
    /conversations` renders `json.id conversation.display_id`. That is the
    right id for every other Application API call here (`/conversations/{id}`
    routes resolve by `display_id`), but it is NOT `conversations.id` — which
    is why the manifest records `account_id` alongside it and `backdate.py`
    resolves `(account_id, display_id)` to a real primary key in SQL rather
    than assuming the two coincide.

    `case_index` is this case's position in the full list `generate()`
    returned (the caller should pass `enumerate(cases)`'s index) — it feeds
    `conversation_source_id` and nothing else.
    """
    config = _require_config()
    status = _safe_status(case.status)
    # Reference call sites (backend/.../adapters/chatwoot.py's
    # _find_or_create_conversation) always send source_id on conversation
    # create — Chatwoot's ConversationBuilder uses it to build the
    # contact_inbox.
    source_id = conversation_source_id(case.channel, batch_id, case_index)

    create_payload = {
        "contact_id": contact_id,
        "inbox_id": config.chatwoot_inbox_id,
        "status": status,
        "source_id": source_id,
    }
    response = await _chatwoot.post(_account_path("/conversations"), json=create_payload)
    response.raise_for_status()
    data = response.json()
    display_id = data.get("id")
    if display_id is None:
        raise RuntimeError(f"conversation create returned no id: {data!r}")
    display_id = int(display_id)
    await _throttle()

    created_status = data.get("status")
    refusal = created_conversation_status_refusal_reason(display_id, status, created_status)
    if refusal is not None:
        raise RuntimeError(refusal)

    # Stamp the marker FIRST -- see the docstring's ordering note. Everything
    # that can find, guard, exclude or display this conversation reads this
    # object, and the endpoint replaces rather than merges, so it is written
    # once, complete.
    custom_attributes = build_case_custom_attributes(case, contact, batch_id, datetime.now(timezone.utc))
    attrs_response = await _chatwoot.post(
        _account_path(f"/conversations/{display_id}/custom_attributes"),
        json={"custom_attributes": custom_attributes},
    )
    attrs_response.raise_for_status()
    await _throttle()

    for sender_role, body in case.messages:
        message_payload = (
            {"content": body, "message_type": "incoming"}
            if sender_role == "customer"
            else {"content": body, "message_type": "outgoing", "private": False}
        )
        message_response = await _chatwoot.post(
            _account_path(f"/conversations/{display_id}/messages"), json=message_payload
        )
        message_response.raise_for_status()
        await _throttle()

    # division_<slug> / dealer_<slug> labels — the convention
    # agent/app/services/sync.py and backend/.../metrics/mapping.py already
    # read back for division derivation and dealer TAT reporting. This POST
    # fires conversation_updated; the custom_attributes above already carry
    # dealer_escalated_at, so maybe_stamp_dealer_escalation short-circuits
    # instead of replacing them (see the module's webhook-safety invariant).
    labels_response = await _chatwoot.post(
        _account_path(f"/conversations/{display_id}/labels"),
        json={"labels": build_case_labels(case)},
    )
    labels_response.raise_for_status()
    await _throttle()

    return display_id


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
