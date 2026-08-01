# Persona-Aware `/chat/turn` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operator-configured assistant persona (language / instructions / guardrails) govern the backend `/chat/turn` support agent, so it reaches live WhatsApp — augmenting the existing `AGENT_INSTRUCTION`, default-preserving and fail-open.

**Architecture:** Thread `inbox_id` into `/chat/turn`; in `handle_turn` resolve the per-inbox assistant (reusing `effective_assignment` + `assistants_store`) and compose `AGENT_INSTRUCTION` + persona into a per-session string; the singleton ADK agent reads it per-turn via an `InstructionProvider` callable backed by a per-session map, falling back to `AGENT_INSTRUCTION`.

**Tech Stack:** Python, google-adk (`Agent`/`InstructionProvider`), pydantic, pytest (backend `asyncio_mode=auto`; agent `asyncio_mode=auto`).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-26-persona-chat-turn-design.md`.
- **Augment, NOT replace:** `AGENT_INSTRUCTION` (Proton tool-orchestration rules) is ALWAYS the base; persona is layered on top. Never replace the base.
- **Default-preserving:** `inbox_id` is optional (`None` everywhere = today's behavior). No `inbox_id` / no resolvable assistant / all-empty persona → the agent sees `AGENT_INSTRUCTION` VERBATIM. Existing `/chat/turn` tests must stay green.
- **Fail-open:** any error resolving/composing the persona → no per-session instruction registered → `AGENT_INSTRUCTION`. Nothing new may raise into a `/chat/turn` turn.
- **Reuse existing resolution:** `inbox_resolver.effective_assignment(assignment_store, assistants_store, tenant_settings_store, settings, inbox_id)` + `assistants_store` — the same path `copilot_router.py` uses. Do NOT build a parallel resolver.
- **Backend tests:** `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest <files> -v`. **Agent tests:** `cd agent && .venv/bin/python -m pytest <files> -v`.

---

## File Structure

**Backend (`backend/apps/backend/src/chatbot/features/chat/`):**
- Create `chat_persona.py` — `compose_chat_agent_instruction(base, assistant) -> str` (pure composer).
- Modify `agents.py` — `build_ai_agent` accepts an optional `instruction_provider`.
- Modify `service.py` — `ChatTurnRequest` inbox_id is threaded; `OrchestratorService` holds the per-session instruction map + provider; `handle_turn` resolves+registers persona.
- Modify `router.py` — `ChatTurnRequest.inbox_id`.
- Modify `main.py` — inject the assistant stores into `OrchestratorService` if not already present.

**Agent (`agent/app/`):**
- Modify `clients/proton.py` — `chat_turn(session_id, text, inbox_id=None)`.
- Modify `services/orchestrator.py` — `_process_via_chat_agent` forwards `inbox_id`.

---

## Task 1: Persona composer (augment AGENT_INSTRUCTION)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/chat_persona.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_chat_persona.py`

**Interfaces:**
- Produces: `compose_chat_agent_instruction(base: str, assistant) -> str` — returns `base` verbatim when the assistant persona (`config.instructions`/`guardrails`/`language`) is empty; else `base` + appended `## Operator persona`, `## Guardrails`, `## Language` sections. `assistant` is an `Assistant` with `.config` (the `AssistantConfig` from `assistants_store.py`); tolerate `assistant=None` → return `base`.

- [ ] **Step 1: Write the failing test**

```python
# test_chat_persona.py
from chatbot.features.chat.chat_persona import compose_chat_agent_instruction
from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig

BASE = "BASE AGENT INSTRUCTION."


def _a(**cfg):
    return Assistant(name="A", config=AssistantConfig(**cfg))


def test_empty_persona_returns_base_verbatim():
    assert compose_chat_agent_instruction(BASE, _a()) == BASE
    assert compose_chat_agent_instruction(BASE, None) == BASE


def test_instructions_appended_as_operator_persona():
    out = compose_chat_agent_instruction(BASE, _a(instructions="Be warm and brief."))
    assert out.startswith(BASE)
    assert "## Operator persona" in out and "Be warm and brief." in out


def test_guardrails_and_language_appended():
    out = compose_chat_agent_instruction(
        BASE, _a(guardrails=["No prices", "No promises"], language="Bahasa Melayu")
    )
    assert out.startswith(BASE)
    assert "## Guardrails" in out and "- No prices" in out and "- No promises" in out
    assert "## Language" in out and "Always respond in Bahasa Melayu." in out
```

