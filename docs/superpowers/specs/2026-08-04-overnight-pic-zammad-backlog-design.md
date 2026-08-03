# Overnight build: PIC/Dealer config UI, Zammad removal, and backlog items

**Status:** Approved for autonomous execution (user explicitly authorized
skipping interactive checkpoints for this run — "decide it which one the
best for this CRM development", 2026-08-04).

**Source:** brainstormed live earlier in the 2026-08-04 session (PIC/Dealer
config + Zammad removal scope decisions were made interactively with the
user before they went to sleep) plus
`docs/analysis/proton-demo-feedback-coverage-2026-07-28.md`'s ⚠️/❌ items
(triaged below — this run does NOT attempt all 17 partially/not-covered
items, only the subset that's genuinely buildable without a missing
external input).

---

## 0. Triage: what this run builds vs. explicitly skips

**Building tonight (5 tracks, detailed below):**
1. PIC/Dealer escalation-routing admin UI + backend
2. Full Zammad removal — code (both services) + live infra (all 3 tenants)
3. Bulk CSV upload for FAQ Q&A pairs (feedback item #1)
4. Round-robin ticket cap per agent (feedback item #20)
5. Customer 360 foundational lookup — phone/vehicle-number keyed (feedback
   items #15, partially #14)

**Explicitly NOT attempted tonight — reason given for each:**

| Item | Why not tonight |
|---|---|
| #2 image/video KB ingestion | Genuinely separate, sizeable multi-modal feature; lower priority than the 5 above given the time available. Backlogged. |
| #4 DMS-API custom tools | Needs real DMS credentials Proton hasn't provided — nothing to build against. |
| #5 PowerBI integration | 🔲 Explicitly needs Proton's target report examples first (testing guide §8 item 9). |
| #7 FAQ Assist quality | Vague ("not helpful" on one live query) — needs real KB content and live testing to diagnose, not something fixable blind. |
| #9 inbox naming confusion | Demo-environment naming, not a code gap. |
| #10 live inbound email | Needs real SMTP/IMAP credentials from Proton. |
| #11 Facebook/Instagram | Needs Meta Business verification, external dependency. |
| #14 per-ticket dashboard | Substantially addressed as a side effect of track 5 (Customer 360); no separate work planned. |
| #16 customer-ID choice for 360 | 🔲 Explicitly needs Proton/Rafael's team discussion. Track 5 uses phone number as the working key (already the de facto standard per the interaction guide's "Previous Conversations" behavior) and is documented as provisional pending Proton's final decision — this is NOT deciding the open question, it's building on the existing convention. |
| #18 email hosting | 🔲 Needs Proton to supply a subdomain + credentials. |
| #21 auto-busy during calls | No clean hook point: real IVR human hand-off (#23) isn't built, so there's no "agent is on this call" event to key off yet. Needs product definition, not guessable. |
| #22 DTMF vs. conversational IVR | 🔲 Explicitly flagged as needing Proton's decision. |
| #23 real IVR hand-off | Live telephony change on a production Twilio number with no way to test a real call from this environment — too risky to ship blind. |
| #25 WhatsApp voice notes | Already code-complete (`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` live); only needs a real-WhatsApp-device confirmation, which isn't buildable. |
| #26 video-understanding discrepancy | Not an engineering task — it's a client-communication gap already flagged in the audit doc. Nothing to build. |
| #27 call recording | Needs a compliance/consent decision plus live Twilio config change — same risk profile as #23. |
| #28 RBAC report separation | Already built (patch `0028`); only needs a demo to the client. |
| #29 report examples from Proton | 🔲 Needs Proton input. |
| #30 category cascading | Already built and deployed (patch `0036`); only needs a manual browser confirmation. |
| #31 vague UX feedback | No specifics given, nothing concrete to act on. |

---

## 1. PIC/Dealer escalation-routing admin UI + backend

*(Design finalized interactively earlier this session — reproduced here for
the implementation plan's benefit.)*

**Storage:** two new Firestore stores mirroring `ChannelPriorityStore`'s
exact pattern (`backend/apps/backend/src/chatbot/features/routing/store.py`)
— fail-open, `asyncio.to_thread`, one document per key:
- `PicStore` — collection `escalation_pics`, doc key = department slug,
  fields: `department`, `pic_name`, `pic_email`, `pic_whatsapp`,
  `cc_emails: list[str]`. No `zammad_group` field (Zammad is being removed
  in track 2 of this same run).
- `DealerStore` — collection `escalation_dealers`, doc key = dealer slug,
  fields: `dealer`, `email`.

**Migration, zero breakage:** `PicRegistry.lookup()`
(`backend/apps/backend/src/chatbot/features/chat/pic_registry.py`) becomes
`async` — checks the injected `PicStore` first; if that department has no
store entry, falls back to parsing the legacy `PIC_MAP_JSON` env var (same
parsing logic already in `build_pic_registry`, just as a fallback path
instead of the only path). Same pattern for `DealerStore`/
`DEALER_EMAIL_MAP_JSON` in `escalation_notifier.py`. Every existing call
site of `PicRegistry.lookup()` is already inside an `async` function
(verified: `EscalationNotifier._resolve_pic`, `chatwoot.py`'s `_pic_label`
and the `_fire_escalation` PIC resolution) — adding `await` at each is a
compatible change, not a signature break for callers outside this
component.

**API:** new router `pic_admin_router.py`, prefix `/admin/escalation`,
mounted alongside the existing `sla_policy_router`/`audit_router` pattern.
Gated by a new `escalation.manage` permission via the SAME
`require_permission()` dependency every other admin router already uses
(`chatbot.features.authz.deps.require_permission`) — add
`"escalation.manage": "Manage PIC/dealer escalation routing"` to
`PERMISSION_REGISTRY` in `backend/apps/backend/src/chatbot/features/authz/seed.py`.
Endpoints:
- `GET /admin/escalation/pics` — list all PIC entries
- `PUT /admin/escalation/pics/{department}` — upsert (body: pic_name,
  pic_email, pic_whatsapp, cc_emails)
- `DELETE /admin/escalation/pics/{department}`
- `GET /admin/escalation/dealers` — list all dealer entries
- `PUT /admin/escalation/dealers/{dealer}` — upsert (body: email)
- `DELETE /admin/escalation/dealers/{dealer}`

**Frontend:** new Chatwoot fork patch, standalone sidebar page "Escalation
Routing" — same RBAC-gated top-level-icon pattern as SLA Policies / Audit
Log / Roles & Permissions (a new route + nav entry, gated on
`escalation.manage` via `protonHasPermission`). Two simple tables (PIC
entries, Dealer entries) with add/edit/delete forms, using `adminRequest()`
from `protonAdmin.js` (forwards both the shared backend key and the
caller's Chatwoot session, matching every sibling admin page).

**Out of scope:** migrating existing `PIC_MAP_JSON`/`DEALER_EMAIL_MAP_JSON`
values INTO the new store automatically — the fallback means nothing
breaks, and an operator can re-enter values through the new UI at their own
pace.

---

## 2. Drop Zammad entirely

*(Design finalized interactively earlier this session.)*

**Code removal — `agent/`:**
- Delete `agent/app/clients/zammad.py`, `agent/app/routers/zammad.py`,
  `agent/app/services/responder.py` (Zammad draft-reply flow)
- `agent/app/services/sync.py` — remove `escalate_conversation`,
  `_ensure_zammad_customer`, the Zammad-ticket-creation branch of
  `maybe_escalate` (KEEP `_maybe_notify_email_escalation` and everything
  else added this session — that's the Chatwoot-only path, unaffected)
- `agent/app/config.py` — remove all `zammad_*` fields including
  `zammad_ticketing_enabled` (no more "off" state to toggle — Zammad simply
  doesn't exist in the codebase after this)
- `agent/app/clients/deps.py` — remove `get_zammad_client`
- `agent/app/main.py` — remove the Zammad router mount and any
  `on_ticket_event`/Zammad wiring
- Delete Zammad-specific test files; remove Zammad-path tests from
  `agent/tests/test_sync_escalation.py` (KEEP the Chatwoot-only-path tests
  added this session, e.g. `test_maybe_escalate_notifies_email_channel_conversation`)

**Code removal — `backend/apps/backend/`:**
- Delete `backend/apps/backend/src/chatbot/features/chat/adapters/zammad.py`
  (`ZammadClient`)
- `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py` —
  remove `_direct_zammad_active()`, the Zammad-ticket-creation branch inside
  `_fire_escalation`, all `self._zammad` references, remove the now-dead
  `_complaint_labels()` suppression-when-Zammad-active branch (the
  `escalate` label no longer needs suppressing since nothing consumes it
  for ticket creation anymore — but the label itself may still be used by
  the email-escalation webhook path from this session, so `_complaint_labels`
  should keep APPLYING the label, just drop the "suppress when Zammad
  active" conditional, always applying it)
- `backend/apps/backend/src/chatbot/main.py` — remove Zammad wiring
- `backend/apps/backend/src/chatbot/platform/config.py` — remove all
  `zammad_*` settings
- Delete Zammad-specific test files

**Deploy infra:**
- `deploy/docker-compose.tenant.yml` — remove the 5 `zammad-*` service
  definitions (zammad-init, zammad-railsserver, zammad-scheduler,
  zammad-websocket, zammad-nginx)
- `deploy/scripts/add-tenant.sh` — remove Zammad DB/role provisioning
  (`CREATE ROLE zammad_${TENANT}`, `CREATE DATABASE zammad_${TENANT}`) and
  any Zammad env var templating
- `deploy/tenants/example.env` — remove `ZAMMAD_*` vars
- `CLAUDE.md` (repo root) — update the "migrating to Chatwoot-only" section
  to reflect Zammad is now fully removed, not just gated off; remove the
  `ZAMMAD_TICKETING_ENABLED` mention in the webhook-pattern section

**Live infra (all 3 tenants — default, proton, wahchan):**
- Stop and remove the running `zammad-*` containers on `default` and
  `wahchan` (proton's were already dropped 2026-07-26, only the
  compose-file definition removal applies there now)
- **Preserve, not purge** the Postgres DBs/volumes for `default` and
  `wahchan` — same conservative approach already used for proton
  (`docker compose -p <tenant> rm -sf zammad-*`, leave
  `zammad_<tenant>` DB and `<tenant>_zammad_storage` volume in place)

**Testing:** full agent + backend suites must pass with zero Zammad
references remaining (`grep -ri zammad` across both `agent/` and
`backend/apps/backend/src/` after the removal should return nothing outside
of comments explaining the historical removal, if any are left for
context).

---

## 3. Bulk CSV upload for FAQ Q&A pairs

**Problem:** `Settings → Knowledge → FAQs` only supports one-by-one manual
entry (`docs/analysis/proton-crm-gap-analysis-2026-07-27.md` §4,
feedback-coverage doc #1).

**Backend:** extend `backend/apps/backend/src/chatbot/features/chat/faq_admin_router.py`
with `POST /kb/faq/bulk` — accepts a `multipart/form-data` file upload (CSV,
columns: `question,answer,keywords,tags` — keywords/tags are
semicolon-separated within their cell, matching how a non-technical
operator would fill this in Excel/Sheets). Parses each row into a
`LiveFaqEntry`-shaped create call, looping `store.create()` per row (reuses
the existing embedding-computation-on-create path — no new indexing logic
needed). Same `x-api-key` auth as the existing FAQ admin endpoints. Returns
`{"created": N, "errors": [{"row": i, "reason": "..."}]}` — a row with a
missing question/answer is skipped and reported, not fatal to the whole
batch (partial success, matching "no silent caps" — every skip is visible
in the response).

**Cap:** reuse the same size-limit precedent as the KB document upload
(`KB_MAX_UPLOAD_BYTES`, 10 MiB) for the CSV file itself — reject oversized
uploads with a 413, same as the existing `/kb/knowledge` upload route.

**Frontend:** new Chatwoot fork patch — add a "Bulk upload (CSV)" button
next to the existing "+ New entry" button on the native `KnowledgeFaqs.vue`
page, opening a simple file-picker + upload dialog, showing the
created/error counts from the response.

---

## 4. Round-robin ticket cap per agent

**Problem:** `RoutingService.pick_agent()`
(`backend/apps/backend/src/chatbot/features/routing/service.py`) has no
concept of "this agent already has too many open conversations, skip them
this round" — feedback-coverage doc #20, verified as a real gap in code.

**Design:** rather than trust an uncertain Chatwoot report endpoint, count
open-conversation load ourselves via the same conversations-listing
capability already used elsewhere in this codebase (`list_conversations`,
referenced in `CLAUDE.md`'s "New Chatwoot client verbs" for the lifecycle
scanner) — **the implementer must locate and reuse the EXISTING
`list_conversations`-style method** (grep for it in
`backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`
and/or `agent/app/clients/chatwoot.py` before writing new fetch logic; do
not invent a second HTTP client for this) to fetch open conversations and
count them per `assignee_id`. Add a new method to `PresenceFetcher`
(`backend/apps/backend/src/chatbot/features/routing/presence.py`):

```python
async def fetch_agent_open_counts(self) -> dict[int, int]:
    """agent_id -> count of open conversations currently assigned to them.
    Empty dict on any failure (fail-open — cap check becomes a no-op)."""
```

Implementation fetches `GET /conversations?status=open` (paginated, same
`_request` helper `PresenceFetcher` already owns), counts by
`meta.assignee.id` (or `assignee_id`, whichever the payload shape actually
uses — verify against a live response, same as `fetch_agents` already
does).

**Wiring into `pick_agent`:** new setting `routing_max_concurrent_per_agent:
int = 0` (0 = unlimited, byte-identical to today's behavior when unset).
When > 0, `pick_agent` fetches `fetch_agent_open_counts()` once per call
(same call-shape as the existing `fetch_agents()`/`list_all()` pair
already fetched every call — no new caching layer, matches existing
style) and excludes any agent whose count is `>= routing_max_concurrent_per_agent`
from ALL THREE tiers (first-priority, any-priority, idle-overflow) — same
exclusion shape as the existing `online` filter.

**Testing:** unit tests for `fetch_agent_open_counts` (mocked HTTP,
respx-style if that's the established pattern in this test file — check
`test_routing_*.py` for the actual mocking convention used) and for
`pick_agent`'s new exclusion behavior (agent at cap is skipped even though
they'd otherwise win tier 1; agent under cap still wins; cap of 0 is
byte-identical to before — a regression test using the exact existing test
cases from `pick_agent`'s current test file, asserting they still pass
unmodified when the cap is unset).

---

## 5. Customer 360 — foundational lookup (phone/vehicle-number keyed)

**Problem:** feedback-coverage doc #15 (raised 3 times in the client
meeting, the single most-repeated ask) — no way to look up a customer by
vehicle number or aggregate their history across channels beyond Chatwoot's
native per-contact "Previous Conversations" (which only works if the
customer used the same contact identity on every channel, and doesn't
surface vehicle info at all).

**Explicitly provisional:** this is NOT the Customer 360/DMS integration
the gap analysis describes as the platform's biggest gap — that needs real
DMS API access Proton hasn't provided, and the identifier-of-record
decision is explicitly Proton's call (#16). This track builds the piece
that's possible with data already inside the CRM: aggregate by phone
number (today's de facto identifier) and search by vehicle number where
it's already been captured as a conversation custom attribute
(`vehicle_model` from the reporting-metrics-extensions work) or an RSA
incident's `vehicle_no` field.

**Backend:** new feature `backend/apps/backend/src/chatbot/features/chat/customer360_router.py`
— `GET /admin/customer360/search?q=<phone-or-vehicle>` (gated by a new
`customer360.view` permission, same `require_permission` pattern). Search
logic: 
1. If `q` looks like a phone number (starts with `+` or is mostly digits),
   look up the Chatwoot contact by phone (reuse whatever contact-search
   capability already exists — check `adapters/chatwoot.py` for an
   existing contact-search method before adding a new one) and fetch all
   their conversations across every inbox.
2. Otherwise, treat `q` as a vehicle number: search the RSA incidents table
   (`RsaRepositoryPort`, already built) for a matching `vehicle_no`, AND
   search Chatwoot conversations for a matching `vehicle_model` custom
   attribute value (best-effort substring match — vehicle numbers may not
   be captured consistently everywhere yet, this is explicitly a
   first-pass, not a guaranteed match).
3. Response shape: `{"contact": {...} | null, "conversations": [{id,
   channel, status, created_at, vehicle_model, case_type}], "rsa_incidents":
   [...]}"` — every list is empty (not an error) when nothing matches, so the
   frontend can render a clean "no results" state rather than a broken one.

**Frontend:** new Chatwoot fork patch — standalone sidebar page "Customer
360" (RBAC-gated on `customer360.view`, same top-level-icon pattern), a
single search box, and a results view listing the matched conversations
(clickable through to the real conversation) and any RSA incidents. No
edit capability — this is a read-only lookup tool for track 1.

**Explicitly out of scope:** editing/merging contacts from this view (use
the existing native contact panel for that), any DMS write-back, and any
attempt to guess at what Proton's eventual chosen identifier will be beyond
"phone number, with vehicle number as a secondary best-effort search."

---

## Global constraints (apply to every track above)

- Every new capability defaults to its documented off/empty state and is
  byte-identical to today's behavior when unconfigured — same hard rule
  this whole project follows.
- New env vars go in both the consuming `config.py` and the relevant
  `example.env`.
- Background-task/webhook code never raises for "nothing to do" cases.
- Every new admin page follows the EXACT existing pattern (standalone
  sidebar icon, `require_permission`, `adminRequest()`) — no new auth
  mechanism invented.
- Full agent + backend test suites, plus a full local Docker image build
  (all fork patches), must pass before any VM deploy step.
- Commit after each track's tasks complete (not one giant commit at the
  end) — this is a long autonomous run; frequent commits are the recovery
  points if anything needs to be rolled back.
