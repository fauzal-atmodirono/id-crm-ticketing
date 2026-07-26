# Persona-Driven Multilingual Agent-Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator configure a persona in the CRM — language, tone, and every customer-facing message — and have it govern the customer-facing agent-bot AND the copilot, all as an additive, default-preserving extension of the existing assistant-persona system.

**Architecture:** Add fields to the existing `AssistantConfig` (backend), inject `language` into the copilot/assist prompt assembly, extend the `ProtonConfigClient` agent↔backend bridge to carry the persona + new messages, and consume them in the agent-bot orchestrator (decision prompt) + lifecycle messages. Extend the existing `KnowledgeSettings.vue` persona editor (no new page). Every new field defaults to empty → today's behavior byte-for-byte.

**Tech Stack:** Python (backend `chatbot` + `agent`), pydantic/dataclasses, pytest (`asyncio_mode=auto`), Vue 3 SPA fork patch + vite.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-26-persona-driven-agent-bot-design.md`.
- **Additive + default-preserving:** every new persona field defaults to `""` (or `[]`); empty → byte-identical to today's hard-coded behavior. All agent-side reads are FAIL-OPEN (proton unreachable / no assistant / empty field → the existing default constant).
- **Out of scope (do NOT touch):** `agent/app/services/responder.py` (Zammad, retiring), `agent/app/services/categorize.py` (internal classifier), email auto-ack (`EMAIL_AUTOACK_TEMPLATE`).
- **Exact new `AssistantConfig` field names:** `language`, `idle_warning_message`, `idle_close_message`, `resolution_prompt_message`, `survey_ai_message`, `survey_agent_message`, `thanks_message`, `assign_agent_message` — all `str = ""`.
- **`get_assistant_messages` dict keys** (existing + new): `welcome`, `handoff`, `resolution`, `idle_warning`, `idle_close`, `resolution_prompt`, `survey_ai`, `survey_agent`, `thanks`, `assign_agent`.
- **`get_assistant_persona` returns** `{"instructions": str, "guardrails": list[str], "language": str}` or `None` (fail-open).
- **Backend tests:** `cd backend/apps/backend && uv run pytest <files> -v` (hermetic — construct `Settings(...)` in-test; `conftest.py` disables `.env`).
- **Agent tests:** `cd agent && pytest <files> -v` (asyncio_mode auto; respx for HTTP; sqlite for DB).
- **Frontend:** author in fork clone `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot` (branch `proton-reports-dev`), export patch `deploy/chatwoot-fork/patches/0022-knowledge-persona-language-messages.patch`, add `0022` to the Dockerfile LABEL, `vite build` gate, `--no-verify` fork commit (bare-string convention).

---

## File Structure

**Backend (`backend/apps/backend/src/chatbot/`):**
- Modify `features/chat/adapters/assistants_store.py` — new `AssistantConfig` fields + (de)serialization tolerant of missing keys.
- Modify `features/assist/assistant_runtime.py` — `build_system_prompt` language section.
- Modify `features/assist/router.py` — `_apply_persona` language prefix.

**Agent (`agent/app/`):**
- Modify `clients/proton.py` — `get_assistant_persona` + extend `get_assistant_messages`.
- Modify `services/orchestrator.py` — persona-driven system prompt.
- Modify `services/lifecycle.py` + `services/lifecycle_scanner.py` — message overrides.

**Frontend:** `KnowledgeSettings.vue` (fork) → patch `0022`.

---

## Task 1: New AssistantConfig persona fields (backend)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/assistants_store.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_assistant_persona_fields.py`

**Interfaces:**
- Produces: `AssistantConfig` gains `language`, `idle_warning_message`, `idle_close_message`, `resolution_prompt_message`, `survey_ai_message`, `survey_agent_message`, `thanks_message`, `assign_agent_message` (all `str = ""`), surviving to_dict/from_dict + `PUT` config merge.

- [ ] **Step 1: Read the file first**

