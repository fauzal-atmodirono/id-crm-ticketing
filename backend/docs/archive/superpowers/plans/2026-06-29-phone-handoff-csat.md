# Phone Handoff + CSAT (Follow-up) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add human handoff (callback + open Zendesk ticket) and in-call CSAT (spoken 1–5, AI-resolved calls only) to the existing real-time phone channel, driven by two new Gemini Live function tools.

**Architecture:** The phone channel runs through Gemini Live (not `handle_turn`), so handoff and CSAT are Live function tools the model calls — like the existing `kb_search`. `PhoneBridge` records the intent during the call and branches at `finalize`: handoff → ticket `open` + handoff note (no survey); AI-resolved → ticket `solved` + CSAT. CSAT recording is a shared module-level helper so the phone path and the existing `record_csat` emit the identical `csat_N` tag + comment.

**Tech Stack:** Python 3.12, `google-genai` 2.8.0 Live tools, FastAPI, pytest-asyncio, ruff, mypy --strict.

## Global Constraints

- Backend commands run from `apps/backend/`. Quality gates per task: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`, `.venv/bin/pytest src/`.
- `asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`.
- 3 pre-existing mypy errors (firestore_session_service.py, service.py, test_service.py) + 1 pre-existing ruff error (vertex_search.py PLC0415) live in unrelated files — do not fix; introduce no NEW ones.
- All external I/O (Zendesk via `ConversationLogPort`) is best-effort try/except + structlog; never crash a call.
- CSAT tag format MUST stay `csat_<score>` and the comment `⭐ Customer satisfaction: <score>/5 (via <channel>)` — identical to the existing `record_csat`, so the future dashboard reads one consistent scheme. Phone uses `channel="phone"`.
- New phone code lives under `apps/backend/src/chatbot/features/chat/phone/`.
- `session_id = "phone-<CallSid>"`; ticket `external_id = session_id`.
- Conventional commits.
- Out of scope (do not build): token-auth gating, DTMF, outbound/SMS survey for handed-off calls, real-PSTN caller number, the metrics dashboard.

---

### Task 1: Shared `record_csat_on_ticket` helper + refactor `record_csat`

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/csat.py`
- Modify: `apps/backend/src/chatbot/features/chat/service.py` (`record_csat`, ~lines 457-480)
- Test: `apps/backend/src/chatbot/features/chat/test_csat_helper.py` (create); existing `test_csat.py` is the regression guard.

**Interfaces:**
- Produces: `async def record_csat_on_ticket(port: ConversationLogPort, ticket_id: str, score: int, channel: str) -> None` — posts the `⭐ Customer satisfaction: <score>/5 (via <channel>)` comment + `add_ticket_tag(f"csat_{score}")`; best-effort try/except + log.
- `OrchestratorService.record_csat` delegates its ticket-write to this helper (behavior unchanged).

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_csat_helper.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock

from chatbot.features.chat.csat import record_csat_on_ticket


async def test_record_csat_on_ticket_posts_comment_and_tag() -> None:
    port = AsyncMock()
    await record_csat_on_ticket(port, "T-7", 5, "phone")
    port.append_conversation_comment.assert_awaited_once_with(
        "T-7", "⭐ Customer satisfaction: 5/5 (via phone)"
    )
    port.add_ticket_tag.assert_awaited_once_with("T-7", "csat_5")


