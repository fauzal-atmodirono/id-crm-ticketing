# Context-aware Reply Suggestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Chatwoot "Reply Suggestion" (`POST /assist/suggest`) read the whole conversation and connect the dots instead of parroting the handoff — by grounding KB retrieval on the customer's intent and rewriting the system prompt.

**Architecture:** Backend-only change confined to `backend/apps/backend/src/chatbot/features/assist/router.py`. Add a pure module-level `_retrieval_query()` helper that builds the KB search query from the customer's turns (not just the last line), and rewrite the `_SUGGEST_SYSTEM` prompt so the model synthesizes the full thread and never re-announces a handoff already present in the transcript. No request-model change, no Chatwoot fork patch, no fork-image rebuild.

**Tech Stack:** Python 3, FastAPI, google-genai (mocked in tests), pytest, ruff, mypy.

## Global Constraints

- All commands run from `backend/apps/backend/`.
- Tests must not hit real GCP/Gemini or a real KB — `genai_client` and `knowledge_port` are stubbed (see existing `test_assist_router.py`).
- Lint/format/type gates: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict` must all pass.
- Full suite command: `.venv/bin/pytest src/`.
- Conventional commit messages (`<type>(<scope>): <desc>`).
- Behaviour-preserving for the existing default path: messages with no `Customer:`-prefixed turn (e.g. `["Hi"]`) must fall back to `messages[-1]`, so existing tests keep passing.
- The `{faq_context}` placeholder in `_SUGGEST_SYSTEM` and the `.format(faq_context=...)` call site (`router.py:208`) must remain intact.

---

### Task 1: `_retrieval_query` helper + wire into `suggest()`

Ground KB retrieval on the customer's turns across the thread instead of the raw last message.

**Files:**
- Modify: `src/chatbot/features/assist/router.py` (add module-level helper near `_build_persona_prefix` at ~line 100; change `suggest()` at line 206)
- Test: `src/chatbot/features/assist/test_assist_router.py` (add tests)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `_retrieval_query(messages: list[str], max_turns: int = 6) -> str` — module-level pure function, importable as `from chatbot.features.assist.router import _retrieval_query`. Returns customer-only turns (leading `Customer:`/`Agent:` label stripped) joined by `\n`, capped to the last `max_turns`, in chronological order; returns `messages[-1]` when no `Customer:`-labelled turn exists.

- [ ] **Step 1: Write the failing tests**

Add to `src/chatbot/features/assist/test_assist_router.py`. First extend the import at the top of the file:

```python
from chatbot.features.assist.router import (
    _ASK_SYSTEM,
    _SUGGEST_SYSTEM,
    _SUMMARIZE_SYSTEM,
    _retrieval_query,
    build_assist_router,
)
```

Then append these tests to the end of the file:

```python
# ---------------------------------------------------------------------------
# _retrieval_query (pure helper) + grounding wiring
# ---------------------------------------------------------------------------


def test_retrieval_query_uses_customer_turns_only() -> None:
    messages = [
        "Customer: nak tanya spec S70",
        "Agent: Berikut spesifikasi Proton S70 ...",
        "Customer: saya nak test drive",
        "Customer: bangsar",
    ]
    q = _retrieval_query(messages)
    assert q == "nak tanya spec S70\nsaya nak test drive\nbangsar"
    assert "Berikut spesifikasi" not in q  # agent turn excluded


def test_retrieval_query_caps_to_max_turns() -> None:
    messages = [f"Customer: msg{i}" for i in range(10)]
    q = _retrieval_query(messages, max_turns=3)
    assert q == "msg7\nmsg8\nmsg9"


def test_retrieval_query_falls_back_to_last_message_when_no_customer_turn() -> None:
    # No "Customer:" label (e.g. the existing default-path shape ["Hi"]).
    assert _retrieval_query(["Hi"]) == "Hi"
    assert _retrieval_query(["Agent: hello", "Agent: still there?"]) == "Agent: still there?"