> Match `Assistant(...)`/`AssistantConfig(...)` construction to the real dataclasses (read `assistants_store.py`; `Assistant` may need only `name`+`config`). Keep the assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_chat_persona.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the composer**

```python
"""Compose the /chat/turn support-agent instruction from the base AGENT_INSTRUCTION
plus an operator-configured assistant persona.

AUGMENT, never replace: the base carries the essential tool-orchestration rules the
agent needs to function, so the operator persona is layered on top. An empty persona
returns the base verbatim (byte-identical default).
"""

from __future__ import annotations


def compose_chat_agent_instruction(base: str, assistant) -> str:
    if assistant is None:
        return base
    config = getattr(assistant, "config", None)
    if config is None:
        return base
    instructions = (getattr(config, "instructions", "") or "").strip()
    guardrails = [g for g in (getattr(config, "guardrails", []) or []) if str(g).strip()]
    language = (getattr(config, "language", "") or "").strip()
    if not instructions and not guardrails and not language:
        return base
    parts = [base]
    if instructions:
        parts.append(f"## Operator persona\n{instructions}")
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    if language:
        parts.append(f"## Language\nAlways respond in {language}.")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_chat_persona.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/chat_persona.py backend/apps/backend/src/chatbot/features/chat/test_chat_persona.py
git commit -m "feat(backend): compose /chat/turn instruction with operator persona (augment)"
```

---

## Task 2: Thread `inbox_id` into `/chat/turn` request + handle_turn

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/router.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/service.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_chat_turn_inbox_id.py`

**Interfaces:**
- Produces: `ChatTurnRequest.inbox_id: int | None = None`; `OrchestratorService.handle_turn(session_id, text, inbox_id: int | None = None)` accepts it (not yet used for behavior — plumbing only).

- [ ] **Step 1: Read the files**

Read `router.py` `ChatTurnRequest` (~L57) + `chat_turn` handler (~L1063) and `service.py` `handle_turn` signature (~L335). Note how `chat_turn` calls `handle_turn`.

- [ ] **Step 2: Write the failing test**

```python
# test_chat_turn_inbox_id.py
import inspect
from chatbot.features.chat.router import ChatTurnRequest
from chatbot.features.chat.service import OrchestratorService


def test_chat_turn_request_accepts_inbox_id():
    r = ChatTurnRequest(session_id="s", text="hi", inbox_id=3)
    assert r.inbox_id == 3
    assert ChatTurnRequest(session_id="s", text="hi").inbox_id is None


def test_handle_turn_accepts_inbox_id_param():
    sig = inspect.signature(OrchestratorService.handle_turn)
    assert "inbox_id" in sig.parameters
    assert sig.parameters["inbox_id"].default is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_chat_turn_inbox_id.py -v`
Expected: FAIL — `inbox_id` not a field / not a param.

- [ ] **Step 4: Implement the plumbing**

In `router.py`, add to `ChatTurnRequest`:
```python
    inbox_id: int | None = None
```
And in the `chat_turn` handler, pass it through:
```python
        result = await self.orchestrator.handle_turn(
            session_id=req.session_id, text=req.text, inbox_id=req.inbox_id
        )
```

In `service.py`, change the `handle_turn` signature to accept `inbox_id` (keep everything else unchanged for now):
```python
    async def handle_turn(self, session_id: str, text: str, inbox_id: int | None = None):
