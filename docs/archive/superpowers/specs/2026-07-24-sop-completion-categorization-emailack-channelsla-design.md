# SOP Completion: Categorization, Email Auto-Ack & Channel Ack-SLAs — Design

**Date:** 2026-07-24
**Status:** Approved (design), pending implementation plan
**Services:** `agent/` (B, C1) and `backend/apps/backend` (C2)
**Source requirement:** Proton CRM Process Flow workbook (`docs/CRM Process Flow (1).xlsx`)

## Purpose

Completes the remaining Proton process-flow SOP items not covered by the
Conversation Lifecycle & Auto-Close feature
(`2026-07-23-conversation-lifecycle-autoclose-design.md`). Three cohesive
sub-features:

- **B — Bot auto-categorization**: when the bot resolves a conversation, assign
  the appropriate case category label (workbook WhatsApp sheet: "If a ticket is
  resolved by the bot, the bot must assign the appropriate case
  category/division"). This concretely implements the previously-deferred
  Task 11 of the lifecycle plan.
- **C1 — Email once-per-thread auto-acknowledgement**: send the SOP email
  auto-reply once per new email/ticket, never on replies in the same thread and
  never on agent replies (workbook Email sheet).
- **C2 — Channel-specific agent-acknowledgement SLAs**: 2 min (WhatsApp/Call),
  2 h (Social), 4 h (Email), 20 s (Call) first-response thresholds (workbook
  across sheets).

All three ship **off by default**, are **fail-open**, and are TDD'd.

## Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| B categorizer | Lightweight Gemini classify in `agent/` | Self-contained where the scanner lives; reuses the existing `app/ai/gemini.py` client; picks one category from a configured taxonomy. |
| C1 email auto-ack home | Custom, event-driven in `agent/` | Matches the SOP's precise once-per-thread + skip-agent-reply rules, which Chatwoot's native inbox auto-reply cannot express. |
| C1 dedup | Structural (per-conversation) | Chatwoot fires `conversation_created` once per conversation; `seed_active` only acts on a new row — so thread replies and agent replies never re-trigger, and a new email (new conversation) acks again. No new state. |
| C2 ack-SLA home | Extend backend `sla.py` first-response check | Reuses the existing scan loop, audit-trail dedup, and WhatsApp alert path; adds a per-channel override in minutes for sub-hour thresholds. |

## Component B — Bot auto-categorization (`agent/`)

### New: `agent/app/services/categorize.py`

```python
async def classify_category(transcript: str, candidates: list[str]) -> str | None
```

- One Gemini call (run via `asyncio.to_thread`, since the agent's
  `app/ai/gemini.py` SDK call is sync) that returns exactly one slug from
  `candidates`, or `None`.
- Prompt: instruct the model to pick the single best-fitting category slug from
  the provided list for the given transcript, replying with only the slug;
  anything not in the list → `None`.
- Fail-open: any error (SDK, timeout, unparseable) → `None`.

### New Chatwoot client method: `add_labels(conversation_id, labels)`

Chatwoot's conversation-labels endpoint (`POST
.../conversations/{id}/labels {"labels":[...]}`) **replaces** the full set, so
to add without clobbering we must merge:

```python
async def add_labels(self, conversation_id: int, labels: list[str]) -> Any:
    # GET current labels, POST the union (dedup-preserving)
```

- GET `.../conversations/{id}/labels` → current list; POST the union of current
  + new. Empty/failed GET → POST just the new labels (fail-open at the caller).

### Wiring

- Gated by the existing `LIFECYCLE_AUTO_CATEGORIZE` (default `false`, already in
  config + `example.env` from the lifecycle feature).
- Candidate taxonomy from a new env `LIFECYCLE_CATEGORY_LABELS` (comma-separated
  slugs; documented default set — must match the tenant's deployed Phase-1
  taxonomy; empty → feature no-ops).
- Applied in the scanner's close paths (`close` and `resolve_timeout`) and in the
  lifecycle YES-resolution close, **after** the CLOSED transition: fetch the
  transcript (reuse the orchestrator's message-fetch or a lightweight
  equivalent), `classify_category`, then `add_labels(conversation_id,
  [f"category_{slug}"])`. All fail-open — a classify/label error never blocks the
  close.

## Component C1 — Email once-per-thread auto-ack (`agent/`)

### Wiring in `on_conversation_created` (lifecycle.py)

- Branch on the inbox channel:
  - **Email inbox** (`Channel::Email`) + `EMAIL_AUTOACK_ENABLED` → post the SOP
    email auto-ack template (verbatim, below) once, publicly.
  - **Chat inbox** → existing AI disclaimer path (unchanged).
- Channel resolved from the conversation payload's inbox (reuse `get_inbox`
  channel lookup, cached/fail-open).
- **Dedup is structural**: `conversation_created` fires once per conversation and
  `seed_active` returns early if a row already exists, so the auto-ack posts at
  most once per conversation. A reply in the same thread is the same
  conversation → no new event → no re-ack; an agent reply is outgoing → no new
  conversation; a new email → new conversation → acks again. No extra flag.

### New config

- `EMAIL_AUTOACK_ENABLED: bool = False`.
- `EMAIL_AUTOACK_TEMPLATE: str = <SOP default>` (overridable). Default (verbatim
  from the workbook Email sheet):

  > Dear Customer,
  > Thank you for your email. This message serves to acknowledge receipt of your enquiry.
  > We will respond within one (1) business day during our operating hours.
  > For urgent matters, please contact our Call Centre at 1300 888 877.
  > Operating Hours:
  > Monday–Friday: 8:30 AM – 5:30 PM
  > Saturday, Sunday & Public Holidays: 9:00 AM – 5:00 PM
  > Thank you for your patience and understanding.
  >
  > Warm regards,
  > Proton e.MAS Centre

## Component C2 — Channel-specific ack SLAs (`backend/apps/backend`)

### Extend `features/chat/sla.py::scan_conversations`

- The `NO_RESPONSE_BREACH` (first-response/ack) check currently uses the global
  `settings.sla_response_hours`. Add a per-channel override, keyed on the
  conversation's channel, expressed in **minutes** (to support sub-hour
  thresholds like 2 min and 20 s):

  ```python
  # new config
  sla_ack_minutes_by_channel_json: str = ""   # e.g. {"whatsapp":2,"call":2,"facebook":120,"instagram":120,"email":240}
  ```

- Parse once into a `{channel: minutes}` map (invalid/empty JSON → empty map →
  global default everywhere, behavior-preserving).
- For each conversation, resolve its channel (from its inbox; cached inbox→channel
  lookup within the scan, fail-open to the global default) and, if the channel
  has an override, use `override_minutes * 60` seconds as the ack threshold
  instead of `sla_response_hours * 3600`. Everything else — the
  `first_reply_created_at` derivation, audit-trail dedup, WhatsApp alert
  callback — is unchanged.
- 20 s (Call) is expressible as a fractional minute (`0.333`) or the map may use
  a small integer; parsing accepts floats. Document the 20 s Call case using a
  fractional value.

### Channel normalization

- Map Chatwoot `channel_type` (`Channel::Whatsapp`, `Channel::TwilioSms`,
  `Channel::FacebookPage`, `Channel::Instagram`, `Channel::Email`, voice/call, …)
  to the short keys used in the JSON (`whatsapp`, `call`, `facebook`,
  `instagram`, `email`). Unknown channel → no override → global default.

## Cross-cutting

- **Off by default:** `LIFECYCLE_AUTO_CATEGORIZE=false`,
  `EMAIL_AUTOACK_ENABLED=false`, empty `sla_ack_minutes_by_channel_json` → each
  sub-feature is a no-op and the existing suites are byte-identical.
- **Fail-open:** classify/label/auto-ack/channel-resolution errors are logged and
  skipped; they never break the close, the webhook, or the SLA scan.
- **Config truth:** new agent env in `agent/app/config.py` + `deploy/tenants/example.env`;
  new backend env in the backend `config.py` + `.env.example` + folded into
  `deploy/tenants/example.env`.
- **Testing:** B + C1 in the agent suite (`cd agent && pytest`, sqlite + respx);
  C2 in the backend suite (`cd backend/apps/backend && pytest`, needs
  `GOOGLE_API_KEY` set at collection — known upstream quirk).

## SOP coverage traceability

| SOP requirement | Covered by |
|---|---|
| Bot assigns case category on resolution | B (classify + add_labels on close) |
| Email auto-ack once per new email/ticket | C1 (`on_conversation_created` email branch) |
| Email auto-ack NOT on thread replies / agent replies | C1 (structural per-conversation dedup) |
| First-response ack ≤ 2 min (WhatsApp/Call) | C2 (per-channel override) |
| First-response ack ≤ 2 h (Social) | C2 |
| First-response ack ≤ 4 h (Email) | C2 |
| First-response ack ≤ 20 s (Call) | C2 (fractional-minute override) |

## Out of scope (future)

- IVR/telephony call flow (Phase 7 plan exists, set aside) — C2 only sets the
  ack-SLA thresholds for the call channel; it does not build IVR.
- SSI dealer-survey process (external to the CRM).
- Escalation-on-ack-breach routing changes beyond the existing WhatsApp alert
  (the alert path is reused as-is).