Read `assistants_store.py` and locate: the `AssistantConfig` dataclass (~line 61), and its serialization (a `to_dict`/`asdict` and a `from_dict`/`_config_from_dict` that builds `AssistantConfig` from a stored/`PUT`-merged dict — note whether it uses `data.get(key, default)` per field or `**data`).

- [ ] **Step 2: Write the failing test**

```python
# test_assistant_persona_fields.py
from chatbot.features.chat.adapters.assistants_store import AssistantConfig, InMemoryAssistantsStore


def test_new_persona_fields_default_empty() -> None:
    c = AssistantConfig()
    assert c.language == ""
    for f in ("idle_warning_message", "idle_close_message", "resolution_prompt_message",
              "survey_ai_message", "survey_agent_message", "thanks_message", "assign_agent_message"):
        assert getattr(c, f) == ""


async def test_new_fields_survive_store_roundtrip() -> None:
    store = InMemoryAssistantsStore()
    a = await store.get_default()
    await store.update(a.id, {"config": {"language": "Bahasa Melayu", "thanks_message": "Terima kasih!"}})
    got = await store.get(a.id)
    assert got.config.language == "Bahasa Melayu"
    assert got.config.thanks_message == "Terima kasih!"
    # untouched fields keep defaults
    assert got.config.idle_warning_message == ""
```

> If the real store class / method names differ (e.g. `create`/`update` signatures, or `get_default` is sync), adjust the test to the ACTUAL API you read in Step 1 — keep the assertions (defaults + roundtrip + merge-preserves-others).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_assistant_persona_fields.py -v`
Expected: FAIL — `AttributeError` / missing fields.

- [ ] **Step 4: Add the fields + ensure tolerant deserialization**

In `AssistantConfig` (after the existing `resolution_message` field), add:

```python
    language: str = ""
    idle_warning_message: str = ""
    idle_close_message: str = ""
    resolution_prompt_message: str = ""
    survey_ai_message: str = ""
    survey_agent_message: str = ""
    thanks_message: str = ""
    assign_agent_message: str = ""
```

If the config `from_dict` reads fields explicitly (per-field `data.get(...)`), add the same `data.get("<field>", "")` lines for each new field so old stored docs (missing these keys) still load. If it uses `AssistantConfig(**{k: v for k, v in data.items() if k in FIELDS})` or `dataclasses`-field filtering, confirm the new fields are picked up and missing keys fall to defaults. Do NOT change existing fields.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_assistant_persona_fields.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/adapters/assistants_store.py backend/apps/backend/src/chatbot/features/chat/test_assistant_persona_fields.py
git commit -m "feat(backend): add language + 7 lifecycle-message fields to AssistantConfig"
```

---

## Task 2: Language injection in copilot + assist prompts (backend)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/assist/assistant_runtime.py`
- Modify: `backend/apps/backend/src/chatbot/features/assist/router.py`
- Test: `backend/apps/backend/src/chatbot/features/assist/test_persona_language.py`

**Interfaces:**
- Consumes: `AssistantConfig.language` (Task 1).
- Produces: `build_system_prompt` appends a `## Language\nAlways respond in {language}.` section when `language` non-empty (empty → unchanged); `_apply_persona` prepends a language directive when set.

- [ ] **Step 1: Read the files**

Read `assistant_runtime.py::build_system_prompt` (~line 49) and `router.py::_apply_persona` (~line 185) to see how sections/prefixes are currently appended (the exact string-join style and the `assistant.config` access).

- [ ] **Step 2: Write the failing test**

```python
# test_persona_language.py
from chatbot.features.assist.assistant_runtime import build_system_prompt
from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig


def _assistant(**cfg) -> Assistant:
    return Assistant(name="A", config=AssistantConfig(**cfg))


def test_language_section_added_when_set() -> None:
    p = build_system_prompt(_assistant(language="Bahasa Melayu"))
    assert "Always respond in Bahasa Melayu." in p


def test_no_language_section_when_empty() -> None:
    p = build_system_prompt(_assistant())
    assert "Always respond in" not in p
```

