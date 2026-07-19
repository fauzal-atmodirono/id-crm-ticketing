# Knowledge → Inboxes — Design (assistant↔inbox assignment + mode)

**Status:** approved design (2026-07-19). REVISED for the Captain-parity program
(see `…-captain-parity-program.md`). Combines Captain's assistant↔inbox routing
with our off/suggest/auto mode. Built last (most cross-cutting). Ships as fork
patch `0017`.

## Summary
Assign each Chatwoot inbox to an **assistant** and a **mode** (`off`/`suggest`/
`auto`). Captain links one assistant per inbox (`captain_inbox.rb`); we add the
3-way mode. The assist/copilot paths consult the assignment; the dashboard gates
the ✨/Ask-Copilot affordances on the current inbox's mode.

## Decision (from brainstorm + program)
- Per inbox: `{ assistant_id, mode }`, mode ∈ `off|suggest|auto`.
- `off` = no AI; `suggest` = AI drafts a private note; `auto` = AI replies directly.
- Unset inbox ⇒ `assistant_id = default assistant`, `mode = tenant default_mode`
  (from the settings spec's `tenant_settings`).

## Backend

### Store — `inbox_assignment_store.py`
`InboxAssignmentStorePort` + `InMemory*` + `Firestore*` (mirror `live_faq.py`),
`build_inbox_assignment_store(settings)`. Firestore collection `inbox_assignments`,
one doc per `inbox_id` → `{ assistant_id, mode }`. Never raises on read.
`delete_for_assistant(assistant_id)` clears assignments when an assistant is deleted
(cascade from the assistants spec).

### Effective resolver
`effective_assignment(inbox_id) -> {assistant_id, mode, source}` = store value else
`{default_assistant, tenant default_mode, "default"}`. Reuses the settings facade.

### Router — `kb_inboxes_router.py`
Auth `x-api-key`. Wire into `main.py` + `run_assist_local.py`.
- `GET /kb/inboxes` → for each Chatwoot inbox (fetched via the existing
  `ChatwootAdapter`, `GET /api/v1/accounts/{id}/inboxes`) joined with assignments:
  `{ inbox_id, name, channel_type, assistant_id, assistant_name, mode, source }`.
  Chatwoot unreachable → return stored assignments only (never raise).
- `PUT /kb/inboxes/{inbox_id}` → `{ assistant_id, mode }` (validate assistant
  exists + mode enum); clearing reverts to default.

### Gating integration (the cross-cutting part)
- **assist/copilot endpoints:** resolve the request's inbox (via `chatwoot_context`)
  → `effective_assignment`; if `mode == off`, refuse/no-op; otherwise use the
  assigned `assistant_id` when the request didn't specify one.
- **auto-reply agent-bot:** lives in the separate **`id-crm-ticketing agent/`**
  service. v1 builds the store + gating for assist/copilot here; the agent-bot
  choosing suggest vs auto from this store (per inbox) is a documented **follow-up**
  (it reads the same collection). Clear boundary — do NOT implement agent-bot
  changes in this patch.
- **frontend gating:** `ReplyTopPanel`/`ConversationView` (patches 0002/0005) should
  hide ✨/Ask when the current inbox is `off`. v1 exposes it via `GET /kb/inboxes`
  (or a per-inbox lookup); if that wiring is deferred, the backend still enforces
  `off`, so behavior is correct even if a button briefly shows.

## Frontend — `KnowledgeInboxes.vue`
Table: Inbox / Channel / **Assistant** (dropdown of assistants) / **Mode**
(off·suggest·auto select) / source badge. Change → `PUT /kb/inboxes/{id}` →
`useAlert`. Header note: unset inboxes inherit the default assistant + default mode
(link to Settings/Assistants). API helpers `listInboxes`, `setInbox` in
`protonKnowledge.js`. Host wiring `section === 'inboxes'`.

## Testing
- `test_kb_inboxes_router.py`: GET joins mocked Chatwoot inbox list with stored
  assignments + `source`; Chatwoot-down degrades; PUT validates assistant+mode enum
  + persists; auth. InMemory store; Chatwoot client mocked.
- `test_mode_gating.py`: `off` inbox → assist/copilot refuses/no-ops; assigned
  assistant used when request omits `assistant_id`; unset → default assistant +
  default_mode; cascade delete clears assignments.
- Frontend: vite compile + smoke (assign an inbox to an assistant, set `off`,
  confirm assist refuses).

## Out of scope / risks
- **Split-service gating:** agent-bot auto-reply lives in the other service; v1
  wires assist/copilot + documents the agent-bot follow-up.
- Inbox list depends on Chatwoot reachability — degrade, don't fail.
- One assistant per inbox (Captain parity); no multi-assistant-per-inbox in v1.