async def test_record_csat_on_ticket_swallows_errors() -> None:
    port = AsyncMock()
    port.append_conversation_comment.side_effect = RuntimeError("zendesk down")
    # must not raise
    await record_csat_on_ticket(port, "T-7", 3, "phone")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_csat_helper.py -v`
Expected: FAIL (module `csat` not found).

- [ ] **Step 3: Create the helper**

Create `apps/backend/src/chatbot/features/chat/csat.py`:

```python
"""Shared CSAT recording: the channel-agnostic Zendesk write (comment + tag).

Used by both OrchestratorService.record_csat (text/WhatsApp/email/web) and the
phone bridge, so every channel emits the identical `csat_<score>` tag + comment
that the future metrics dashboard reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.features.chat.ports import ConversationLogPort

_log = structlog.get_logger(__name__)


async def record_csat_on_ticket(
    port: ConversationLogPort, ticket_id: str, score: int, channel: str
) -> None:
    """Post the CSAT comment + `csat_<score>` tag to a ticket. Best-effort."""
    try:
        await port.append_conversation_comment(
            ticket_id, f"⭐ Customer satisfaction: {score}/5 (via {channel})"
        )
        await port.add_ticket_tag(ticket_id, f"csat_{score}")
    except Exception as e:
        _log.error("record_csat_on_ticket_failed", ticket_id=ticket_id, error=str(e))
```

- [ ] **Step 4: Refactor `record_csat` to delegate**

In `service.py`, add the import near the other feature imports at the top:

```python
from chatbot.features.chat.csat import record_csat_on_ticket
```

In `record_csat` (around lines 465-473), replace the inline try/except ticket-write block:

```python
        ticket_id = state.get("conversation_ticket_id")
        if ticket_id:
            try:
                await self._conversation_log_port.append_conversation_comment(
                    ticket_id, f"⭐ Customer satisfaction: {score}/5 (via {channel})"
                )
                await self._conversation_log_port.add_ticket_tag(ticket_id, f"csat_{score}")
            except Exception as e:
                _log.error("record_csat_log_failed", session_id=session_id, error=str(e))
```

with:

```python
        ticket_id = state.get("conversation_ticket_id")
        if ticket_id:
            await record_csat_on_ticket(self._conversation_log_port, ticket_id, score, channel)
```

(Leave the rest of `record_csat` — the `state[...]` updates and `_persist_session_state` — unchanged.)

- [ ] **Step 5: Run the new test + the CSAT regression suite + gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_csat_helper.py src/chatbot/features/chat/test_csat.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS (helper tests + existing record_csat tests green — the refactor is behavior-preserving), clean.

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/csat.py src/chatbot/features/chat/service.py src/chatbot/features/chat/test_csat_helper.py
git commit -m "refactor(chat): extract shared record_csat_on_ticket helper"
```

---

### Task 2: Handoff + CSAT Live tool declarations + `parse_csat_score`

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/handoff_csat_tools.py`
- Test: `apps/backend/src/chatbot/features/chat/phone/test_handoff_csat_tools.py`

**Interfaces:**
- Produces: `REQUEST_HANDOFF_TOOL: types.Tool` (function `request_human_handoff(reason: str, summary: str)`); `SUBMIT_CSAT_TOOL: types.Tool` (function `submit_csat(score: int)`); `def parse_csat_score(args: dict[str, object]) -> int | None` (returns the int 1–5 or None).

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/phone/test_handoff_csat_tools.py`:

```python
import pytest

from chatbot.features.chat.phone.handoff_csat_tools import (
    REQUEST_HANDOFF_TOOL,
    SUBMIT_CSAT_TOOL,
    parse_csat_score,
)


def test_tools_declare_expected_function_names() -> None:
    handoff = [fd.name for fd in (REQUEST_HANDOFF_TOOL.function_declarations or [])]
    csat = [fd.name for fd in (SUBMIT_CSAT_TOOL.function_declarations or [])]
    assert "request_human_handoff" in handoff
    assert "submit_csat" in csat


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"score": 5}, 5),
        ({"score": 1}, 1),
        ({"score": 5.0}, 5),
        ({"score": "4"}, 4),
        ({"score": 0}, None),
        ({"score": 6}, None),
        ({"score": "x"}, None),
        ({}, None),
        ({"score": None}, None),
    ],
)
def test_parse_csat_score(args, expected) -> None:
    assert parse_csat_score(args) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_handoff_csat_tools.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the tools + parser**

Create `apps/backend/src/chatbot/features/chat/phone/handoff_csat_tools.py`:

```python
"""Gemini Live function tools for phone handoff + CSAT (the phone channel runs
through Live, not handle_turn, so these are how the model triggers them)."""

from __future__ import annotations

from google.genai import types

REQUEST_HANDOFF_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="request_human_handoff",
            description=(
                "Call this when you cannot resolve the caller's issue, they explicitly ask "
                "for a human, or it is a complaint or sensitive matter. A support ticket with "
                "the conversation is created and a human specialist will follow up."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reason": types.Schema(
                        type=types.Type.STRING, description="Short reason for the handoff."
                    ),
                    "summary": types.Schema(
                        type=types.Type.STRING,
                        description="A brief summary of what the caller needs help with.",
                    ),
                },
                required=["reason", "summary"],
            ),
        )
    ]
)

SUBMIT_CSAT_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="submit_csat",
            description=(
                "After the caller gives a 1 to 5 satisfaction rating at the end of a resolved "
                "call, call this with the number they said."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "score": types.Schema(
                        type=types.Type.INTEGER,
                        description="The caller's rating, an integer from 1 to 5.",
                    )
                },
                required=["score"],
            ),
        )
    ]
)


def parse_csat_score(args: dict[str, object]) -> int | None:
    """Validate the submit_csat `score` arg → int 1–5, else None."""
    raw = args.get("score")
    try:
        score = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return score if 1 <= score <= 5 else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_handoff_csat_tools.py -v`
Expected: PASS (11 cases).

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/phone/handoff_csat_tools.py src/chatbot/features/chat/phone/test_handoff_csat_tools.py
git commit -m "feat(phone): request_human_handoff + submit_csat Live tools + score parser"
```

---

### Task 3: PhoneBridge handoff/CSAT state + pump dispatch + finalize branching

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/phone/bridge.py`
- Test: `apps/backend/src/chatbot/features/chat/phone/test_bridge.py`

**Interfaces:**
- Consumes: `record_csat_on_ticket` (Task 1), `parse_csat_score` (Task 2), the `ToolCall` event, `ConversationLogPort`.
- `PhoneBridge` gains `self.handoff: dict[str, str] | None = None` and `self.csat_score: int | None = None`.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/src/chatbot/features/chat/phone/test_bridge.py` (the file already has `_FakeLive`, `_FakeLog`, `_bridge`, and imports `ToolCall`):

```python
async def test_pump_request_handoff_sets_state_and_responds() -> None:
    live = _FakeLive(
        [ToolCall(id="h1", name="request_human_handoff", args={"reason": "billing", "summary": "double charge"})]
    )
    b = _bridge(live, [])
    await b.pump()
    assert b.handoff == {"reason": "billing", "summary": "double charge"}
    assert live.tool_responses and live.tool_responses[0][0] == "h1"


async def test_pump_submit_csat_sets_score() -> None:
    live = _FakeLive([ToolCall(id="c1", name="submit_csat", args={"score": 5})])
    b = _bridge(live, [])
    await b.pump()
    assert b.csat_score == 5
    assert live.tool_responses and live.tool_responses[0][1] == "submit_csat"


async def test_pump_submit_csat_ignores_out_of_range() -> None:
    live = _FakeLive([ToolCall(id="c2", name="submit_csat", args={"score": 9})])
    b = _bridge(live, [])
    await b.pump()
    assert b.csat_score is None
    assert live.tool_responses  # still answered so the Live turn isn't stalled


async def test_finalize_handoff_opens_ticket_with_note_and_no_csat() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "I want a human")]
    b.handoff = {"reason": "complaint", "summary": "angry about delay"}
    b.csat_score = 5  # must be ignored on a handoff
    await b.finalize()
    # the handoff note + transcript are posted; the handoff note carries status "open"
    assert any("[Handoff to human agent]" in c[1] for c in log.comments)
    assert any(c[2] == "open" for c in log.comments)
    assert log.external_ids == [("T-1", "phone-C1")]
    assert log.tags == []  # NO csat tag on a handoff


async def test_finalize_resolved_with_score_solves_and_records_csat() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "thanks"), ("ASSISTANT", "you're welcome")]
    b.csat_score = 4
    await b.finalize()
    assert any(c[2] == "solved" for c in log.comments)
    assert ("T-1", "csat_4") in log.tags
    assert any("Customer satisfaction: 4/5 (via phone)" in c[1] for c in log.comments)


async def test_finalize_resolved_without_score_solves_no_csat() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "ok")]
    await b.finalize()
    assert any(c[2] == "solved" for c in log.comments)
    assert log.tags == []
```

The existing `_FakeLog` records `append_conversation_comment` calls into `self.comments` as `(ticket_id, text, status)` and returns `"T-1"` from `ensure_conversation_ticket`. Add a `tags` recorder to `_FakeLog` — its `add_ticket_tag` currently `...`-stubs; change it to:

```python
    async def add_ticket_tag(self, ticket_id, tag):
        self.tags.append((ticket_id, tag))
```

and initialise `self.tags: list = []` in `_FakeLog.__init__` (next to `self.comments`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_bridge.py -v`
Expected: FAIL (no `handoff`/`csat_score` attrs; tool names unhandled; finalize doesn't branch).

- [ ] **Step 3: Add state to `PhoneBridge.__init__`**

In `bridge.py` `__init__`, after `self.transcript: list[tuple[str, str]] = []`:

```python
        self.handoff: dict[str, str] | None = None
        self.csat_score: int | None = None
```

Add imports at the top of `bridge.py`:

```python
from chatbot.features.chat.csat import record_csat_on_ticket
from chatbot.features.chat.phone.handoff_csat_tools import parse_csat_score
```

- [ ] **Step 4: Handle the two new tools in `pump`**

In `pump`, replace the `ToolCall` branch (currently kb_search + unknown) with:

```python
            elif isinstance(event, ToolCall):
                if event.name == "kb_search":
                    result = await dispatch_kb_search(event.args, self._knowledge)
                    await self._live.send_tool_response(event.id, event.name, result)
                elif event.name == "request_human_handoff":
                    self.handoff = {
                        "reason": str(event.args.get("reason") or ""),
                        "summary": str(event.args.get("summary") or ""),
                    }
                    await self._live.send_tool_response(
                        event.id, event.name, {"status": "ticket_created"}
                    )
                elif event.name == "submit_csat":
                    score = parse_csat_score(event.args)
                    if score is not None:
                        self.csat_score = score
                    await self._live.send_tool_response(
                        event.id,
                        event.name,
                        {"status": "recorded" if score is not None else "ignored"},
                    )
                else:
                    _log.warning("phone_unknown_tool", name=event.name, call_id=event.id)
                    await self._live.send_tool_response(
                        event.id, event.name, {"error": f"unknown tool: {event.name}"}
                    )
```

- [ ] **Step 5: Branch `finalize` on handoff / CSAT**

Replace the body of `finalize` (after the `if not self.transcript or not self.call_sid: return` guard) with:

```python
        session_id = f"phone-{self.call_sid}"
        body = "\n".join(f"{role}: {text}" for role, text in self.transcript)
        status = "open" if self.handoff is not None else "solved"
        try:
            ticket_id = await self._log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=f"[phone] Conversation {session_id}",
                customer_name=None,
                customer_phone=None,
            )
            if self.handoff is not None:
                note = (
                    "[Handoff to human agent]\n"
                    f"{self.handoff.get('reason', '')}\n{self.handoff.get('summary', '')}"
                )
                await self._log_port.append_conversation_comment(ticket_id, note, status="open")
            await self._log_port.append_conversation_comment(ticket_id, body, status=status)
            await self._log_port.set_ticket_external_id(ticket_id, session_id)
            if self.handoff is None and self.csat_score is not None:
                await record_csat_on_ticket(self._log_port, ticket_id, self.csat_score, "phone")
        except Exception as e:
            _log.error(
                "phone_finalize_failed", session_id=session_id, error=str(e), transcript=body
            )
```

- [ ] **Step 6: Run tests + gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_bridge.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS (existing bridge tests + 6 new), clean.

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/phone/bridge.py src/chatbot/features/chat/phone/test_bridge.py
git commit -m "feat(phone): bridge handoff (open ticket) + CSAT (solved + csat tag) at finalize"
```

---

### Task 4: Wire the tools + system instruction into `phone_stream`

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py` (`phone_stream`)
- Test: `apps/backend/src/chatbot/features/chat/test_phone_stream.py`

**Interfaces:**
- Consumes: `KB_SEARCH_TOOL`, `REQUEST_HANDOFF_TOOL`, `SUBMIT_CSAT_TOOL`.
- The Live session is opened with all three tools and the handoff/CSAT-aware system instruction.

- [ ] **Step 1: Write the failing test**

The existing `test_phone_stream.py` has a fake `factory(settings, system_instruction, tools)`. Add a test that captures `tools` + `system_instruction` and asserts all three tools and the new guidance are passed. Add at the end of the file:

```python
def test_stream_opens_live_with_all_three_tools_and_handoff_instruction() -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def factory(settings, system_instruction, tools):  # noqa: ANN001
        captured["tools"] = tools
        captured["instruction"] = system_instruction
        yield _FakeLive([])

    orch = _orch()
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch, live_session_factory=factory))
    client = TestClient(app)
    with client.websocket_connect("/voice/phone/stream") as ws:
        ws.send_json({"event": "start", "start": {"streamSid": "S1", "callSid": "C1"}})

    names = [
        fd.name
        for tool in captured["tools"]  # type: ignore[union-attr]
        for fd in (tool.function_declarations or [])
    ]
    assert {"kb_search", "request_human_handoff", "submit_csat"} <= set(names)
    instruction = str(captured["instruction"]).lower()
    assert "human" in instruction and "rate" in instruction
```

(The `_FakeLive` here yields no events, so the WS handler reads the `start`, spawns the pump (which finishes immediately), tears down, and closes — no media frame is asserted.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_phone_stream.py::test_stream_opens_live_with_all_three_tools_and_handoff_instruction -v`
Expected: FAIL (only `kb_search` is passed; instruction lacks the guidance).

- [ ] **Step 3: Update `phone_stream`**

In `router.py`, add to the phone-tool imports (next to `KB_SEARCH_TOOL`):

```python
from chatbot.features.chat.phone.handoff_csat_tools import REQUEST_HANDOFF_TOOL, SUBMIT_CSAT_TOOL
```

In `phone_stream`, change the `system_instruction` string to:

```python
        system_instruction = (
            "You are Proton's friendly phone support agent. Answer spoken questions "
            "concisely and naturally. Use the kb_search tool to ground answers in the "
            "Proton knowledge base before giving facts. If you cannot resolve the caller's "
            "issue, they ask for a human, or it is a complaint or sensitive matter, call "
            "request_human_handoff with a short reason and summary, then tell the caller a "
            "specialist will follow up. When you have fully resolved the caller's question and "
            "the call is wrapping up, ask 'How would you rate your experience from 1 to 5?' and "
            "then call submit_csat with the number they say. Do NOT ask for a rating if you "
            "handed off to a human. No markdown — this is spoken aloud."
        )
```

And change the tools list passed to the factory from `[KB_SEARCH_TOOL]` to:

```python
                self.orchestrator._settings,
                system_instruction,
                [KB_SEARCH_TOOL, REQUEST_HANDOFF_TOOL, SUBMIT_CSAT_TOOL],
```

- [ ] **Step 4: Run the test + the full suite + gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_phone_stream.py -v && .venv/bin/pytest src/ && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS (report the count), clean.

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_phone_stream.py
git commit -m "feat(phone): open Live session with handoff+CSAT tools and guidance"
```

---

### Task 5: Docs — smoke-test scenarios + CHANGELOG

**Files:**
- Modify: `docs/testing/phone-channel-smoke-test.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add smoke-test scenarios**

In `docs/testing/phone-channel-smoke-test.md`, add two scenarios after the existing ones (match the file's checkbox + expected-results style):

- **Scenario E — Handoff:** on a call, say "I need to speak to a human / this is a complaint." Expected: the AI says a specialist will follow up; after hang-up a Zendesk ticket exists at status **open** with a `[Handoff to human agent]` note (reason + summary) plus the transcript, `external_id = phone-<CallSid>`, and **no** `csat_*` tag.
- **Scenario F — CSAT (AI-resolved):** on a call the AI fully answers, let it ask "rate 1 to 5" and say a number. Expected: after hang-up the ticket is **solved** with a `csat_<N>` tag and a `⭐ Customer satisfaction: N/5 (via phone)` comment. Also confirm a handed-off call (Scenario E) is **not** surveyed.

- [ ] **Step 2: CHANGELOG entry**

Add under `## [Unreleased]` (or a dated `## [2026-06-29]`) in *Added*:

```markdown
- **Phone channel — human handoff + CSAT.** The phone AI can now escalate via a
  `request_human_handoff` Live tool (creates an **open** Zendesk ticket with a handoff note
  + transcript for a human to follow up), and collects an in-call spoken 1–5 rating on
  AI-resolved calls via a `submit_csat` tool (recorded as the same `csat_N` tag + comment as
  every other channel, `channel="phone"`). Handed-off calls are not surveyed.
```

- [ ] **Step 3: Final full suite + gates**

Run (from `apps/backend/`): `.venv/bin/pytest src/ && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: PASS; only the documented pre-existing mypy/ruff errors remain.

- [ ] **Step 4: Commit**

```bash
git add docs/testing/phone-channel-smoke-test.md CHANGELOG.md
git commit -m "docs(phone): handoff + CSAT smoke-test scenarios + changelog"
```

---

## Self-Review Notes

- **Spec coverage:** Live tools → Task 2; bridge state + pump dispatch + finalize branching (open vs solved, CSAT) → Task 3; shared `record_csat_on_ticket` (unified `csat_N`) → Task 1; tools + system instruction wired into the Live session → Task 4; smoke test + changelog → Task 5. AI-resolved-only CSAT is enforced by the `self.handoff is None` guard in Task 3's finalize. All spec sections covered.
- **Type consistency:** `record_csat_on_ticket(port, ticket_id, score, channel)` used identically in Tasks 1 and 3; `parse_csat_score(args) -> int | None` in Tasks 2 and 3; `REQUEST_HANDOFF_TOOL`/`SUBMIT_CSAT_TOOL` in Tasks 2 and 4; `self.handoff`/`self.csat_score` consistent across Task 3; `csat_<score>` tag + `via <channel>` comment identical to the existing `record_csat`.
- **No phone ADK session needed:** the bridge records CSAT directly through `ConversationLogPort` via the module-level helper — no `OrchestratorService`/ADK-session dependency added to the bridge.
- **Out of scope honored:** no token-auth, DTMF, outbound survey, PSTN number, or dashboard work.
