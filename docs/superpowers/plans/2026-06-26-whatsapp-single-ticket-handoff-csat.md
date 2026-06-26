# WhatsApp Single-Ticket Handoff + Channel-Agnostic CSAT — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate each WhatsApp conversation into a single Zendesk ticket that is both the conversation log and the live-handoff channel, notify the customer on handoff, and add a channel-agnostic CSAT survey (WhatsApp text + custom web UI widget) on handback.

**Architecture:** The existing capture ticket (Support API, `ConversationLogPort`) becomes the single hub. A per-session state machine (`active → paused → awaiting_survey → active`) lives in the Firestore-persisted ADK session state. The WhatsApp inbound webhook routes by state; the human agent's reply relays back via the existing Support webhook; handback triggers a CSAT survey recorded through one shared `record_csat`. WhatsApp handoff bypasses Sunshine; the web frontend's Sunshine handoff is unchanged except for the added survey.

**Tech Stack:** Python 3.12, FastAPI, httpx (async), Google ADK, pytest + pytest-asyncio, ruff, mypy --strict; Vue 3 + Pinia + TypeScript (vue-tsc).

**Spec:** `docs/superpowers/specs/2026-06-26-whatsapp-single-ticket-handoff-csat-design.md`

## Global Constraints

- **Backend** run from `apps/backend/`; all new code passes `.venv/bin/ruff check .` and `.venv/bin/mypy src/ --strict`. Tests: `.venv/bin/pytest src/`. Tests co-located as `test_*.py`. Async tests use `@pytest.mark.asyncio` (`asyncio_mode = "auto"`).
- **Frontend** run from `apps/frontend/`; `npm run type-check` (vue-tsc strict) must pass.
- **Additive / scoped:** do NOT change the web/Sunshine handoff behaviour except adding the survey. Branch WhatsApp logic on `session_id.startswith("whatsapp-")`.
- **Best-effort I/O:** Zendesk/Twilio calls are wrapped in try/except and logged; they never raise out of a webhook.
- **Session-state keys** (persisted via `_persist_session_state`): `whatsapp_handoff_state` (`"active"|"paused"|"awaiting_survey"`), `csat_score` (int), `csat_nudged` (bool). Reuse existing `conversation_ticket_id`, `conversation_logged_count`, `handoff_triggered`, `chat_history`.
- **Commits:** conventional format; commit at the end of each task. Work on branch `feat/whatsapp-twilio-zendesk`.

---

### Task 1: Zendesk `add_ticket_tag` on ConversationLogPort

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/ports.py`
- Modify: `apps/backend/src/chatbot/features/chat/adapters/zendesk.py`
- Modify: `apps/backend/src/chatbot/features/chat/adapters/noop_conversation_log.py`
- Test: `apps/backend/src/chatbot/features/chat/adapters/test_zendesk_csat_tag.py`

**Interfaces:**
- Produces: `ConversationLogPort.add_ticket_tag(ticket_id: str, tag: str) -> None`; implemented by `ZendeskAdapter` (additive `PUT /api/v2/tickets/{id}/tags.json`) and `NoOpConversationLog`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/adapters/test_zendesk_csat_tag.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.features.chat.adapters.zendesk import ZendeskAdapter


def _adapter() -> ZendeskAdapter:
    settings = SimpleNamespace(
        zendesk_subdomain="proton",
        zendesk_email="agent@proton.test",
        zendesk_api_token="tok",
    )
    return ZendeskAdapter(settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_add_ticket_tag_puts_to_tags_endpoint() -> None:
    adapter = _adapter()
    response = MagicMock(status_code=200, raise_for_status=lambda: None)
    client = MagicMock()
    client.put = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        await adapter.add_ticket_tag("4242", "csat_4")

    url = client.put.call_args.args[0]
    assert url == "https://proton.zendesk.com/api/v2/tickets/4242/tags.json"
    assert client.put.call_args.kwargs["json"] == {"tags": ["csat_4"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_zendesk_csat_tag.py -v`
Expected: FAIL — `AttributeError: add_ticket_tag`.

- [ ] **Step 3: Add the port method**

In `ports.py`, inside the `ConversationLogPort` Protocol (after `append_conversation_comment`):

```python
    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        """Add a single tag to the ticket (additive; does not replace tags)."""
        ...
```

- [ ] **Step 4: Implement on `ZendeskAdapter`**

In `zendesk.py`, after `append_conversation_comment`:

```python
    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}/tags.json"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url, json={"tags": [tag]}, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
        except Exception as e:
            _log.error("zendesk_add_ticket_tag_failed", ticket_id=ticket_id, error=str(e))
```

- [ ] **Step 5: Implement on `NoOpConversationLog`**

In `noop_conversation_log.py`, add:

```python
    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:  # noqa: ARG002
        return None
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_zendesk_csat_tag.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/ports.py src/chatbot/features/chat/adapters/zendesk.py src/chatbot/features/chat/adapters/noop_conversation_log.py src/chatbot/features/chat/adapters/test_zendesk_csat_tag.py
git commit -m "feat(chat): add ConversationLogPort.add_ticket_tag for CSAT tagging"
```

---

