# Persona-Aware `/chat/turn` (persona reaches live WhatsApp) — Design

**Date:** 2026-07-26
**Status:** Approved design (pre-implementation)
**Scope:** Follow-up to the persona-driven agent-bot phase
(`2026-07-26-persona-driven-agent-bot-design.md`), closing the gap the final
review found: the operator persona does not reach WhatsApp when it routes
through the backend `/chat/turn` agent.

## Problem

The WhatsApp "brain-swap" (commits `ebbb08f`/`d4144ca`) routes the WhatsApp bot
through the backend `POST /chat/turn` support agent. That agent is a **singleton**
built once at `OrchestratorService.__init__` with a **static, Proton-specific
`AGENT_INSTRUCTION`** (`features/chat/prompts.py`) that ignores the operator's
configured assistant persona. And `/chat/turn`'s request is `{session_id, text}`
only — it carries **no `inbox_id`**. So with `CHAT_AGENT_ENABLED=true`, the
persona an operator configures in the CRM (language / instructions / guardrails)
has **no effect on live WhatsApp**.

## Goal

Make the operator-configured assistant persona govern the `/chat/turn` agent, so
editing a persona in the CRM changes the live WhatsApp bot's language, tone, and
guardrails — while preserving the agent's essential tool-orchestration behavior
and staying byte-identical when no persona is configured.

## Design decisions

