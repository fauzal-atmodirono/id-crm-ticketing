# Knowledge → Playground — Design

**Status:** approved design (2026-07-19). One of four editable Knowledge sub-pages
(see also settings / tools / inboxes specs). **Ships as fork patch `0012`.**

## Summary
A standalone "copilot sandbox": a chat surface where an admin/agent can ask the
assistant questions **outside any real conversation**, to see how it answers,
which tools it fires, and what knowledge it cites. It is a test harness for the
KB + copilot, not a customer-facing feature.

## Decision (from brainstorm)
- Reuse the existing `POST /assist/copilot` endpoint — **no new backend**.
- Send a **synthetic `conversation_id: "playground"`**. Conversation-context
  tools (customer lookup, past tickets) return nothing useful without a real
  conversation; the UI notes this. An **optional** "simulate conversation id"
  field lets a power user paste a real Chatwoot conversation id to exercise the
  context tools. (Assumption approved.)

## Shared foundations (common to all four pages)
- **Frontend pattern:** native Vue view `app/javascript/dashboard/components/proton/KnowledgePlayground.vue`
  using Chatwoot `n-*` design tokens + `useAlert`; fetch helper in
  `app/javascript/dashboard/api/protonKnowledge.js`; wired into
  `views/ProtonKnowledgeHost.vue` by `section === 'playground'`.
- **Patch pipeline:** author in the upstream clone with `0001–0011` applied,
  `git diff` → `deploy/chatwoot-fork/patches/0012-knowledge-playground.patch`,
  rebuild image (vite compile gate), redeploy, browser smoke.

## Backend
None new. Contract already provided by `src/chatbot/features/assist/copilot_router.py`:
`POST /assist/copilot` with `{ conversation_id: str, thread: [{role, content}] }`
→ `{ answer, tool_calls: [str], sources: [{title, url}] }`. Auth `x-api-key`.

The `/assist/copilot` handler already tolerates a conversation id that has no
Chatwoot backing (context tools return empty). Confirm during implementation
that a non-numeric id like `"playground"` doesn't raise in `chatwoot_context.py`;
if it does, pass a config'd throwaway id or guard there (small, in scope).

## Frontend — `KnowledgePlayground.vue`
- Reuses the interaction model of `components/copilot/ProtonCopilotPanel.vue`
  (multi-turn thread, "Thinking…", per-message `tool_calls` + `sources`) but as a
  full-width console page, not a conversation sidepanel, and calls a new
  `askPlayground(thread, conversationId)` helper (thin wrapper over
  `/assist/copilot`, reusing `api/protonCopilot.js` shape).
- Elements: message list; input (Enter to send, Shift+Enter newline); **Reset**;
  a subtle header note "Sandbox — not tied to a conversation"; an optional
  collapsible "Advanced → simulate conversation id" text field (default
  `playground`). Show `tool_calls` ("Looked at: …") and clickable `sources`.
- No "Insert into reply" action (no editor here).

## Data flow
UI thread → `askPlayground` → `/assist/copilot` (x-api-key from `useProtonConfig`)
→ render answer + tools + sources. Stateless; nothing persisted.

## Testing
- Backend: covered by existing copilot tests; add one asserting a non-numeric
  `conversation_id` returns 200 with empty context (guard if needed).
- Frontend: vite compile at image build; manual smoke (ask a KB question, see a
  grounded answer + `search_knowledge_base` in tools + a source link).

## Out of scope
Saving/replaying sessions; prompt editing; model selection (that's Settings).

## Risks
- **Context-tool with fake id** — mitigated by the guard above.
- Playground answers cost Gemini tokens like any copilot call — acceptable for an
  admin tool; no rate-limit in v1 (note for later).