### Task 2: CSAT parse + shared `record_csat` (service.py)

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py`
- Test: `apps/backend/src/chatbot/features/chat/test_csat.py`

**Interfaces:**
- Consumes: `ConversationLogPort.append_conversation_comment`, `.add_ticket_tag` (Task 1); `_persist_session_state`.
- Produces (on `OrchestratorService`):
  - module constants `WHATSAPP_ACTIVE = "active"`, `WHATSAPP_PAUSED = "paused"`, `WHATSAPP_AWAITING_SURVEY = "awaiting_survey"`, `_HANDOFF_STATE_KEY = "whatsapp_handoff_state"`.
  - `@staticmethod parse_csat(text: str) -> int | None` — first standalone digit in 1–5, else None.
  - `async record_csat(session_id: str, score: int, channel: str = "whatsapp") -> bool` — posts private comment `⭐ Customer satisfaction: {score}/5 (via {channel})`, adds tag `csat_{score}`, stores `csat_score`, sets state `active`, clears `csat_nudged`; persists. Returns False if no session.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_csat.py`:

```python
from __future__ import annotations

import pytest

from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.test_capture_conversation import (  # reuse doubles
    _FakeLog,
    _LiveSessions,
    _orchestrator,
)


def test_parse_csat_accepts_1_to_5() -> None:
    assert OrchestratorService.parse_csat("4") == 4
    assert OrchestratorService.parse_csat("I'd say 5, thanks") == 5
    assert OrchestratorService.parse_csat("rating: 1") == 1


def test_parse_csat_rejects_out_of_range_or_text() -> None:
    assert OrchestratorService.parse_csat("great service") is None
    assert OrchestratorService.parse_csat("10") is None
    assert OrchestratorService.parse_csat("") is None


@pytest.mark.asyncio
async def test_record_csat_writes_comment_tag_and_resets_state() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot", user_id="whatsapp-+60123", session_id="whatsapp-+60123",
        state={"conversation_ticket_id": "T1", "whatsapp_handoff_state": "awaiting_survey"},
    )

    ok = await orch.record_csat("whatsapp-+60123", 4, channel="whatsapp")

    assert ok is True
    assert session.state["csat_score"] == 4
    assert session.state["whatsapp_handoff_state"] == "active"
    assert any("4/5" in text for _t, text, _s in log.appended)
    assert ("T1", "csat_4") in log.tags
```

> Note: `_FakeLog` gains a `tags` list and an `add_ticket_tag` method in Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_csat.py -v`
Expected: FAIL — `AttributeError: parse_csat` / `record_csat`.

- [ ] **Step 3: Extend `_FakeLog` to record tags**

In `apps/backend/src/chatbot/features/chat/test_capture_conversation.py`, add to `_FakeLog.__init__`: `self.tags: list[tuple[str, str]] = []`, and add the method:

```python
    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        self.tags.append((ticket_id, tag))
```

- [ ] **Step 4: Add constants + methods to `OrchestratorService`**

Near the top of `service.py` (after imports), add module constants:

```python
WHATSAPP_ACTIVE = "active"
WHATSAPP_PAUSED = "paused"
WHATSAPP_AWAITING_SURVEY = "awaiting_survey"
_HANDOFF_STATE_KEY = "whatsapp_handoff_state"
```

Ensure `import re` is present at the top of `service.py` (add if missing). Add these methods to `OrchestratorService` (after `capture_conversation`):

```python
    @staticmethod
    def parse_csat(text: str) -> int | None:
        """Return the first standalone 1–5 rating in the text, else None."""
        for token in re.findall(r"\d+", text or ""):
            if token in {"1", "2", "3", "4", "5"}:
                return int(token)
        return None

    async def record_csat(self, session_id: str, score: int, channel: str = "whatsapp") -> bool:
        """Record a CSAT score: private comment + tag + session state; resume AI."""
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return False
        state = session.state
        ticket_id = state.get("conversation_ticket_id")
        if ticket_id:
            await self._conversation_log_port.append_conversation_comment(
                ticket_id, f"⭐ Customer satisfaction: {score}/5 (via {channel})"
            )
            await self._conversation_log_port.add_ticket_tag(ticket_id, f"csat_{score}")
        state["csat_score"] = score
        state[_HANDOFF_STATE_KEY] = WHATSAPP_ACTIVE
        state.pop("csat_nudged", None)
        await self._persist_session_state(session)
        return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_csat.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/test_csat.py src/chatbot/features/chat/test_capture_conversation.py
git commit -m "feat(chat): add parse_csat + shared record_csat with state reset"
```

---

### Task 3: WhatsApp state reader + `begin_whatsapp_handoff` + skip-Sunshine branch

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py`
- Test: `apps/backend/src/chatbot/features/chat/test_whatsapp_handoff_state.py`

**Interfaces:**
- Consumes: constants + `_persist_session_state`, `_conversation_log_port`, `_handoff_triggered` (existing, line ~222).
- Produces:
  - `async whatsapp_state(session_id: str) -> str` — returns the persisted state or `WHATSAPP_ACTIVE`.
  - `async begin_whatsapp_handoff(session_id: str, summary: str, *, customer_name: str | None = None, customer_phone: str | None = None) -> None` — ensure ticket, set `open`, post handoff summary note, set state `paused`, persist.
  - `async needs_whatsapp_handoff(session_id: str) -> bool` — True when `handoff_triggered` is set AND state is `active`.
  - Behaviour change: `handle_turn` skips `_escalate_handoff` for `whatsapp-*` sessions.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_whatsapp_handoff_state.py`:

```python
from __future__ import annotations

import pytest