```
(Do NOT use `inbox_id` yet — Task 4 wires the behavior. This task only proves the param threads through.)

- [ ] **Step 5: Run test + existing chat-turn tests**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_chat_turn_inbox_id.py src/chatbot/features/chat/test_service.py -v`
Expected: new tests PASS; existing `test_service.py` still PASS (signature is backward-compatible).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/router.py backend/apps/backend/src/chatbot/features/chat/service.py backend/apps/backend/src/chatbot/features/chat/test_chat_turn_inbox_id.py
git commit -m "feat(backend): accept optional inbox_id on /chat/turn (plumbing)"
```

---

## Task 3: ADK `InstructionProvider` + per-session instruction map

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/agents.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/service.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_chat_instruction_provider.py`

**Interfaces:**
- Consumes: `AGENT_INSTRUCTION` (`prompts.py`).
- Produces: `build_ai_agent(settings, ticketing_port, knowledge_port, instruction_provider=None)` — passes `instruction=instruction_provider or AGENT_INSTRUCTION` to `Agent(...)`. `OrchestratorService` gains `self._instruction_by_session: dict[str, str]` and a bound provider `_chat_instruction_provider(ctx) -> str` returning `self._instruction_by_session.get(<session id from ctx>, AGENT_INSTRUCTION)`, wired into `build_ai_agent` at construction.

- [ ] **Step 1: Read the files + the ADK ReadonlyContext**

Read `agents.py` `build_ai_agent` (~L17, agent constructed ~L159) and `service.py` `__init__` (~L130, where `self._support_agent = build_ai_agent(...)`). Then read the ADK `ReadonlyContext` class to find how to read the current session id from the callback context: `backend/apps/backend/.venv/lib/python3.12/site-packages/google/adk/agents/readonly_context.py` (and `invocation_context.py` if needed). Identify the accessor (e.g. a `session` / invocation-context property exposing `.id`). You will use it in the provider.

- [ ] **Step 2: Write the failing test**

```python
# test_chat_instruction_provider.py
from chatbot.features.chat.prompts import AGENT_INSTRUCTION
from chatbot.features.chat.service import OrchestratorService


class _FakeCtx:
    # mimic the ReadonlyContext session-id path the provider reads (adjust attr
    # path in Step 4 to the real accessor you found; keep this fake matching it)
    def __init__(self, session_id):
        self.session_id = session_id


def test_provider_returns_registered_instruction_then_falls_back(monkeypatch):
    svc = OrchestratorService.__new__(OrchestratorService)  # bypass heavy __init__
    svc._instruction_by_session = {"crm-42": "PERSONA-INSTRUCTION"}
    # session with a registered instruction:
    assert svc._chat_instruction_provider(_FakeCtx("crm-42")) == "PERSONA-INSTRUCTION"
    # unregistered session -> base:
    assert svc._chat_instruction_provider(_FakeCtx("crm-99")) == AGENT_INSTRUCTION
    # ctx without a resolvable session id -> base (fail-open):
    assert svc._chat_instruction_provider(object()) == AGENT_INSTRUCTION
```

> Adjust `_FakeCtx` so its attribute path matches the real `ReadonlyContext` session-id accessor you use in Step 4 (e.g. if you read `ctx._invocation_context.session.id`, make the fake expose that path). The three behaviors (registered → persona, unregistered → base, unreadable → base) are the contract.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_chat_instruction_provider.py -v`
Expected: FAIL — `_chat_instruction_provider` / `_instruction_by_session` missing.

- [ ] **Step 4: Implement**

In `agents.py`, add the optional param and use it:
```python
def build_ai_agent(
    settings: Settings,
    _ticketing_port: TicketingPort,
    knowledge_port: KnowledgePort,
    instruction_provider=None,
) -> Agent:
    ...
    return Agent(
        name="support_agent",
        model=settings.gemini_model,
        instruction=instruction_provider or AGENT_INSTRUCTION,
        ...  # generate_content_config + tools unchanged
    )
```

In `service.py` `OrchestratorService.__init__`, before building the agent, add the map, and pass the provider:
```python
        self._instruction_by_session: dict[str, str] = {}
        self._support_agent = build_ai_agent(
            settings, ticketing_port, knowledge_port,
            instruction_provider=self._chat_instruction_provider,
        )