> Match `Assistant(...)` construction to the real dataclass (it may require `id`/other args — read Task-1 file). Keep the two assertions.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/assist/test_persona_language.py -v`
Expected: FAIL — no "Always respond in" text.

- [ ] **Step 4: Implement**

In `build_system_prompt`, after the existing sections are assembled (before returning), add:

```python
    language = getattr(assistant.config, "language", "") or ""
    if language.strip():
        sections.append(f"## Language\nAlways respond in {language.strip()}.")
```

(Use the actual accumulator variable name from the file — e.g. `sections`/`parts` — and the same join style used to return the prompt.)

In `router.py::_apply_persona`, where it already builds the product/guardrails prefix from the resolved assistant, add a language line when set (append to the same prefix list it returns):

```python
    language = getattr(assistant.config, "language", "") or ""
    if language.strip():
        prefix_parts.append(f"Always respond in {language.strip()}.")
```

(Match the real variable name / structure `_apply_persona` uses.)

- [ ] **Step 5: Run test + the existing assist suite**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/assist/test_persona_language.py src/chatbot/features/assist/ -v`
Expected: new tests PASS; existing assist tests still PASS (empty-language path unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/assist/assistant_runtime.py backend/apps/backend/src/chatbot/features/assist/router.py backend/apps/backend/src/chatbot/features/assist/test_persona_language.py
git commit -m "feat(backend): inject persona language into copilot + assist prompts"
```

---

## Task 3: Extend ProtonConfigClient — persona + new messages (agent)

**Files:**
- Modify: `agent/app/clients/proton.py`
- Test: `agent/tests/test_proton_persona.py`

**Interfaces:**
- Consumes: backend `GET /kb/inboxes` + `GET /kb/assistants/{id}` (already used by `get_assistant_messages`).
- Produces:
  - `ProtonConfigClient.get_assistant_persona(inbox_id) -> dict | None` = `{"instructions": str, "guardrails": list[str], "language": str}` (fail-open `None`).
  - `get_assistant_messages(inbox_id)` extended to also return keys `idle_warning`, `idle_close`, `resolution_prompt`, `survey_ai`, `survey_agent`, `thanks`, `assign_agent` (from the matching `*_message` config fields), alongside existing `welcome`/`handoff`/`resolution`.

- [ ] **Step 1: Read the file**

Read `agent/app/clients/proton.py` — study `get_assistant_messages`: how it resolves `inbox_id`→`assistant_id` (via `/kb/inboxes`), fetches `/kb/assistants/{id}`, reads `config.welcome_message` etc., and its caching + fail-open (`return None`/`{}`) shape. Reuse that exact pattern.

- [ ] **Step 2: Write the failing test**

```python
# test_proton_persona.py
import httpx, respx
from app.clients.proton import ProtonConfigClient


def _client() -> ProtonConfigClient:
    return ProtonConfigClient(base_url="http://backend", api_key="k")


@respx.mock
async def test_get_assistant_persona_maps_fields() -> None:
    respx.get("http://backend/kb/inboxes").mock(return_value=httpx.Response(
        200, json={"inboxes": [{"inbox_id": 3, "assistant_id": "asst_1", "mode": "auto"}]}))
    respx.get("http://backend/kb/assistants/asst_1").mock(return_value=httpx.Response(
        200, json={"id": "asst_1", "config": {
            "instructions": "Be terse.", "guardrails": ["No prices"], "language": "English",
            "thanks_message": "Cheers!"}}))
    c = _client()
    persona = await c.get_assistant_persona(3)
    assert persona == {"instructions": "Be terse.", "guardrails": ["No prices"], "language": "English"}
    msgs = await c.get_assistant_messages(3)
    assert msgs["thanks"] == "Cheers!"


@respx.mock
async def test_persona_fail_open_on_error() -> None:
    respx.get("http://backend/kb/inboxes").mock(return_value=httpx.Response(500))
    assert await _client().get_assistant_persona(3) is None
```

> Adjust `ProtonConfigClient(...)` construction, the exact response JSON shape for `/kb/inboxes`, and the caching (a shared cache may make the two calls hit one mock) to match the real code you read. Keep the mapping + fail-open assertions.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd agent && pytest tests/test_proton_persona.py -v`
Expected: FAIL — `get_assistant_persona` missing / `thanks` key absent.

- [ ] **Step 4: Implement**

Extend `get_assistant_messages` to map the 7 new config fields into the returned dict (mirror the existing welcome/handoff/resolution mapping):

```python
        "idle_warning": config.get("idle_warning_message", ""),
        "idle_close": config.get("idle_close_message", ""),
        "resolution_prompt": config.get("resolution_prompt_message", ""),
        "survey_ai": config.get("survey_ai_message", ""),
        "survey_agent": config.get("survey_agent_message", ""),
        "thanks": config.get("thanks_message", ""),
        "assign_agent": config.get("assign_agent_message", ""),
```

Add `get_assistant_persona` reusing the same inbox→assistant resolution + fetch + fail-open:

```python
    async def get_assistant_persona(self, inbox_id: int | None) -> dict | None:
        """Persona fields for shaping the agent-bot decision prompt. Fail-open None."""
        assistant = await self._resolve_assistant(inbox_id)  # reuse the existing resolver
        if assistant is None:
            return None
        config = assistant.get("config", {}) or {}
        return {
            "instructions": config.get("instructions", "") or "",
            "guardrails": list(config.get("guardrails", []) or []),
            "language": config.get("language", "") or "",
        }
```

(Use whatever the existing helper is named — if `get_assistant_messages` inlines the resolution rather than calling a shared `_resolve_assistant`, factor the resolution into a small shared helper OR inline the same steps here. Keep both methods sharing the cached fetch.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd agent && pytest tests/test_proton_persona.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/app/clients/proton.py agent/tests/test_proton_persona.py
git commit -m "feat(agent): ProtonConfigClient persona + 7 lifecycle messages"
```

---

## Task 4: Persona-driven agent-bot system prompt (agent)

**Files:**
- Modify: `agent/app/services/orchestrator.py`
- Test: `agent/tests/test_orchestrator_persona_prompt.py`

**Interfaces:**
- Consumes: `ProtonConfigClient.get_assistant_persona` (Task 3); the module `SYSTEM_PROMPT` constant.
- Produces: pure `_build_system_prompt(persona: dict | None) -> str` — `None`/all-empty → returns `SYSTEM_PROMPT` verbatim; else base(`instructions` or `SYSTEM_PROMPT`) + `## Guardrails` + `Always reply in {language}.`. Orchestrator passes the composed prompt to `gemini.decide(...)`.

- [ ] **Step 1: Read the file**

Read `orchestrator.py`: the `SYSTEM_PROMPT` constant (~line 62), the `gemini.decide(SYSTEM_PROMPT, context)` call (~line 362), and where `proton` + `inbox_id` are already resolved in `_process_conversation` (for mode/debounce). You will fetch the persona there and pass the composed prompt.

- [ ] **Step 2: Write the failing test**

```python
# test_orchestrator_persona_prompt.py
from app.services.orchestrator import SYSTEM_PROMPT, _build_system_prompt


def test_none_persona_returns_verbatim() -> None:
    assert _build_system_prompt(None) == SYSTEM_PROMPT


def test_empty_persona_returns_verbatim() -> None:
    assert _build_system_prompt({"instructions": "", "guardrails": [], "language": ""}) == SYSTEM_PROMPT


def test_instructions_override_base() -> None:
    out = _build_system_prompt({"instructions": "You are Ana.", "guardrails": [], "language": ""})
    assert out.startswith("You are Ana.")
    assert SYSTEM_PROMPT not in out


def test_guardrails_and_language_appended() -> None:
    out = _build_system_prompt({"instructions": "", "guardrails": ["No prices"], "language": "Bahasa Melayu"})
    assert out.startswith(SYSTEM_PROMPT)  # default base kept
    assert "## Guardrails" in out and "- No prices" in out
    assert "Always reply in Bahasa Melayu." in out
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd agent && pytest tests/test_orchestrator_persona_prompt.py -v`
Expected: FAIL — `_build_system_prompt` undefined.

- [ ] **Step 4: Implement the helper + wire it**

Add near `SYSTEM_PROMPT`:

```python
def _build_system_prompt(persona: dict | None) -> str:
    """Compose the agent-bot decision prompt from an assistant persona.

    None or all-empty persona -> the module SYSTEM_PROMPT verbatim (byte-identical
    default). Otherwise: base = instructions if set else SYSTEM_PROMPT; then append
    a Guardrails section and an explicit language line when present.
    """
    if not persona:
        return SYSTEM_PROMPT
    instructions = (persona.get("instructions") or "").strip()
    guardrails = [g for g in (persona.get("guardrails") or []) if str(g).strip()]
    language = (persona.get("language") or "").strip()
    if not instructions and not guardrails and not language:
        return SYSTEM_PROMPT
    parts = [instructions or SYSTEM_PROMPT]
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    if language:
        parts.append(f"Always reply in {language}.")
    return "\n\n".join(parts)
```

In `_process_conversation`, where `proton` + `inbox_id` are available (same place mode is resolved), fetch the persona fail-open and use the composed prompt:

```python
    persona = await proton.get_assistant_persona(inbox_id) if proton is not None else None
    system_prompt = _build_system_prompt(persona)
    ...
    decision = await gemini.decide(system_prompt, context)   # was: gemini.decide(SYSTEM_PROMPT, context)
```

Wrap the `get_assistant_persona` call so any exception → `persona = None` (fail-open) if the client isn't already guaranteed fail-open.

- [ ] **Step 5: Run the test + orchestrator regression suite**

Run: `cd agent && pytest tests/test_orchestrator_persona_prompt.py tests/test_orchestrator.py -v`
Expected: new tests PASS; existing orchestrator tests still PASS (None persona → verbatim prompt, so behavior is unchanged when proton is unset/mock returns None).

- [ ] **Step 6: Commit**

```bash
git add agent/app/services/orchestrator.py agent/tests/test_orchestrator_persona_prompt.py
git commit -m "feat(agent): persona-driven agent-bot system prompt (fail-open, default-preserving)"
```

---

## Task 5: Operator-overridable lifecycle messages (agent)

**Files:**
- Modify: `agent/app/services/lifecycle.py`
- Modify: `agent/app/services/lifecycle_scanner.py`
- Test: `agent/tests/test_lifecycle_messages.py`

**Interfaces:**
- Consumes: extended `get_assistant_messages` (Task 3).
- Produces: each lifecycle customer message resolved as `messages.get(<key>) or <DEFAULT>`; new `ASSIGN_AGENT_DEFAULT` constant for the previously-inline "assign an agent" text.

- [ ] **Step 1: Read the files**

Read `lifecycle.py` (constants ~38-52; `_welcome_text` ~70-80 — the reference pattern that already resolves a message via `proton.get_assistant_messages(inbox_id)` with fallback; use sites: `SURVEY_AI_DEFAULT`~248, `THANKS_DEFAULT`~265, `SURVEY_AGENT_DEFAULT`~299, inline "assign an agent"~239) and `lifecycle_scanner.py` (`IDLE_WARNING_DEFAULT`~154, `IDLE_CLOSE_DEFAULT`~160, `RESOLUTION_PROMPT_DEFAULT`~161). Note how each site obtains `inbox_id` + the `proton` client (the scanner already fetches per-conversation info; welcome already uses proton).

- [ ] **Step 2: Write the failing test**

```python
# test_lifecycle_messages.py
from app.services import lifecycle
from app.services.lifecycle import _resolve_message  # helper added in Step 4


def test_resolve_prefers_override() -> None:
    assert _resolve_message({"thanks": "Terima kasih!"}, "thanks", "Thank you!") == "Terima kasih!"


def test_resolve_falls_back_on_empty_or_missing() -> None:
    assert _resolve_message({"thanks": ""}, "thanks", "Thank you!") == "Thank you!"
    assert _resolve_message({}, "thanks", "Thank you!") == "Thank you!"
    assert _resolve_message(None, "thanks", "Thank you!") == "Thank you!"


def test_assign_agent_default_exists() -> None:
    assert lifecycle.ASSIGN_AGENT_DEFAULT.strip() != ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_messages.py -v`
Expected: FAIL — `_resolve_message` / `ASSIGN_AGENT_DEFAULT` undefined.

- [ ] **Step 4: Implement**

In `lifecycle.py` add the constant (promote the inline literal at ~239) and a small resolver:

```python
ASSIGN_AGENT_DEFAULT = "Thank you. We will assign an agent to assist you further."


def _resolve_message(messages: dict | None, key: str, default: str) -> str:
    """Operator override if present and non-empty, else the hard-coded default."""
    if messages:
        val = messages.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return default
```

Then at each customer-message send site, fetch the assistant messages once (mirror `_welcome_text`'s `proton.get_assistant_messages(inbox_id)`, fail-open to `{}`/`None`) and resolve:
- `lifecycle_scanner.py`: idle warning → `_resolve_message(msgs, "idle_warning", lifecycle.IDLE_WARNING_DEFAULT)`; idle close → `"idle_close"` / `IDLE_CLOSE_DEFAULT`; resolution prompt → `"resolution_prompt"` / `RESOLUTION_PROMPT_DEFAULT`.
- `lifecycle.py`: AI survey → `"survey_ai"` / `SURVEY_AI_DEFAULT`; agent survey → `"survey_agent"` / `SURVEY_AGENT_DEFAULT`; thanks → `"thanks"` / `THANKS_DEFAULT`; assign-agent (the ~239 literal) → `"assign_agent"` / `ASSIGN_AGENT_DEFAULT`.

Keep it fail-open: if the messages fetch raises or returns nothing, `_resolve_message(None, ...)` yields the default. Do not change the timing/flow — only the message string source.

- [ ] **Step 5: Run test + lifecycle regression**

Run: `cd agent && pytest tests/test_lifecycle_messages.py tests/test_lifecycle.py -v`
Expected: new tests PASS; existing lifecycle tests still PASS (empty overrides → default strings unchanged).

- [ ] **Step 6: Run the full agent suite (default-off proof)**

Run: `cd agent && pytest -q`
Expected: all pass — no regressions; behavior unchanged when no persona/overrides.

- [ ] **Step 7: Commit**

```bash
git add agent/app/services/lifecycle.py agent/app/services/lifecycle_scanner.py agent/tests/test_lifecycle_messages.py
git commit -m "feat(agent): operator-overridable lifecycle messages (fail-open defaults)"
```

---

## Task 6: Extend the KnowledgeSettings persona editor (fork patch 0022)

**Files:**
- Author in fork clone: `app/javascript/dashboard/components/proton/KnowledgeSettings.vue`
- Create: `deploy/chatwoot-fork/patches/0022-knowledge-persona-language-messages.patch`
- Modify: `deploy/chatwoot-fork/Dockerfile` (LABEL — add `0022`)

**Interfaces:**
- Consumes: the `config` fields from Task 1 (`language` + the 7 `*_message` fields). The editor already `PUT`s the whole `config` object, so binding new `v-model`s to `form.config.<field>` is sufficient.

- [ ] **Step 1: Read the existing editor**

In the fork clone `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot`, read `app/javascript/dashboard/components/proton/KnowledgeSettings.vue` — find the **Basic** section (name/description/product_name) and the **Messages** section (welcome/handoff/resolution). Note the exact input markup + classes + how `form.config` is bound and saved (`PUT /kb/assistants/{id}` with the config object).

- [ ] **Step 2: Add the Language field + 7 message fields**

- In the Basic/persona area, add a **Language** text input bound to `form.config.language`, placeholder e.g. `"Leave empty to reply in the customer's language (e.g. Bahasa Melayu)"`.
- In the existing **Messages** section, after resolution_message, add textareas/inputs (same markup as the existing three) bound to: `form.config.idle_warning_message`, `idle_close_message`, `resolution_prompt_message`, `survey_ai_message`, `survey_agent_message`, `thanks_message`, `assign_agent_message`, each with a clear label and a placeholder showing the current default text as guidance.
- Ensure these fields are included when the form initializes from a fetched assistant and when it builds the `PUT` config payload (mirror how welcome/handoff/resolution are wired — if the save path sends the whole `form.config`, no extra wiring is needed; if it whitelists keys, add the new keys).

- [ ] **Step 3: Build-verify in the fork clone**

Run: `cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot && NODE_OPTIONS=--max-old-space-size=4096 pnpm exec vite build`
Expected: build succeeds, 0 errors (bare-string warnings are fine). If pnpm/vite is unavailable, STOP and report — do not fabricate.

- [ ] **Step 4: Export the patch + register it**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git add app/javascript/dashboard/components/proton/KnowledgeSettings.vue
git commit --no-verify -m "feat(ui): persona language + lifecycle message fields"
git diff HEAD~1 HEAD -- app/javascript/dashboard/components/proton/KnowledgeSettings.vue \
  > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0022-knowledge-persona-language-messages.patch
```

Then in the main repo, add `0022` to the chatwoot-image Dockerfile LABEL (`deploy/chatwoot-fork/Dockerfile`). Confirm the patch file is non-empty and includes the new `v-model` bindings.

- [ ] **Step 5: Commit (main repo)**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0022-knowledge-persona-language-messages.patch deploy/chatwoot-fork/Dockerfile
git commit -m "feat(ui): persona language + lifecycle message fields (patch 0022)"
```

- [ ] **Step 6: Note remaining (user env)**

In the report, note REMAINING for the user: chatwoot image rebuild + Chrome smoke (edit a persona: set language + a couple messages, save, confirm `PUT` persists and the WhatsApp bot reflects them).

---

## Self-Review

**Spec coverage:**
- `language` + 7 message fields on `AssistantConfig` → Task 1. ✓
- Copilot + assist language injection → Task 2. ✓
- Agent↔backend bridge (persona + new messages) → Task 3. ✓
- Agent-bot SYSTEM_PROMPT persona wiring → Task 4. ✓
- Lifecycle messages overridable → Task 5. ✓
- UI in existing editor (no new page) → Task 6. ✓
- Zammad/categorize/email out of scope → not touched by any task. ✓
- Default-preserving + fail-open → Tasks 3-5 return defaults on None/empty; Tasks 1-2 default empty; verified by "verbatim/unchanged" tests. ✓

**Placeholder scan:** No "TBD"/"add error handling". The "read the real file and match the pattern" steps are deliberate (these files exist and the exact accumulator/method names must be read), with the new code + tests given verbatim — consistent with how the pgvector plan handled `main.py` wiring.

**Type consistency:** Field names identical across tasks (`language`, `*_message`); `get_assistant_messages` keys (`idle_warning`,`idle_close`,`resolution_prompt`,`survey_ai`,`survey_agent`,`thanks`,`assign_agent`) match between Task 3 (produce) and Task 5 (consume); `get_assistant_persona` dict shape (`instructions`/`guardrails`/`language`) matches between Task 3 (produce) and Task 4 (consume); `_build_system_prompt`/`_resolve_message` signatures match their tests.