from chatbot.features.chat.test_capture_conversation import _FakeLog, _LiveSessions, _orchestrator


@pytest.mark.asyncio
async def test_whatsapp_state_defaults_active() -> None:
    orch = _orchestrator(_FakeLog())
    assert await orch.whatsapp_state("whatsapp-+60000") == "active"


@pytest.mark.asyncio
async def test_begin_handoff_opens_ticket_and_pauses() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot", user_id="whatsapp-+60123", session_id="whatsapp-+60123",
        state={"chat_history": [], "handoff_triggered": True},
    )

    assert await orch.needs_whatsapp_handoff("whatsapp-+60123") is True
    await orch.begin_whatsapp_handoff("whatsapp-+60123", "Customer wants a human.")

    assert session.state["whatsapp_handoff_state"] == "paused"
    assert log.ensure_calls == 1
    _ticket, body, status = log.appended[0]
    assert status == "open"
    assert "Customer wants a human." in body
    # Once paused, it no longer "needs" a fresh handoff.
    assert await orch.needs_whatsapp_handoff("whatsapp-+60123") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_whatsapp_handoff_state.py -v`
Expected: FAIL — `AttributeError: whatsapp_state`.

- [ ] **Step 3: Add the methods to `OrchestratorService`**

After `record_csat` in `service.py`:

```python
    async def whatsapp_state(self, session_id: str) -> str:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return WHATSAPP_ACTIVE
        return str(session.state.get(_HANDOFF_STATE_KEY) or WHATSAPP_ACTIVE)

    async def needs_whatsapp_handoff(self, session_id: str) -> bool:
        if await self.whatsapp_state(session_id) != WHATSAPP_ACTIVE:
            return False
        return await self._handoff_triggered(session_id)

    async def begin_whatsapp_handoff(
        self,
        session_id: str,
        summary: str,
        *,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        state = session.state
        ticket_id = state.get("conversation_ticket_id")
        if not ticket_id:
            ticket_id = await self._conversation_log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=f"[WhatsApp] Conversation {session_id}",
                customer_name=customer_name,
                customer_phone=customer_phone,
            )
            state["conversation_ticket_id"] = ticket_id
        await self._conversation_log_port.append_conversation_comment(
            ticket_id, f"[Handoff to human agent]\n{summary}", status="open"
        )
        state[_HANDOFF_STATE_KEY] = WHATSAPP_PAUSED
        await self._persist_session_state(session)
```

- [ ] **Step 4: Skip Sunshine escalation for WhatsApp in `handle_turn`**

In `service.py` around line 346–351, change:

```python
        handoff_payload = None
        if session_state.get("handoff_triggered") is True:
            reason = session_state.get("handoff_reason", "help_request")
            handoff_payload = await self._escalate_handoff(session_id, reason)
```

to (guard the Sunshine escalation; WhatsApp handoff is handled by the webhook):

```python
        handoff_payload = None
        if session_state.get("handoff_triggered") is True and not session_id.startswith("whatsapp-"):
            reason = session_state.get("handoff_reason", "help_request")
            handoff_payload = await self._escalate_handoff(session_id, reason)
```

(Keep the exact `reason = ...` line that already exists; only add the `and not session_id.startswith("whatsapp-")` guard.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_whatsapp_handoff_state.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/test_whatsapp_handoff_state.py
git commit -m "feat(chat): WhatsApp handoff state + begin_whatsapp_handoff (skip Sunshine)"
```

---

### Task 4: Forward paused customer messages to the agent

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py`
- Test: `apps/backend/src/chatbot/features/chat/test_forward_to_agent.py`

**Interfaces:**
- Produces: `async forward_whatsapp_to_agent(session_id: str, text: str) -> None` — posts a private note `Customer (WhatsApp): {text}` on the session's capture ticket. No-op if no ticket.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_forward_to_agent.py`:

```python
from __future__ import annotations

import pytest

from chatbot.features.chat.test_capture_conversation import _FakeLog, _LiveSessions, _orchestrator


@pytest.mark.asyncio
async def test_forward_posts_prefixed_private_note() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    await sessions.create_session(
        app_name="chatbot", user_id="whatsapp-+60123", session_id="whatsapp-+60123",
        state={"conversation_ticket_id": "T1"},
    )

    await orch.forward_whatsapp_to_agent("whatsapp-+60123", "is my car ready?")

    assert log.appended[-1][0] == "T1"
    assert "Customer (WhatsApp): is my car ready?" in log.appended[-1][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_forward_to_agent.py -v`
Expected: FAIL — `AttributeError: forward_whatsapp_to_agent`.

- [ ] **Step 3: Implement**

After `begin_whatsapp_handoff` in `service.py`:

```python
    async def forward_whatsapp_to_agent(self, session_id: str, text: str) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        ticket_id = session.state.get("conversation_ticket_id")
        if not ticket_id:
            return
        await self._conversation_log_port.append_conversation_comment(
            ticket_id, f"Customer (WhatsApp): {text}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_forward_to_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/test_forward_to_agent.py
git commit -m "feat(chat): forward paused WhatsApp messages to agent as private notes"
```

---