```
Add the provider method (use the REAL session-id accessor you found in Step 1; the body must be fail-open):
```python
    def _chat_instruction_provider(self, ctx) -> str:
        """ADK InstructionProvider: per-session composed instruction, else base."""
        try:
            session_id = ctx._invocation_context.session.id  # <-- replace with the real accessor
        except Exception:
            return AGENT_INSTRUCTION
        return self._instruction_by_session.get(session_id, AGENT_INSTRUCTION)
```
Ensure `AGENT_INSTRUCTION` is imported in `service.py` (from `chatbot.features.chat.prompts`).

- [ ] **Step 5: Run test + existing service tests**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_chat_instruction_provider.py src/chatbot/features/chat/test_service.py -v`
Expected: new tests PASS; existing service tests PASS (empty map → every session gets `AGENT_INSTRUCTION`, byte-identical default).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/agents.py backend/apps/backend/src/chatbot/features/chat/service.py backend/apps/backend/src/chatbot/features/chat/test_chat_instruction_provider.py
git commit -m "feat(backend): per-session InstructionProvider for the /chat/turn agent (default AGENT_INSTRUCTION)"
```

---

## Task 4: Resolve + register persona in `handle_turn` (+ store wiring)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/service.py`
- Modify: `backend/apps/backend/src/chatbot/main.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_handle_turn_persona.py`

**Interfaces:**
- Consumes: `compose_chat_agent_instruction` (Task 1), `self._instruction_by_session` (Task 3), `effective_assignment` + `assistants_store` (existing).
- Produces: `handle_turn`, when `inbox_id` is set, resolves the assistant and registers `compose_chat_agent_instruction(AGENT_INSTRUCTION, assistant)` in `self._instruction_by_session[session_id]` (only when it differs from `AGENT_INSTRUCTION`); fail-open. `OrchestratorService.__init__` accepts the assistant stores (injected by `main.py`).

- [ ] **Step 1: Read the files**

Read `service.py` `handle_turn` (~L335) — where the session id is known and where `_run_support_agent`/`_invoke_support_agent` is called (~L394/250). Read `inbox_resolver.effective_assignment` signature and how `copilot_router.py` (~L95-133) resolves `assistant` from `inbox_id` (which stores it passes). Read `main.py` where `OrchestratorService` is constructed and where the assistant stores (`assignment_store`, `assistants_store`, `tenant_settings_store`) are created (they exist for the copilot wiring).

- [ ] **Step 2: Write the failing test**

```python
# test_handle_turn_persona.py
from chatbot.features.chat.prompts import AGENT_INSTRUCTION
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig


def _svc_with_persona(assistant):
    svc = OrchestratorService.__new__(OrchestratorService)
    svc._instruction_by_session = {}
    # minimal stubs for the resolver helper the impl calls:
    svc._resolve_chat_assistant = None  # replaced below

    async def _resolve(inbox_id):
        return assistant

    svc._resolve_chat_assistant = _resolve
    return svc


async def test_register_persona_when_inbox_resolves_nonempty():
    a = Assistant(name="A", config=AssistantConfig(language="Bahasa Melayu"))
    svc = _svc_with_persona(a)
    await svc._register_chat_persona("crm-1", inbox_id=3)
    reg = svc._instruction_by_session.get("crm-1")
    assert reg is not None and reg.startswith(AGENT_INSTRUCTION)
    assert "Always respond in Bahasa Melayu." in reg


async def test_no_inbox_registers_nothing():
    svc = _svc_with_persona(None)
    await svc._register_chat_persona("crm-2", inbox_id=None)
    assert "crm-2" not in svc._instruction_by_session


async def test_empty_persona_registers_nothing():
    a = Assistant(name="A", config=AssistantConfig())  # all empty
    svc = _svc_with_persona(a)
    await svc._register_chat_persona("crm-3", inbox_id=3)
    assert "crm-3" not in svc._instruction_by_session
```