def test_suggest_grounds_kb_on_customer_intent_not_last_line() -> None:
    """KB search receives the customer's intent, not a lone trailing word."""
    class _RecordingKnowledge:
        def __init__(self) -> None:
            self.last_query: str | None = None

        async def search_kb(self, query: str, limit: int = 3) -> list:
            self.last_query = query
            return [KbArticle(title="FAQ Title", content="FAQ content body", url="http://faq/1")]

    kb = _RecordingKnowledge()
    mock_genai = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "reply"
    mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_response)

    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=_settings(),
            knowledge_port=kb,
            genai_client=mock_genai,
        )
    )
    client = TestClient(app)
    r = client.post(
        "/assist/suggest",
        json={
            "conversation_id": "1",
            "messages": [
                "Customer: nak test drive S70",
                "Agent: boleh, di dealer mana?",
                "Customer: bangsar",
            ],
        },
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    assert kb.last_query == "nak test drive S70\nbangsar"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/assist/test_assist_router.py -k "retrieval_query or grounds_kb" -v`
Expected: FAIL — `ImportError: cannot import name '_retrieval_query'` (collection error), and the grounding test would fail on the query assertion once import is fixed.

- [ ] **Step 3: Add the helper and wire it in**

In `src/chatbot/features/assist/router.py`, add this module-level function immediately after `_build_persona_prefix` (before `def build_assist_router`):

```python
def _retrieval_query(messages: list[str], max_turns: int = 6) -> str:
    """Build the KB query from the customer's turns, not just the last line.

    ``messages`` are ``"Customer: ..."`` / ``"Agent: ..."`` strings (see the
    Chatwoot composer). Grounding on the whole customer intent keeps retrieval
    from being derailed by a one-word last turn like "bangsar". Falls back to
    the last message when no customer-labelled turn is present, so callers that
    pass unlabelled messages behave exactly as before.
    """
    customer = [
        m.split(":", 1)[1].strip()
        for m in messages
        if ":" in m and m.split(":", 1)[0].strip().lower() == "customer"
    ]
    if not customer:
        return messages[-1]
    return "\n".join(customer[-max_turns:])
```

Then in `suggest()` replace line 206:

```python
        query = req.messages[-1]  # ground on the customer's latest message
```

with:

```python
        query = _retrieval_query(req.messages)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/assist/test_assist_router.py -k "retrieval_query or grounds_kb" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full assist suite (guard against regressions)**

Run: `.venv/bin/pytest src/chatbot/features/assist/ -v`
Expected: PASS — including `test_suggest_default_path_is_behaviour_preserving` (messages `["Hi"]` → fallback → query `"Hi"`, unchanged).

- [ ] **Step 6: Lint + type-check**

Run: `.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/assist/router.py src/chatbot/features/assist/test_assist_router.py
git commit -m "feat(assist): ground /suggest KB retrieval on customer intent

Replace query=messages[-1] with a _retrieval_query() helper that builds
the KB query from the customer's turns across the thread, so a one-word
last message like 'bangsar' no longer derails retrieval. Falls back to
messages[-1] when no Customer: turn is present (behaviour-preserving)."
```

---

### Task 2: Rewrite `_SUGGEST_SYSTEM` to read the whole thread and not repeat a handoff

Change what the drafter is told to do: synthesize the full conversation, and when the request is already complete and a handoff was already sent, confirm the specifics instead of re-announcing the handoff.

**Files:**
- Modify: `src/chatbot/features/assist/router.py:52-63` (the `_SUGGEST_SYSTEM` constant)
- Test: `src/chatbot/features/assist/test_assist_router.py` (add a prompt-content test)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the updated `_SUGGEST_SYSTEM` string (still a `.format()` template with a single `{faq_context}` field).

- [ ] **Step 1: Write the failing test**

Append to `src/chatbot/features/assist/test_assist_router.py`:

```python
# ---------------------------------------------------------------------------
# _SUGGEST_SYSTEM prompt content (connect-the-dots + no-duplicate-handoff)
# ---------------------------------------------------------------------------


def test_suggest_system_prompt_instructs_full_context_and_no_repeat_handoff() -> None:
    p = _SUGGEST_SYSTEM.lower()
    # Reads the whole conversation / connects the dots.
    assert "entire conversation" in p
    # Explicitly avoids re-announcing an already-sent handoff.
    assert "do not repeat" in p
    assert "handoff" in p
    # Existing guarantees are preserved.
    assert "exact same language" in p
    assert "return only the reply text" in p
    # Template placeholder still present.
    assert "{faq_context}" in _SUGGEST_SYSTEM
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/assist/test_assist_router.py -k "system_prompt_instructs" -v`
Expected: FAIL — `assert "entire conversation" in p` (current prompt says "the customer's latest message").

- [ ] **Step 3: Rewrite the prompt**

In `src/chatbot/features/assist/router.py`, replace the `_SUGGEST_SYSTEM` constant (lines 52-63) with:

```python
_SUGGEST_SYSTEM = (
    "You are a customer-support agent for Proton Holdings.\n"
    "Read the ENTIRE conversation below — not just the last line — and connect "
    "the dots: what the customer wants, which details they have already "
    "provided, and what the agent or bot has already said or done.\n\n"
    "Then write the single most useful next reply to the customer:\n"
    "- If the customer's request is already complete (for example, every detail "
    "for a booking or request has been collected) AND the agent/bot has already "
    "told them they are being connected to a human or will be contacted, do NOT "
    "repeat that handoff. Instead write a brief, specific confirmation that "
    "reflects the concrete details they gave (such as the model, the dealer or "
    "location, and how they will be contacted).\n"
    "- Otherwise, answer or advance the conversation using the FAQ context "
    "below.\n\n"
    "LANGUAGE (critical): reply in the EXACT SAME language as the customer's "
    "latest message. If they wrote in English, reply in English; if in Malay, "
    "reply in Malay. Never switch languages and never default to Malay when the "
    "customer wrote in another language. "
    "Do not include a salutation or sign-off. "
    "Return only the reply text, nothing else.\n\n"
    "FAQ context:\n{faq_context}"
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/assist/test_assist_router.py -k "system_prompt_instructs" -v`
Expected: PASS.

- [ ] **Step 5: Run the full assist suite**

Run: `.venv/bin/pytest src/chatbot/features/assist/ -v`
Expected: PASS. `test_suggest_default_path_is_behaviour_preserving` recomputes `_SUGGEST_SYSTEM.format(...)` dynamically, so the new prompt text does not break it. `test_suggest_includes_assistant_persona_in_system_prompt` asserts `"customer-support agent" in system` — still present in the new prompt.

- [ ] **Step 6: Lint + type-check**

Run: `.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/assist/router.py src/chatbot/features/assist/test_assist_router.py
git commit -m "feat(assist): rewrite /suggest prompt to synthesize full thread

The drafter now reads the entire conversation and connects the dots; when
the request is already complete and a handoff was already sent, it writes a
specific confirmation instead of re-announcing the handoff. Language-match,
no-salutation, and return-only-text rules are preserved."
```

---

### Task 3: Final full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire backend test suite**

Run: `.venv/bin/pytest src/`
Expected: PASS (no regressions anywhere in `backend/apps/backend`).

- [ ] **Step 2: Final lint + strict type-check**

Run: `.venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: no errors.

---

## Self-Review

**Spec coverage:**
- Spec §Design 1 (retrieval grounding) → Task 1. ✓
- Spec §Design 2 (prompt rewrite) → Task 2. ✓
- Spec §Design 3 (tests: query-builder pure tests, prompt-content assertions, handler wiring assertion) → Task 1 Steps 1 (query + grounding) and Task 2 Step 1 (prompt content). ✓
- Spec "Out of scope" (copilot, orchestrator, fork patch, summarize/ask) → untouched by all tasks. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N" — all code and commands are literal. ✓

**Type consistency:** `_retrieval_query(messages: list[str], max_turns: int = 6) -> str` is defined identically in the Interfaces block, the implementation (Task 1 Step 3), and all call/test sites. The `{faq_context}` template field is consistent across prompt, call site, and tests. ✓