- **Augment, not replace.** `AGENT_INSTRUCTION` encodes essential tool rules (KB
  search, handoff, ticket-classify, test-drive, escalation) the agent needs to
  function. The operator persona is **layered onto** it, never replacing it:
  base `AGENT_INSTRUCTION` + an "## Operator persona" section (`instructions`) +
  "## Guardrails" (`guardrails`) + a "## Language" directive (`language`,
  overriding the base's "reply in the customer's language" line). Fully replacing
  the base would break the agent's tools. (A future "fully templatable base
  instruction per tenant" is a separate, larger effort — out of scope.)
- **Dynamic instruction via google-adk `InstructionProvider`.** ADK's
  `Agent.instruction` accepts `Union[str, InstructionProvider]` where
  `InstructionProvider = Callable[[ReadonlyContext], Union[str, Awaitable[str]]]`
  (`google/adk/agents/llm_agent.py`). The agent stays a singleton; its
  instruction becomes a callable that returns the per-turn composed persona
  string, falling back to `AGENT_INSTRUCTION`. No per-request agent rebuild.
- **Default-preserving + fail-open.** No `inbox_id`, no resolvable assistant, or
  an all-empty persona → the callable returns `AGENT_INSTRUCTION` verbatim →
  byte-identical to today. Any resolution error is swallowed (the turn proceeds
  with the base instruction). No new failure can break a `/chat/turn` reply.

## Existing seams (verified) this uses

- `/chat/turn`: `features/chat/router.py` `ChatTurnRequest{session_id, text}`
  (~L57) → `OrchestratorService.chat_turn` → `handle_turn(session_id, text)`
  (`features/chat/service.py` ~L335).
- Singleton agent: `service.py` `self._support_agent = build_ai_agent(...)`
  (~L130) via `features/chat/agents.py` `build_ai_agent(settings, ticketing_port,
  knowledge_port)` → `Agent(instruction=AGENT_INSTRUCTION, tools=[...])` (~L159).
- Per-turn run: `_invoke_support_agent` (~L250) `runner =
  self._runner_factory(self._support_agent)`.
- Persona resolution (reused, same path as the copilot): `inbox_resolver.py`
  `effective_assignment(assignment_store, assistants_store, tenant_settings_store,
  settings, inbox_id) -> {assistant_id, mode, source}`; `assistants_store` +
  `resolve_assistant`; `AssistantConfig{instructions, guardrails, language, ...}`.
  Reference: `copilot_router.py` (~L95-133) already does this resolution.
- Agent-service caller: `agent/app/clients/proton.py` `chat_turn(session_id,
  text)` (~L261) sends `{session_id, text}`; `agent/app/services/orchestrator.py`
  `_process_via_chat_agent` (~L485) uses `session_id = f"crm-{conversation_id}"`
  and is called from `_process_conversation`, which ALREADY resolves `inbox_id`
  (~L329-338) but does not forward it.

## Components

### 1. Agent-service plumbing (thread `inbox_id`)
- `proton.chat_turn(session_id, text, inbox_id: int | None = None)` — include
  `inbox_id` in the POST body when present.
- `_process_via_chat_agent(..., inbox_id)` — accept `inbox_id` and pass it to
  `proton.chat_turn`. `_process_conversation` already has `inbox_id`; forward it
  at the call site.

### 2. Backend request (accept `inbox_id`)
- `ChatTurnRequest` gains `inbox_id: int | None = None`.
- `chat_turn` handler passes it to `handle_turn(session_id, text, inbox_id=None)`.

### 3. Persona composer (backend)
- New pure helper `compose_chat_agent_instruction(base: str, assistant) -> str`:
  returns `base` verbatim when the assistant persona is empty; otherwise appends
  `## Operator persona\n{instructions}` (if set), `## Guardrails\n- …` (if any),
  and `## Language\nAlways respond in {language}.` (if set). Lives beside the
  other prompt-composition code (e.g. `agents.py` or a small `chat_persona.py`).
- Resolution helper: given `inbox_id`, reuse `effective_assignment` +
  `assistants_store` to get the `AssistantConfig` (fail-open → `None`).

### 4. ADK injection (per-turn instruction)
- `build_ai_agent` switches `instruction=AGENT_INSTRUCTION` → an
  `InstructionProvider` callable. The callable returns the per-turn composed
  instruction for the current session if the service has one, else
  `AGENT_INSTRUCTION`.
- Mechanism for per-turn handoff (implementer chooses the most robust; recommend
  the in-process map for testability + no persistence coupling):
  - **Recommended:** `OrchestratorService` holds
    `self._instruction_by_session: dict[str, str]`; `build_ai_agent` receives an
    `instruction_provider` closure that reads that map by the session id from
    `ReadonlyContext` (fallback `AGENT_INSTRUCTION`). `handle_turn` sets the map
    entry for the session before the run (and may clear it after).
  - **Alternative:** write the composed string into ADK `session.state` and use a
    callable / `{placeholder}` that reads it. Verify ADK state propagates to
    `canonical_instruction` before relying on it.
- In `handle_turn`: when `inbox_id` is provided, resolve the assistant, compose
  `compose_chat_agent_instruction(AGENT_INSTRUCTION, assistant)`, and register it
  for this session so the callable serves it on the run. When `inbox_id` is
  absent or resolution yields nothing / empty persona, register nothing → the
  callable serves `AGENT_INSTRUCTION`.

### 5. Store wiring
`OrchestratorService.handle_turn` needs `assignment_store`, `assistants_store`,
and `tenant_settings_store` to resolve the persona (the same instances the
copilot uses). If they are not already on `OrchestratorService`, inject them via
`main.py` DI. When any store is missing/unset → fail-open to `AGENT_INSTRUCTION`.

## Data flow

WhatsApp message → agent `_process_conversation` (has `inbox_id`) →
`_process_via_chat_agent(..., inbox_id)` → `proton.chat_turn(session_id, text,
inbox_id)` → backend `handle_turn` resolves inbox→assistant → composes
`AGENT_INSTRUCTION` + persona → registers it for the session → ADK
`InstructionProvider` feeds it to the model for this turn. Operator edits the
persona in the CRM (already shipped) → next WhatsApp turn reflects it.

## Error handling

Fail-open at every step: missing stores, unresolved inbox/assistant, empty
persona, or any exception during resolution/compose → no per-session instruction
registered → the callable returns `AGENT_INSTRUCTION` verbatim. `inbox_id` stays
optional end-to-end (`None` everywhere = today's behavior). Nothing new can raise
into a `/chat/turn` turn.

## Testing

- **Composer:** `compose_chat_agent_instruction` — empty persona → base verbatim;
  instructions/guardrails/language each appended correctly; language directive
  present only when set.
- **InstructionProvider:** callable returns the registered per-session
  instruction when present, `AGENT_INSTRUCTION` when absent.
- **handle_turn:** with a stubbed store returning a persona, the composed
  instruction is registered for the session; with no `inbox_id` / no persona,
  nothing is registered and behavior is unchanged (existing `/chat/turn` tests
  stay green).
- **Agent-service plumbing:** `proton.chat_turn` includes `inbox_id` in the body
  when set (respx); `_process_via_chat_agent` forwards the resolved `inbox_id`.

## Rollout

No flag of its own. With no persona configured (or `inbox_id` absent) behavior is
byte-identical. Takes effect only for tenants running the brain-swap
(`CHAT_AGENT_ENABLED=true`) who have configured a persona — exactly the intended
audience. Ships alongside the already-shipped persona editor.

## Note

This modifies files the WhatsApp brain-swap recently added (`chat/router.py`,
`chat/service.py`, `agents.py`, `agent/orchestrator.py`, `proton.py`). It is the
user-requested follow-up to that work.