### Task 5: `begin_survey` + WhatsApp inbound webhook state routing

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py` (add `begin_survey`)
- Modify: `apps/backend/src/chatbot/features/chat/router.py`
- Test: `apps/backend/src/chatbot/features/chat/test_router_whatsapp_states.py`

**Interfaces:**
- Consumes: `whatsapp_state`, `needs_whatsapp_handoff`, `begin_whatsapp_handoff`, `forward_whatsapp_to_agent`, `parse_csat`, `record_csat` (Tasks 2–4).
- Produces:
  - `OrchestratorService.begin_survey(session_id: str) -> None` — set state `awaiting_survey`, persist.
  - Router module constants `_HANDOFF_MESSAGE`, `_SURVEY_MESSAGE`, `_CSAT_NUDGE`, `_CSAT_THANKS`.
  - `twilio_whatsapp_webhook` routes by `whatsapp_state`; survey handling helper `_handle_whatsapp_survey`.

- [ ] **Step 1: Add `begin_survey` to `OrchestratorService`**

In `service.py`, after `forward_whatsapp_to_agent`:

```python
    async def begin_survey(self, session_id: str) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        session.state[_HANDOFF_STATE_KEY] = WHATSAPP_AWAITING_SURVEY
        session.state.pop("csat_nudged", None)
        await self._persist_session_state(session)
```

- [ ] **Step 2: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_router_whatsapp_states.py`:

```python
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import base64
import hashlib
import hmac

from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeRunner:
    async def run_async(self, **_: Any) -> AsyncGenerator[Any, None]:
        for _i in range(0):
            yield None


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()


def _setup() -> tuple[TestClient, OrchestratorService, AsyncMock]:
    settings = get_settings()
    settings.twilio_auth_token = "tok"
    settings.twilio_account_sid = "AC1"
    settings.twilio_whatsapp_number = "whatsapp:+60111"
    settings.twilio_webhook_base_url = ""
    orch = OrchestratorService(
        settings=settings, chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(), knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(), runner_factory=lambda _a: _FakeRunner(),
    )
    twilio = AsyncMock()
    app = create_app(settings)
    app.include_router(build_chat_router(orch, twilio_adapter=twilio))
    return TestClient(app), orch, twilio


def _post(client: TestClient, body: str, frm: str = "whatsapp:+60123") -> None:
    params = {"From": frm, "Body": body, "MessageSid": "SM1"}
    sig = _sign("tok", "http://testserver/webhooks/twilio-whatsapp", params)
    res = client.post("/webhooks/twilio-whatsapp", data=params, headers={"X-Twilio-Signature": sig})
    assert res.status_code == 200


def test_paused_session_forwards_and_skips_ai() -> None:
    client, orch, twilio = _setup()
    orch.handle_turn = AsyncMock()  # type: ignore[method-assign]
    orch.whatsapp_state = AsyncMock(return_value="paused")  # type: ignore[method-assign]
    orch.forward_whatsapp_to_agent = AsyncMock()  # type: ignore[method-assign]

    _post(client, "any update?")

    orch.handle_turn.assert_not_awaited()
    orch.forward_whatsapp_to_agent.assert_awaited_once()
    twilio.send_message.assert_not_awaited()


def test_survey_valid_rating_records_and_thanks() -> None:
    client, orch, twilio = _setup()
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")  # type: ignore[method-assign]
    orch.record_csat = AsyncMock(return_value=True)  # type: ignore[method-assign]

    _post(client, "5")

    orch.record_csat.assert_awaited_once()
    assert orch.record_csat.await_args.args[1] == 5
    twilio.send_message.assert_awaited()  # thank-you sent
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_router_whatsapp_states.py -v`
Expected: FAIL — current webhook always calls `handle_turn`.

- [ ] **Step 4: Add router constants + survey helper + route by state**

In `router.py`, add module constants near the other helpers:

```python
_HANDOFF_MESSAGE = (
    "You're being connected to a human agent who will continue helping you here shortly. \U0001f9d1‍\U0001f4bc"
)
_SURVEY_MESSAGE = (
    "Thanks for chatting with our support team! How would you rate your experience? "
    "Reply with a number 1–5 (1 = poor, 5 = excellent)."
)
_CSAT_NUDGE = "Please reply with a number 1–5."
_CSAT_THANKS = (
    "Thank you for your feedback! \U0001f64f You're back with our automated assistant "
    "whenever you need anything."
)
```

Replace the body of `twilio_whatsapp_webhook` after `session_id`/`profile_name`/`e164` are computed (keep the signature-verify + early-return-on-empty parts unchanged) so the turn runs through state routing:

```python
        state = await self.orchestrator.whatsapp_state(session_id)
        if state == "awaiting_survey":
            await self._handle_whatsapp_survey(from_addr, session_id, body)
            return Response(status_code=200)
        if state == "paused":
            await self.orchestrator.forward_whatsapp_to_agent(session_id, body)
            return Response(status_code=200)

        result = await self.orchestrator.handle_turn(session_id=session_id, text=body)

        if await self.orchestrator.needs_whatsapp_handoff(session_id):
            summary = result.reply or "Customer requested a human agent."
            await self.orchestrator.begin_whatsapp_handoff(
                session_id, summary, customer_name=profile_name, customer_phone=e164
            )
            if self._twilio_adapter is not None:
                await self._twilio_adapter.send_message(
                    conversation_id=from_addr, text=_HANDOFF_MESSAGE
                )
        else:
            await self._send_whatsapp_reply(from_addr, result)

        await self.orchestrator.capture_conversation(
            session_id, channel="WhatsApp", customer_name=profile_name, customer_phone=e164
        )
        return Response(status_code=200)

    async def _handle_whatsapp_survey(self, from_addr: str, session_id: str, body: str) -> None:
        score = OrchestratorService.parse_csat(body)
        if score is not None:
            await self.orchestrator.record_csat(session_id, score, channel="WhatsApp")
            if self._twilio_adapter is not None:
                await self._twilio_adapter.send_message(conversation_id=from_addr, text=_CSAT_THANKS)
            return
        # Invalid: nudge once, then stop trapping the customer.
        if await self.orchestrator.consume_survey_nudge(session_id):
            if self._twilio_adapter is not None:
                await self._twilio_adapter.send_message(conversation_id=from_addr, text=_CSAT_NUDGE)
            return
        # Second invalid → resume AI and process this message normally.
        await self.orchestrator.resume_ai(session_id)
        result = await self.orchestrator.handle_turn(session_id=session_id, text=body)
        await self._send_whatsapp_reply(from_addr, result)
        await self.orchestrator.capture_conversation(session_id, channel="WhatsApp")
```

Add `OrchestratorService` import usage (it is already imported in `router.py`).

- [ ] **Step 5: Add `consume_survey_nudge` + `resume_ai` to `OrchestratorService`**

In `service.py`, after `begin_survey`:

```python
    async def consume_survey_nudge(self, session_id: str) -> bool:
        """Return True the first time (and mark nudged); False thereafter."""
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return False
        if session.state.get("csat_nudged"):
            return False
        session.state["csat_nudged"] = True
        await self._persist_session_state(session)
        return True

    async def resume_ai(self, session_id: str) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        session.state[_HANDOFF_STATE_KEY] = WHATSAPP_ACTIVE
        session.state.pop("csat_nudged", None)
        await self._persist_session_state(session)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_router_whatsapp_states.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/router.py src/chatbot/features/chat/test_router_whatsapp_states.py
git commit -m "feat(chat): route WhatsApp inbound by handoff/survey state"
```

---

### Task 6: Relay agent replies to WhatsApp via the Support webhook

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py`
- Test: `apps/backend/src/chatbot/features/chat/test_zendesk_support_whatsapp_relay.py`

**Interfaces:**
- Consumes: `payload.session_id` (`ZendeskSupportWebhookPayload`), `payload`'s comment text field (existing), `self._twilio_adapter`, `_sanitize_for_whatsapp`.
- Produces: `zendesk_support_webhook` relays the agent comment to Twilio when `session_id.startswith("whatsapp-")`, in addition to the existing `sim-` SSE behaviour.

- [ ] **Step 1: Inspect the existing handler and payload**

Read `zendesk_support_webhook` in `router.py` and `ZendeskSupportWebhookPayload` in `schemas.py` to confirm the comment-text field name (referred to below as `payload.comment_text` — adjust to the real attribute). The handler currently returns early unless `session_id.startswith("sim-")`.

- [ ] **Step 2: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_zendesk_support_whatsapp_relay.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(twilio: AsyncMock, handoff_bridge: MagicMock) -> TestClient:
    settings = get_settings()
    settings.zendesk_support_webhook_secret = ""  # skip signature for the test
    app = create_app(settings)
    app.include_router(
        build_chat_router(MagicMock(), handoff_bridge=handoff_bridge, twilio_adapter=twilio)
    )
    return TestClient(app)


def test_agent_reply_on_support_ticket_relayed_to_whatsapp() -> None:
    twilio = AsyncMock()
    handoff = MagicMock()
    client = _client(twilio, handoff)

    # Body shape must match ZendeskSupportWebhookPayload; fill required fields.
    res = client.post(
        "/webhooks/zendesk-support",
        json={"session_id": "whatsapp-+60123", "comment_text": "Hi, your car is ready."},
    )

    assert res.status_code == 200
    twilio.send_message.assert_awaited_once()
    assert twilio.send_message.await_args.kwargs["conversation_id"] == "whatsapp:+60123"
    assert "your car is ready" in twilio.send_message.await_args.kwargs["text"]
```

