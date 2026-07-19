# Knowledge → Inboxes — Design

**Status:** approved design (2026-07-19). One of four editable Knowledge sub-pages.
**Ships as fork patch `0015` + a new `inbox_modes` store & router.** Most
cross-cutting of the four (touches the assist/copilot/auto-reply gating).

## Summary
Control the assistant per Chatwoot inbox. Each inbox gets a **mode** — `off`,
`suggest`, or `auto` — replacing today's global on/off. The assist/copilot/
auto-reply paths consult the effective mode for the inbox in play; the dashboard
UI gates the ✨ / Ask-Copilot affordances on the current inbox's mode.

## Decision (from brainstorm) — "Mode: off / suggest / auto"
- `off` — no AI on that inbox.
- `suggest` — AI drafts a **private note** for the human agent.
- `auto` — AI replies directly.
- Unset inbox ⇒ inherit the global **default_mode** from the Settings store.

## Shared foundations
- **Store pattern:** `InboxModeStorePort` + `InMemoryInboxModeStore` +
  `FirestoreInboxModeStore` (mirror `live_faq.py`), `build_inbox_mode_store(settings)`.
  Firestore collection `inbox_modes`, one doc per `inbox_id` → `{ mode }`.
- **Effective mode facade:** `effective_mode(inbox_id)` = store value else Settings
  `default_mode` (reuses the Settings spec's config facade).
- **Frontend:** `components/proton/KnowledgeInboxes.vue` + `api/protonKnowledge.js`
  + host wiring `section === 'inboxes'`. Patch `0015`.

## Backend — `kb_inboxes_router.py`
- `GET /kb/inboxes` → list of `{ inbox_id, name, channel_type, mode, mode_source }`
  by fetching inboxes from the existing **Chatwoot client**
  (`src/chatbot/features/chat/adapters/chatwoot.py` — `GET /api/v1/accounts/{id}/inboxes`)
  and joining stored modes; `mode_source: "override"|"default"`. Never raises —
  if Chatwoot is unreachable, return stored modes only (or `[]`). Auth `x-api-key`.
- `PUT /kb/inboxes/{inbox_id}` → `{ mode }` (enum-validated); clearing reverts to
  default. Wire into `main.py` + `run_assist_local.py`.

## Gating integration (the cross-cutting part)
Where the assistant acts, resolve `effective_mode(inbox_id)` and branch:
- **Assist/copilot endpoints** (`/assist/*`, `/assist/copilot`): the request carries
  a `conversation_id`; resolve its inbox (via Chatwoot context already fetched in
  `chatwoot_context.py`) and **refuse (or no-op) when mode is `off`**. Copilot/assist
  are agent-invoked, so `off` should hide them client-side too.
- **Auto-reply / agent-bot** path: the incoming-message flow chooses suggest vs auto
  by `effective_mode(inbox_id)` instead of the global `AGENT_MODE`. NOTE: the
  agent-bot orchestrator lives in the **`agent/` service of `id-crm-ticketing`**, not
  proton-conversational-ai. v1 scope: implement the mode store + gating for the
  **assist/copilot** paths here; the agent-bot's per-inbox mode is a **follow-up**
  that reads the same store (documented, not built in this patch). Flag clearly.
- **Frontend gating:** `KnowledgeInboxes.vue` edits modes; separately, the ✨/Ask
  affordances (patches 0002/0005) should consult the current inbox's mode. v1: add a
  lightweight `GET /kb/inboxes/{id}` (or reuse the list) so `ReplyTopPanel`/
  `ConversationView` can hide AI when `off`. If that wiring is deferred, the backend
  still enforces `off`, so behaviour is correct even if the button is briefly visible.

## Data model (`inbox_modes/{inbox_id}`)
```json
{ "mode": "suggest" }   // one of off|suggest|auto
```

## Frontend — `KnowledgeInboxes.vue`
A table: Inbox / Channel / Mode (a 3-way select off·suggest·auto) / source badge
(override vs default). Change → `PUT /kb/inboxes/{id}` → `useAlert`. A header note
states unset inboxes inherit the global default (link to Settings).

## Testing
- `test_kb_inboxes_router.py`: GET joins mocked Chatwoot inbox list with stored
  modes + correct `mode_source`; Chatwoot-down → degrades; PUT validates enum +
  persists; auth. InMemory store; Chatwoot client mocked.
- `test_mode_gating.py`: `off` inbox → assist/copilot refuses/no-ops; `suggest` vs
  `auto` selects the right branch; unset → default_mode.
- Frontend: vite compile + smoke (set an inbox to `off`, confirm assist refuses).

## Out of scope / risks
- **Split-service gating:** the agent-bot auto-reply lives in the other service; v1
  wires assist/copilot here and documents the follow-up for the agent-bot to read
  the same store. Clear boundary, called out above.
- Inbox list depends on Chatwoot reachability — degrade, don't fail.
- No per-inbox model/tool overrides in v1 (mode only).