> This tests a small extracted helper `_register_chat_persona(session_id, inbox_id)` and its dependency `_resolve_chat_assistant(inbox_id) -> Assistant | None`. Implement both so `handle_turn` just calls `await self._register_chat_persona(session_id, inbox_id)`. Adjust construction to the real store fields if you inline resolution instead of the `_resolve_chat_assistant` seam — but keep the seam so this test can stub resolution without a live Firestore.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_handle_turn_persona.py -v`
Expected: FAIL — helpers missing.

- [ ] **Step 4: Implement resolution + registration + store wiring**

In `service.py`:
- Accept the stores in `__init__` (add params `assignment_store=None, assistants_store=None, tenant_settings_store=None`, store on `self`).
- Add `_resolve_chat_assistant(inbox_id)` — fail-open, reusing `effective_assignment` exactly as the copilot does:
```python
    async def _resolve_chat_assistant(self, inbox_id):
        if inbox_id is None or self._assistants_store is None:
            return None
        try:
            eff = await effective_assignment(
                self._assignment_store, self._assistants_store,
                self._tenant_settings_store, self._settings, inbox_id,
            )
            assistant_id = eff.get("assistant_id") if eff else None
            return await self._assistants_store.get(assistant_id) if assistant_id else \
                await self._assistants_store.get_default()
        except Exception:
            return None
```
  (Match the exact `effective_assignment` arg order + `assistants_store` getter names you read in Step 1; `resolve_assistant`/`get`/`get_default` — use the real ones.)
- Add `_register_chat_persona(session_id, inbox_id)`:
```python
    async def _register_chat_persona(self, session_id, inbox_id) -> None:
        try:
            assistant = await self._resolve_chat_assistant(inbox_id)
            composed = compose_chat_agent_instruction(AGENT_INSTRUCTION, assistant)
            if composed != AGENT_INSTRUCTION:
                self._instruction_by_session[session_id] = composed
            else:
                self._instruction_by_session.pop(session_id, None)
        except Exception:
            self._instruction_by_session.pop(session_id, None)
```
- In `handle_turn`, right after the session id is known and before running the support agent, add: `await self._register_chat_persona(session_id, inbox_id)`. Import `compose_chat_agent_instruction` + `effective_assignment`.

In `main.py`, pass the existing assistant stores into `OrchestratorService(...)` (the same instances the copilot wiring uses). If `OrchestratorService` is constructed before those stores exist, move the store construction up or pass them in — keep it minimal.

- [ ] **Step 5: Run test + the whole chat test dir**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy uv run pytest src/chatbot/features/chat/test_handle_turn_persona.py src/chatbot/features/chat/test_service.py src/chatbot/features/chat/test_chat_persona.py -v`
Expected: new tests PASS; existing service tests PASS (no `inbox_id` in existing tests → nothing registered → `AGENT_INSTRUCTION`).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/service.py backend/apps/backend/src/chatbot/main.py backend/apps/backend/src/chatbot/features/chat/test_handle_turn_persona.py
git commit -m "feat(backend): resolve + register operator persona per /chat/turn session (fail-open)"
```

---

## Task 5: Agent-service — forward `inbox_id` to `/chat/turn`

**Files:**
- Modify: `agent/app/clients/proton.py`
- Modify: `agent/app/services/orchestrator.py`
- Test: `agent/tests/test_chat_turn_inbox_id.py`

**Interfaces:**
- Consumes: backend `/chat/turn` now accepts `inbox_id` (Task 2).
- Produces: `ProtonConfigClient.chat_turn(session_id, text, inbox_id: int | None = None)` includes `inbox_id` in the POST body when not None; `_process_via_chat_agent(..., inbox_id)` forwards the `inbox_id` resolved in `_process_conversation`.

- [ ] **Step 1: Read the files**

Read `proton.py` `chat_turn` (~L261, sends `{session_id, text}`) and `orchestrator.py` `_process_via_chat_agent` (~L485) + its call site in `_process_conversation` (~L403, where `inbox_id` is already resolved ~L329-338).

- [ ] **Step 2: Write the failing test**

```python
# test_chat_turn_inbox_id.py
import httpx, respx
from app.clients.proton import ProtonConfigClient