> Adjust the JSON keys to the real `ZendeskSupportWebhookPayload` fields discovered in Step 1.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_zendesk_support_whatsapp_relay.py -v`
Expected: FAIL — session not `sim-`, returns ignored; Twilio not called.

- [ ] **Step 4: Add the WhatsApp relay branch**

In `zendesk_support_webhook`, where it currently checks `session_id.startswith("sim-")`, add a WhatsApp branch BEFORE that check (using the real comment-text attribute):

```python
        if session_id.startswith("whatsapp-") and self._twilio_adapter is not None:
            to = "whatsapp:" + session_id[len("whatsapp-") :]
            await self._twilio_adapter.send_message(
                conversation_id=to, text=_sanitize_for_whatsapp(payload.comment_text)
            )
            return {"status": "relayed"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_zendesk_support_whatsapp_relay.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_zendesk_support_whatsapp_relay.py
git commit -m "feat(chat): relay Support-ticket agent replies to WhatsApp"
```

---

### Task 7: Handback triggers CSAT (WhatsApp text + web survey event)

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/handoff_bridge.py` (survey event)
- Modify: `apps/backend/src/chatbot/features/chat/router.py` (handback webhook + SSE serialization)
- Test: `apps/backend/src/chatbot/features/chat/test_handback_csat.py`

**Interfaces:**
- Produces:
  - `HandoffBridge.publish_survey(session_id: str) -> None` — enqueue a `SurveyEvent()` sentinel to that session's subscriber queues.
  - `chat_stream` emits `event: survey\ndata: {"type":"survey"}` for a `SurveyEvent`.
  - `zendesk_handback_webhook`: for `whatsapp-*` → `begin_survey` + send `_SURVEY_MESSAGE` via Twilio; for other sessions → `begin_survey` + `publish_survey` (after the existing unpause behaviour).

- [ ] **Step 1: Add a `SurveyEvent` and `publish_survey`**

In `handoff_bridge.py`, add a tiny event type and method. Define near the top:

```python
class SurveyEvent:
    """Sentinel pushed to SSE subscribers to request a CSAT rating."""
```

Change the subscriber queue type to `asyncio.Queue[AgentMessageEvent | SurveyEvent | None]` (update `_subscribers` annotation and `subscribe` return type). Add:

```python
    async def publish_survey(self, session_id: str) -> None:
        for queue in list(self._subscribers.get(session_id, [])):
            try:
                queue.put_nowait(SurveyEvent())
            except asyncio.QueueFull:
                _log.warning("survey_publish_queue_full", session_id=session_id)
```

- [ ] **Step 2: Serialize the survey event in `chat_stream`**

In `router.py` `chat_stream` `event_source`, import `SurveyEvent` and handle it before the agent-message branch:

```python
                    from chatbot.features.chat.handoff_bridge import SurveyEvent

                    if isinstance(event, SurveyEvent):
                        yield b"event: survey\ndata: {\"type\": \"survey\"}\n\n"
                        continue
```

- [ ] **Step 3: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_handback_csat.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock, twilio: AsyncMock, handoff: MagicMock) -> TestClient:
    settings = get_settings()
    settings.zendesk_support_webhook_secret = ""
    app = create_app(settings)
    app.include_router(
        build_chat_router(orch, handoff_bridge=handoff, twilio_adapter=twilio)
    )
    return TestClient(app)


def test_handback_whatsapp_starts_text_survey() -> None:
    orch = MagicMock()
    orch._settings = get_settings()
    orch.begin_survey = AsyncMock()
    orch.unpause = AsyncMock()  # whatever the existing handler calls; mock it out
    twilio = AsyncMock()
    handoff = MagicMock()
    handoff.publish_survey = AsyncMock()

    client = _client(orch, twilio, handoff)
    res = client.post("/webhooks/zendesk-handback", json={"session_id": "whatsapp-+60123"})

    assert res.status_code == 200
    orch.begin_survey.assert_awaited_once_with("whatsapp-+60123")
    twilio.send_message.assert_awaited_once()
    handoff.publish_survey.assert_not_awaited()  # WhatsApp uses text, not SSE


def test_handback_web_publishes_survey_event() -> None:
    orch = MagicMock()
    orch._settings = get_settings()
    orch.begin_survey = AsyncMock()
    twilio = AsyncMock()
    handoff = MagicMock()
    handoff.publish_survey = AsyncMock()

    client = _client(orch, twilio, handoff)
    res = client.post("/webhooks/zendesk-handback", json={"session_id": "sim-abc"})

    assert res.status_code == 200
    orch.begin_survey.assert_awaited_once_with("sim-abc")
    handoff.publish_survey.assert_awaited_once_with("sim-abc")
    twilio.send_message.assert_not_awaited()
```

> Adjust the `json={...}` keys to the real `ZendeskHandbackPayload` fields, and remove the `orch.unpause` mock line if the existing handler uses a different method (mock whatever it actually calls so it does not error).

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_handback_csat.py -v`
Expected: FAIL — survey not started.

- [ ] **Step 5: Add the CSAT trigger to `zendesk_handback_webhook`**

After the existing unpause logic in `zendesk_handback_webhook` (keep it), before returning, add:

```python
        await self.orchestrator.begin_survey(session_id)
        if session_id.startswith("whatsapp-"):
            if self._twilio_adapter is not None:
                to = "whatsapp:" + session_id[len("whatsapp-") :]
                await self._twilio_adapter.send_message(conversation_id=to, text=_SURVEY_MESSAGE)
        elif self._handoff_bridge is not None:
            await self._handoff_bridge.publish_survey(session_id)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_handback_csat.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/handoff_bridge.py src/chatbot/features/chat/router.py src/chatbot/features/chat/test_handback_csat.py
git commit -m "feat(chat): handback starts CSAT survey (WhatsApp text + web SSE event)"
```

---

### Task 8: `POST /chat/csat` endpoint (web CSAT submit)

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py`
- Test: `apps/backend/src/chatbot/features/chat/test_chat_csat_endpoint.py`

**Interfaces:**
- Consumes: `OrchestratorService.record_csat`.
- Produces: `POST /chat/csat` with body `{ "session_id": str, "score": int }`; validates `1 <= score <= 5` (422 otherwise); calls `record_csat(session_id, score, channel="web")`; returns `{"status": "ok", "message": <thanks>}`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_chat_csat_endpoint.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_csat_endpoint_records_web_rating() -> None:
    orch = MagicMock()
    orch.record_csat = AsyncMock(return_value=True)
    client = _client(orch)

    res = client.post("/chat/csat", json={"session_id": "sim-abc", "score": 5})

    assert res.status_code == 200
    orch.record_csat.assert_awaited_once_with("sim-abc", 5, channel="web")


def test_csat_endpoint_rejects_out_of_range() -> None:
    orch = MagicMock()
    client = _client(orch)
    res = client.post("/chat/csat", json={"session_id": "sim-abc", "score": 9})
    assert res.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_chat_csat_endpoint.py -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Add the request model, route, and handler**

In `router.py`, add a model near `ChatTurnRequest`:

```python
class CsatRequest(BaseModel):
    session_id: str
    score: int = Field(ge=1, le=5)
