# Conversation Lifecycle & Auto-Close — Design

**Date:** 2026-07-23
**Status:** Approved (design), pending implementation plan
**Service:** `agent/` (first-party Chatwoot sync/orchestration service)
**Source requirement:** Proton CRM Process Flow workbook (`docs/CRM Process Flow (1).xlsx`)

## Purpose

The Proton process-flow SOP describes how a conversation should behave end-to-end
across channels (WhatsApp, Social, Email, IVR). The AI / knowledge / routing /
escalation / reporting / NPS backbone already exists, but the **connective tissue
that actually runs the SOP** does not. This feature adds that tissue: the
conversation lifecycle state machine that gates on business hours, detects idle,
auto-closes, confirms resolution, and triggers rating surveys.

Scope is the "🔴 cluster" identified in the gap analysis — five sub-features
delivered as one cohesive state machine:

1. Business-hours gating (reads Chatwoot's native per-inbox working hours).
2. Idle detection → warning → auto-close.
3. Resolution confirmation ("Is your case resolved? YES / NO").
4. Rating survey (AI-performance and agent-performance variants).
5. AI disclaimer / welcome message on conversation-created (finally wires the
   long-dormant `welcome_message` persona field).

**Explicitly out of scope** (the "🟡 cluster" — separate future spec): email
once-per-thread auto-acknowledgement dedup, and channel-specific agent
acknowledgement SLA thresholds (2min / 2h / 4h / 20s). IVR/telephony (Phase 7)
and the SSI dealer-survey process remain separate.

## Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scanner home | New async scheduler in `agent/` | Keeps all conversation-lifecycle logic in one service, next to the orchestrator that owns Chatwoot conversation state. |
| Business hours | Reuse Chatwoot **native** per-inbox working hours + native out-of-office auto-reply; our code only **reads** in-hours status | Least custom code, tenant-configurable in the Chatwoot UI, no duplicate feature. The out-of-office auto-reply is posted by Chatwoot itself (ops config, not code). |
| Survey mechanism | Custom in-conversation flow (post prompts, parse replies, record via existing backend NPS/CSAT) | Matches the SOP exactly: YES/NO gate → AI-vs-agent survey split → unresolved→reassign branch, which native Chatwoot CSAT cannot express. |
| Lifecycle state storage | New `conversation_lifecycle` DB table in `agent/` **+** mirror to a `lifecycle_state` Chatwoot custom attribute | DB gives transactional dedup via a unique constraint (matches the `conversation_links` / `processed_deliveries` idempotency pattern; no Alembic — `create_all`). The mirrored custom attribute makes state visible to agents in the Chatwoot panel (like the existing `case_state`). |
| Feature gating | `LIFECYCLE_ENABLED` master switch, default **off** | When off, behavior is byte-identical to today. Fail-open throughout. |

## State machine

A per-conversation lifecycle state, owned by `agent/`, layered on top of
Chatwoot's own `pending` / `open` / `resolved` status.

```
                    conversation_created (webhook)
                              │
                              ▼
   ┌──────────┐   post disclaimer + welcome_message (once)
   │  ACTIVE  │◄──────────────────────────────────────────┐
   └────┬─────┘                                            │
        │ scanner: idle > IDLE_WARN (10m)                  │ customer replies
        ▼                                                  │ (activity resets)
   ┌──────────────┐  post "chat closes in 5 min"           │
   │ IDLE_WARNED  │────────────────────────────────────────┘
   └──────┬───────┘
          │ scanner: idle > close threshold
          │ post "Closed due to inactivity" + "Is your case resolved? YES / NO"
          ▼
   ┌──────────────────────┐
   │ AWAITING_RESOLUTION   │
   └──────┬────────┬───────┘
     "YES"│        │"NO"                 scanner: no reply in CONFIRM_GRACE
          │        │                          │ → treat as resolved
          │        ▼                          │
          │   escalate/handoff to agent       │
          │   (if out-of-hours: tag           │
          │    "next-business-hour")          │
          │   → back to ACTIVE (agent-owned)  │
          ▼                                   ▼
   ┌──────────────────┐  post rating survey ("Rate our AI 1–5")
   │ AWAITING_SURVEY   │  (variant: AI-perf vs agent-perf)
   └────────┬─────────┘
            │ customer replies with rating → record via backend NPS/CSAT
            ▼
        ┌────────┐  toggle conversation → resolved
        │ CLOSED │  (+ optional bot auto-categorization label)
        └────────┘
```

### States

- `ACTIVE` — normal bot/agent handling; idle clock runs from `last_activity_at`.
- `IDLE_WARNED` — idle warning posted; awaiting the close grace period.
- `AWAITING_RESOLUTION` — auto-close message + YES/NO prompt posted; awaiting reply.
- `AWAITING_SURVEY` — rating survey prompt posted; awaiting a numeric rating.
- `CLOSED` — conversation resolved; lifecycle terminal.

### Two survey variants, two triggers

1. **AI-performance** — from the idle → auto-close → "YES" path (bot handled it).
2. **Agent-performance** — when a human resolves the conversation
   (Chatwoot `conversation_status_changed → resolved`, or a Zammad ticket close
   via the existing `sync.on_ticket_event` path).

### Survey / confirmation reply capture (key structural change)

The orchestrator's message handler gets a **pre-check before its `pending`-only
eligibility filter**: if the conversation's lifecycle state is
`AWAITING_RESOLUTION` or `AWAITING_SURVEY`, the incoming customer message is
routed to the lifecycle reply-parser (YES/NO or rating) instead of Gemini. This
is what lets us capture replies on `open` / `resolved` conversations, where the
normal bot flow would ignore them.

### Channel scoping

Idle warn/close applies **only** to chat-like inboxes (WhatsApp, web, FB/IG) in
the **bot phase** (no agent assigned). Email is excluded — it is async by nature
and the SOP email flow has no idle-close. Channel is resolved via the inbox,
reusing the existing `_resolve_conv_channel` helper. Once an agent is assigned,
idle auto-close is suppressed (the agent / team-leader owns the conversation).

## Components

All new code lives in `agent/` except survey recording, which reuses the backend
over HTTP. Everything is additive and fail-open — a lifecycle error never breaks
the bot or a webhook.

### 1. `agent/app/services/lifecycle_scanner.py` (new)

- Asyncio loop started in `app/main.py`'s lifespan (async-native; no new
  dependency, unlike the backend's APScheduler). Runs every
  `LIFECYCLE_SCAN_INTERVAL_SECONDS` (default 60).
- Each tick: list open/pending conversations via the new client method → for each
  chat-like, bot-phase conversation, compute idle from `last_activity_at` and
  drive `ACTIVE → IDLE_WARNED → AWAITING_RESOLUTION → CLOSED`.
- Owns its own DB session (background-task invariant). Dedup is structural: a
  transition fires only if the row's current state + timestamps warrant it
  (mirrors the SLA scan's audit-dedup, but via our table's row guard).
- Gated by `LIFECYCLE_ENABLED`.

### 2. `agent/app/services/lifecycle.py` (new)

- Transition logic + message authoring (warning, close, YES/NO prompt, survey
  prompt). Templates default to the SOP verbatim text, overridable via backend
  persona messages where they exist (`welcome_message` is finally wired here).
- `handle_lifecycle_reply(conversation, message, state)` — YES/NO and rating
  parser called from the orchestrator pre-check.
- `on_conversation_created(...)` — posts disclaimer + welcome once, seeds the row
  as `ACTIVE`.
- `on_human_resolved(...)` — triggers the agent-performance survey.
- Records ratings by calling the backend (`ProtonConfigClient.record_survey` →
  backend NPS/CSAT endpoint), reusing existing scoring — no new store.

### 3. `agent/app/db/models.py` — `ConversationLifecycle` (new table)

Columns: `conversation_id` (unique / PK), `state`, `channel`, `warned_at`,
`state_changed_at`, `survey_variant`, `created_at`. Created via the existing
`Base.metadata.create_all` in `init_db` (no Alembic, matches the repo).

### 4. `agent/app/services/orchestrator.py` (changes)

- `handle_bot_event`: add the lifecycle pre-check **before** the
  `status == pending` filter → route `AWAITING_*` conversations to
  `lifecycle.handle_lifecycle_reply`.
- On a normal customer reply, reset lifecycle `IDLE_WARNED → ACTIVE` (activity
  resets the idle clock).
- `SYSTEM_PROMPT`: add a "reply in the customer's language" instruction (covers
  the same-language SOP requirement).

### 5. Router changes — `agent/app/routers/chatwoot.py`

Add two handlers following the verify → dedupe → 200-fast → dispatch shape:
- `conversation_created` → `lifecycle.on_conversation_created` (background task).
- `conversation_status_changed → resolved` (by a human) →
  `lifecycle.on_human_resolved` (background task).

### 6. Chatwoot client additions — `agent/app/clients/chatwoot.py`

- `list_conversations(status=..., assignee_type=...)` — the scanner needs it
  (does not exist today).
- `get_inbox(inbox_id)` — reads native `working_hours` / `timezone` /
  `working_hours_enabled`.
- `set_custom_attributes(conversation_id, {...})` — mirrors `lifecycle_state`.

### 7. `agent/app/services/business_hours.py` (new)

- `is_within_business_hours(inbox)` — evaluates Chatwoot's native `working_hours`
  array against now-in-inbox-timezone. Used by the scanner to (a) select the
  close threshold (out-of-hours vs in-hours) and (b) tag unresolved-NO cases
  `next-business-hour` for assignment. The out-of-office auto-reply stays native
  (Chatwoot posts it) — enabling it per inbox is an ops step, not code.

### 8. Optional — bot auto-categorization

On bot auto-close, apply a `category_*` label via a lightweight Gemini classify
(or reuse the existing handoff classification). Gated by
`LIFECYCLE_AUTO_CATEGORIZE` (default off) and sequenced **last** in the plan so it
never blocks the core.

## Configuration

New env vars in `agent/app/config.py` + `deploy/tenants/example.env` (per the
config-truth convention). Defaults keep behavior identical when the feature is off.

| Var | Default | Purpose |
|---|---|---|
| `LIFECYCLE_ENABLED` | `false` | Master switch — off = byte-identical to today |
| `LIFECYCLE_SCAN_INTERVAL_SECONDS` | `60` | Scanner tick |
| `LIFECYCLE_IDLE_WARN_MINUTES` | `10` | Idle → warning |
| `LIFECYCLE_IDLE_CLOSE_GRACE_MINUTES` | `5` | In-hours grace after warning → close (10 + 5 = 15m total) |
| `LIFECYCLE_IDLE_CLOSE_OUT_OF_HOURS_GRACE_MINUTES` | `0` | Out-of-hours grace after warning → close (10 + 0 = 10m total) |

**Close threshold is always `IDLE_WARN + selected grace`, so the warning always
fires first.** The grace is selected by business hours: in-hours default 5m
(→ 15m total) and out-of-hours default 0m (→ close on the next tick after the
warning, ≈ 10m total). This reconciles the workbook's "15 min in-hours / 10 min
out-of-hours" close numbers with its always-first "closes in 5 minutes" warning.
| `LIFECYCLE_CONFIRM_GRACE_MINUTES` | `10` | No YES/NO reply → treat resolved |
| `LIFECYCLE_SURVEY_ENABLED` | `true` | Gate the rating survey step |
| `LIFECYCLE_DISCLAIMER_ENABLED` | `true` | Gate the AI disclaimer/welcome on conversation-created |
| `LIFECYCLE_AUTO_CATEGORIZE` | `false` | Optional bot auto-categorization on close |

SOP message templates (disclaimer, warning, close, YES/NO, survey) ship as
hardcoded defaults sourced verbatim from the workbook, overridable via backend
persona messages where those exist.

## Testing

Follows the repo's existing pattern: `pytest`, `asyncio_mode=auto`, sqlite via
aiosqlite, `respx` for HTTP stubs, injected Gemini — no real Chatwoot / Zammad /
Gemini calls.

- **Pure transition tests** for `lifecycle.py`: every edge of the state machine
  (idle → warn → close → confirm → survey → closed; activity-reset; YES vs NO;
  rating parse; timeout-resolve).
- **Scanner tests** with a fake clock + stubbed `list_conversations`: the right
  transition fires at each threshold and is **idempotent** across ticks
  (re-scanning does not double-post).
- **Orchestrator pre-check tests**: `AWAITING_*` routes to the lifecycle parser,
  not Gemini; normal messages still reach Gemini.
- **Channel-scoping test**: an email inbox is skipped by idle logic.
- **Business-hours tests**: in/out-of-hours evaluation against a sample Chatwoot
  `working_hours` payload; close-threshold selection.

## Fail-open & edge cases

- Every lifecycle background task and scanner tick swallows expected errors
  (missing inbox, HTTP failures, unknown ids) → log + skip, never raise
  (background-task invariant). A dead backend means no survey recording, not a
  broken bot.
- **Race with the debounce/Gemini flow:** a customer message mid-scan resets the
  lifecycle to `ACTIVE`; the scanner's transition is row-guarded so a warning
  won't post after activity resumed.
- **Agent takes over:** once an agent is assigned, idle auto-close is suppressed
  (bot-phase only) — the agent / team-leader owns it.
- **Late reply to a CLOSED/resolved survey conversation:** re-entry seeds a fresh
  `ACTIVE` row (new lifecycle), matching Chatwoot's reopening semantics.
- **Duplicate scanner ticks / multiple workers:** the unique `conversation_id`
  row + state check is the concurrency guard (belt-and-suspenders like the sync
  mapping tables).

## SOP coverage traceability

| SOP requirement (workbook) | Covered by |
|---|---|
| AI disclaimer on first contact | `on_conversation_created` + `LIFECYCLE_DISCLAIMER_ENABLED` |
| Bot replies in customer's language | `SYSTEM_PROMPT` instruction |
| Out-of-hours auto-reply | Native Chatwoot out-of-office (ops config) |
| Idle 10-min warning ("closes in 5 min") | Scanner `ACTIVE → IDLE_WARNED` |
| Auto-close after idle (10m out / 15m in-hours) | Scanner `IDLE_WARNED → AWAITING_RESOLUTION`; close = `IDLE_WARN + business-hours-selected grace` |
| "Is your case resolved? YES/NO" | `AWAITING_RESOLUTION` prompt + reply parser |
| Rating survey (AI performance) | AI-perf survey variant |
| Rating survey (agent performance) | Agent-perf survey variant via `on_human_resolved` |
| Unresolved → assign next business hour | "NO" branch + `next-business-hour` tag |
| Bot assigns case category on resolution | Optional auto-categorization (`LIFECYCLE_AUTO_CATEGORIZE`) |

## Out of scope (future specs)

- Email once-per-thread auto-acknowledgement dedup (🟡).
- Channel-specific ack SLAs: 2min (WA/Call), 2h (Social), 4h (Email), 20s (Call) (🟡).
- IVR / telephony (Phase 7).
- SSI dealer-survey process (external to the CRM).
