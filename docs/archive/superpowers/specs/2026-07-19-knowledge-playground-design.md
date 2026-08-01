# Knowledge → Playground — Design (per-assistant)

**Status:** approved design (2026-07-19). REVISED for the Captain-parity program
(see `…-captain-parity-program.md`) — now assistant-scoped. Built after Settings.
Ships as fork patch `0014`.

## Summary
A standalone sandbox to test a **selected assistant** outside any real
conversation: see how it answers, which tools it fires, and what it cites. Mirrors
Captain's `AssistantPlayground.vue` + `POST /captain/assistants/{id}/playground`.
No new store; reuses `POST /assist/copilot`.

## Decision (from brainstorm + program)
- Reuse `POST /assist/copilot` with a **synthetic `conversation_id: "playground"`**
  and the selected **`assistant_id`** (from `useKnowledgeAssistant`). The assistant
  resolver applies that assistant's persona + tools; grounding is the shared KB.
- Conversation-context tools (customer/past-tickets/memory) are inert without a
  real conversation; the UI notes this. Optional "Advanced → simulate conversation
  id" field to exercise context tools. (Approved.)

## Backend
No new endpoint. `/assist/copilot` already returns `{ answer, tool_calls, sources }`
and (per the assistants spec) now accepts `assistant_id`. Confirm during
implementation that `conversation_id: "playground"` (non-numeric) does not raise in
`chatwoot_context.py`; guard/short-circuit context fetch for it if needed (small).
Captain shows `agent_name` for scenario delegation; we have no V2 delegation, so we
surface `tool_calls` + `sources` as the equivalent transparency.

## Frontend — `KnowledgePlayground.vue`
- Header: shared **AssistantSelector** (switching assistant resets the thread).
- Chat like `components/copilot/ProtonCopilotPanel.vue` (multi-turn, "Thinking…",
  per-message `tool_calls` "Looked at: …" + clickable `sources`), full-width, not a
  sidepanel. **Reset** button. Header note "Sandbox — not tied to a conversation".
- Optional collapsible "Advanced → simulate conversation id" (default `playground`).
- No "Insert into reply" (no editor here).
- API helper `askPlayground(assistantId, thread, conversationId)` in
  `protonKnowledge.js` — thin wrapper over `/assist/copilot`.
- Host wiring `section === 'playground'`.

## Data flow
UI thread + selected `assistant_id` → `askPlayground` → `/assist/copilot`
(x-api-key) → render answer + tools + sources. Stateless; nothing persisted.

## Testing
- Backend: existing copilot tests + one asserting non-numeric `conversation_id`
  returns 200 with empty context (guard if needed) and that `assistant_id` selects
  the right persona (covered jointly with assistants spec).
- Frontend: vite compile + smoke (pick an assistant, ask a KB question, see a
  grounded answer + `search_knowledge_base` in tools + a source link; switch
  assistant → different persona/behavior).

## Out of scope / risks
- No saved/replayable sessions; no prompt editing here (that's Settings).
- Playground calls cost Gemini tokens like any copilot call — admin tool, no
  rate-limit in v1 (note for later).