@respx.mock
async def test_chat_turn_includes_inbox_id_when_set():
    route = respx.post("http://backend/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "hi"})
    )
    c = ProtonConfigClient(base_url="http://backend", api_key="k")
    await c.chat_turn("crm-1", "hello", inbox_id=3)
    body = route.calls.last.request.content.decode()
    assert '"inbox_id": 3' in body or '"inbox_id":3' in body


@respx.mock
async def test_chat_turn_omits_or_nulls_inbox_id_when_none():
    route = respx.post("http://backend/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "hi"})
    )
    c = ProtonConfigClient(base_url="http://backend", api_key="k")
    await c.chat_turn("crm-1", "hello")
    # default None -> either omitted or explicit null; must NOT send a bogus inbox
    body = route.calls.last.request.content.decode()
    assert '"inbox_id": 3' not in body
```

> Adjust `ProtonConfigClient(...)` construction + the base URL to the real constructor you read. Keep the two assertions.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd agent && .venv/bin/python -m pytest tests/test_chat_turn_inbox_id.py -v`
Expected: FAIL — `chat_turn` has no `inbox_id` param.

- [ ] **Step 4: Implement**

In `proton.py` `chat_turn`:
```python
    async def chat_turn(self, session_id: str, text: str, inbox_id: int | None = None) -> dict | None:
        ...
        payload = {"session_id": session_id, "text": text}
        if inbox_id is not None:
            payload["inbox_id"] = inbox_id
        response = await self._client.post("/chat/turn", json=payload, timeout=60.0)
        ...
```
(Keep the rest of the method — auth headers, fail-open handling — unchanged.)

In `orchestrator.py`:
- Add `inbox_id: int | None = None` to `_process_via_chat_agent`'s signature.
- Pass it through: `await proton.chat_turn(f"crm-{conversation_id}", text, inbox_id)`.
- At the call site in `_process_conversation` (~L403), pass the already-resolved `inbox_id`: `_process_via_chat_agent(..., inbox_id=inbox_id)`.

- [ ] **Step 5: Run test + existing chat-agent orchestrator tests**

Run: `cd agent && .venv/bin/python -m pytest tests/test_chat_turn_inbox_id.py tests/test_orchestrator_chat_agent.py -v`
Expected: new tests PASS; the existing brain-swap tests still PASS (inbox_id optional, default None preserves behavior).

- [ ] **Step 6: Commit**

```bash
git add agent/app/clients/proton.py agent/app/services/orchestrator.py agent/tests/test_chat_turn_inbox_id.py
git commit -m "feat(agent): forward inbox_id to /chat/turn so persona resolves for WhatsApp"
```

---

## Self-Review

**Spec coverage:**
- Augment composer → Task 1. ✓
- `inbox_id` on `/chat/turn` request + `handle_turn` → Task 2. ✓
- ADK `InstructionProvider` + per-session map (default `AGENT_INSTRUCTION`) → Task 3. ✓
- Resolve + register persona in `handle_turn`, reuse `effective_assignment`, store wiring → Task 4. ✓
- Agent-service forwards `inbox_id` → Task 5. ✓
- Default-preserving + fail-open → Tasks 1/3/4 (empty→base, errors→base, no inbox→base); existing tests stay green each task. ✓

**Placeholder scan:** No "TBD"/"add error handling". The "read the file / find the real accessor" steps are deliberate (ADK `ReadonlyContext` session-id accessor + exact store getters must be read), with the code + tests given verbatim and a fail-open fallback that makes a wrong accessor degrade to `AGENT_INSTRUCTION` (not crash).

**Type consistency:** `compose_chat_agent_instruction(base, assistant) -> str` used identically in Task 1 (produce) and Task 4 (consume). `_instruction_by_session: dict[str,str]` + `_chat_instruction_provider(ctx)` + `_register_chat_persona(session_id, inbox_id)` + `_resolve_chat_assistant(inbox_id)` consistent across Tasks 3–4. `inbox_id: int | None = None` consistent across router → handle_turn (Task 2) → proton.chat_turn → `_process_via_chat_agent` (Task 5).