```

Add `Field` to the pydantic import (`from pydantic import BaseModel, Field`). Register the route in `__init__` (after `/chat/turn`):

```python
        self.router.add_api_route("/chat/csat", self.chat_csat, methods=["POST"])
```

Add the handler:

```python
    async def chat_csat(self, payload: CsatRequest) -> dict[str, str]:
        await self.orchestrator.record_csat(payload.session_id, payload.score, channel="web")
        return {
            "status": "ok",
            "message": "Thank you for your feedback!",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_chat_csat_endpoint.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Backend full suite + lint + types**

```bash
.venv/bin/pytest src/
.venv/bin/ruff check . --fix
.venv/bin/mypy src/ --strict
```
Expected: all pass; ruff clean; mypy shows only the 3 pre-existing errors (`firestore_session_service.py:12`, `service.py` `_get_or_create_session` no-any-return, `test_service.py:9`).

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_chat_csat_endpoint.py
git commit -m "feat(chat): add POST /chat/csat endpoint for web CSAT"
```

---

### Task 9: Frontend — CSAT API + types

**Files:**
- Modify: `apps/frontend/src/features/chat/api/chat.api.ts`
- Modify: `apps/frontend/src/features/chat/types/index.ts`

**Interfaces:**
- Produces: `postCsat(sessionId: string, score: number): Promise<{ status: string; message: string }>`; type `CsatResponse`.

- [ ] **Step 1: Add the type**

In `apps/frontend/src/features/chat/types/index.ts`, add:

```typescript
export interface CsatResponse {
  status: string;
  message: string;
}
```

- [ ] **Step 2: Add the API call**

In `apps/frontend/src/features/chat/api/chat.api.ts`, add (mirroring `postChatTurn`):

```typescript
import type { ChatTurnResponse, CsatResponse } from '@/features/chat/types';

export async function postCsat(sessionId: string, score: number): Promise<CsatResponse> {
  const res = await fetch(`${API_BASE_URL}/chat/csat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, score }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`chat/csat ${res.status}: ${body}`);
  }
  return (await res.json()) as CsatResponse;
}
```

(Merge the `CsatResponse` import into the existing `import type { ChatTurnResponse } ...` line.)

- [ ] **Step 3: Type-check**

Run (from `apps/frontend/`): `npm run type-check`
Expected: passes.

- [ ] **Step 4: Commit**

```bash
git add src/features/chat/api/chat.api.ts src/features/chat/types/index.ts
git commit -m "feat(chat-ui): add postCsat api + CsatResponse type"
```

---

### Task 10: Frontend — `CsatSurvey.vue` rating widget

**Files:**
- Create: `apps/frontend/src/features/chat/components/CsatSurvey.vue`

**Interfaces:**
- Consumes: `postCsat` (Task 9).
- Produces: `<CsatSurvey :session-id="..." @done="..." />` — renders five buttons (1–5); on click posts the score, shows the returned thank-you, and emits `done`.

- [ ] **Step 1: Create the component**

Create `apps/frontend/src/features/chat/components/CsatSurvey.vue`:

```vue
<script setup lang="ts">
import { ref } from 'vue';
import { postCsat } from '@/features/chat/api/chat.api';

const props = defineProps<{ sessionId: string }>();
const emit = defineEmits<{ (e: 'done', score: number): void }>();

const submitting = ref(false);
const thanks = ref<string | null>(null);
const error = ref<string | null>(null);

async function rate(score: number): Promise<void> {
  if (submitting.value || thanks.value) return;
  submitting.value = true;
  error.value = null;
  try {
    const res = await postCsat(props.sessionId, score);
    thanks.value = res.message;
    emit('done', score);
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Failed to submit rating';
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="csat">
    <template v-if="thanks">
      <p class="csat__thanks">{{ thanks }}</p>
    </template>
    <template v-else>
      <p class="csat__prompt">How would you rate your experience?</p>
      <div class="csat__scale">
        <button
          v-for="n in 5"
          :key="n"
          type="button"
          class="csat__btn"
          :disabled="submitting"
          @click="rate(n)"
        >
          {{ n }}
        </button>
      </div>
      <p v-if="error" class="csat__error">{{ error }}</p>
    </template>
  </div>
</template>

<style scoped>
.csat { padding: 0.75rem; border-radius: 0.5rem; background: #f4f4f5; }
.csat__prompt { margin: 0 0 0.5rem; font-size: 0.9rem; }
.csat__scale { display: flex; gap: 0.5rem; }
.csat__btn {
  width: 2.25rem; height: 2.25rem; border-radius: 0.5rem;
  border: 1px solid #d4d4d8; background: #fff; cursor: pointer; font-weight: 600;
}
.csat__btn:disabled { opacity: 0.5; cursor: default; }
.csat__error { color: #b91c1c; font-size: 0.8rem; margin: 0.5rem 0 0; }
.csat__thanks { margin: 0; font-size: 0.9rem; }
</style>
```

- [ ] **Step 2: Type-check**

Run (from `apps/frontend/`): `npm run type-check`
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add src/features/chat/components/CsatSurvey.vue
git commit -m "feat(chat-ui): add CsatSurvey rating widget"
```

---

### Task 11: Frontend — show the survey on the `survey` SSE event

**Files:**
- Modify: `apps/frontend/src/features/chat/store/chat.store.ts`
- Modify: `apps/frontend/src/features/chat/components/ChatLog.vue`

**Interfaces:**
- Consumes: the `survey` SSE event from `openAgentStream` (Task 7 backend); `CsatSurvey.vue` (Task 10).
- Produces: store state `surveyRequested: boolean` set true on a `survey` event and false after submission; `ChatLog.vue` renders `<CsatSurvey>` when `surveyRequested` is true.

- [ ] **Step 1: Inspect the store's EventSource wiring**

Read `chat.store.ts` to find where `openAgentStream` is used and how `agent_message` events are handled (a `.addEventListener('agent_message', ...)` or `onmessage`). The survey listener mirrors that.

- [ ] **Step 2: Add survey state + listener in the store**

In `chat.store.ts`, add reactive state `const surveyRequested = ref(false);` (and expose it from the store's return). Where the EventSource is created, add a listener:

```typescript
  source.addEventListener('survey', () => {
    surveyRequested.value = true;
  });
```

Add an action to clear it:

```typescript
  function dismissSurvey(): void {
    surveyRequested.value = false;
  }
```

Export `surveyRequested` and `dismissSurvey` from the store.

- [ ] **Step 3: Render the survey in `ChatLog.vue`**

In `ChatLog.vue`, import the component and store fields, and render the survey at the bottom of the log when requested:

```vue
<script setup lang="ts">
import CsatSurvey from '@/features/chat/components/CsatSurvey.vue';
// ...existing store usage; ensure surveyRequested, dismissSurvey, and sessionId are in scope
</script>

<template>
  <!-- ...existing message list... -->
  <CsatSurvey
    v-if="surveyRequested"
    :session-id="sessionId"
    @done="dismissSurvey"
  />
</template>
```

(Adjust to the file's existing `<script setup>` store-binding style and the real session-id source.)

- [ ] **Step 4: Type-check**

Run (from `apps/frontend/`): `npm run type-check`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/features/chat/store/chat.store.ts src/features/chat/components/ChatLog.vue
git commit -m "feat(chat-ui): show CSAT survey when a survey SSE event arrives"
```

---

## Manual Verification (after Task 11)

With real Twilio + Zendesk credentials and the Sunshine/Support/handback webhooks pointed at the current tunnel:

1. **Single ticket:** routine WhatsApp message → one `solved` capture ticket; complaint → same ticket flips `open` (no second Sunshine ticket).
2. **Handoff:** AI hands off → customer receives "connecting you to a human agent"; subsequent WhatsApp messages appear as `Customer (WhatsApp):` private notes; agent's public reply arrives on the customer's WhatsApp.
3. **Handback → CSAT:** agent resolves → customer gets the 1–5 survey; reply `4` → ticket shows `⭐ Customer satisfaction: 4/5`, tag `csat_4`; bot resumes.
4. **Web CSAT:** in the Vue app, after an agent resolves a handed-off web session, the rating widget appears; submitting records the score and shows the thank-you.

## Notes for the implementer

- The real attribute names on `ZendeskSupportWebhookPayload` / `ZendeskHandbackPayload` (Tasks 6, 7) must be confirmed in `schemas.py`; the plan uses `comment_text` / `session_id` placeholders — match the actual fields.
- WhatsApp handoff intentionally bypasses Sunshine; the web/Sunshine path is unchanged except for the added survey event on handback.
- The capture ticket id + logged count remain the source of truth in session state (from the earlier dedup fix), so `begin_whatsapp_handoff` reuses the same ticket the routine turns already created.

## Self-Review (completed by plan author)

- **Spec coverage:** single ticket (Tasks 3, 5 reuse capture ticket) ✓; handoff pause + notify (Tasks 3, 5) ✓; paused routing both directions (Tasks 4, 5, 6) ✓; handback → CSAT (Task 7) ✓; shared `record_csat` (Task 2) ✓; WhatsApp survey capture (Task 5) ✓; web survey event + endpoint + widget (Tasks 7, 8, 9, 10, 11) ✓; tag/comment storage (Tasks 1, 2) ✓; state persistence (reuses `_persist_session_state`, exercised in Tasks 2–5). Native CSAT + free-text deferred (non-goals).
- **Placeholders:** payload field names in Tasks 6–7 are explicitly flagged to confirm against `schemas.py`; every other step has complete code.
- **Type consistency:** state constants (`WHATSAPP_ACTIVE/PAUSED/AWAITING_SURVEY`, `_HANDOFF_STATE_KEY`) defined in Task 2 and reused verbatim; `record_csat(session_id, score, channel)`, `begin_whatsapp_handoff`, `forward_whatsapp_to_agent`, `begin_survey`, `consume_survey_nudge`, `resume_ai`, `needs_whatsapp_handoff`, `whatsapp_state` signatures consistent across service.py, router.py, and tests; `add_ticket_tag` consistent across port/adapter/noop/_FakeLog; `postCsat`/`CsatResponse` consistent across frontend tasks.
