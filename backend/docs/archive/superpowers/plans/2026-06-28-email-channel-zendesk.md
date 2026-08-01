# Email Channel via Zendesk-Native Email — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add email as a customer channel feeding the existing `OrchestratorService`, using Zendesk's native email-to-ticket transport, reusing the WhatsApp handoff + unified-CSAT state machine.

**Architecture:** A customer email becomes a Zendesk ticket; a Zendesk trigger POSTs the new end-user comment to `POST /webhooks/zendesk-email`. The handler runs the AI on `session_id = "email-<ticket_id>"`, posts the reply as a *public* ticket comment (Zendesk emails it), and routes by conversation state (active / paused / awaiting_survey). The ticket *is* the conversation — no separate capture ticket, no message forwarding.

**Tech Stack:** Python 3.12, FastAPI, Google ADK/Gemini, httpx, pytest-asyncio, ruff, mypy --strict.

## Global Constraints

- Run all backend commands from `apps/backend/`. Quality gates per task: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`, `.venv/bin/pytest src/`.
- `pyproject.toml` sets `asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`.
- Adapter I/O is wrapped in try/except + `structlog` logging; webhook handlers never 500 on a downstream hiccup (return HTTP 200 with a status string).
- Webhook auth reuses the existing `zendesk_support_webhook_secret` via the `X-Proton-Webhook-Secret` header (no new secret).
- Session state persists in Firestore via `_persist_session_state`; `conversation_ticket_id` and the handoff state key survive restarts.
- Conventional commits: `<type>(<scope>): <desc>`. Never print secrets; provisioning scripts read from settings.
- Unified CSAT: email satisfaction MUST go through `record_csat(..., channel="email")` — do not use Zendesk's native survey.

---

### Task 1: `post_public_reply` on `ConversationLogPort` and adapters

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/ports.py` (add method to `ConversationLogPort`)
- Modify: `apps/backend/src/chatbot/features/chat/adapters/zendesk.py` (implement)
- Modify: `apps/backend/src/chatbot/features/chat/adapters/noop_conversation_log.py` (no-op)
- Test: `apps/backend/src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py`

**Interfaces:**
- Produces: `async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None` on `ConversationLogPort` — posts a **public** comment to an existing ticket, optionally setting status.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py`:

```python
async def test_post_public_reply_posts_public_comment_with_status(monkeypatch):
    from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
    from chatbot.platform.config import get_settings

    captured = {}

    class _Resp:
        def raise_for_status(self): ...

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def put(self, url, json, headers, timeout):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", lambda: _Client())

    adapter = ZendeskAdapter(get_settings())
    await adapter.post_public_reply("123", "Hello from AI", status="pending")

    assert "/tickets/123.json" in captured["url"]
    comment = captured["json"]["ticket"]["comment"]
    assert comment["body"] == "Hello from AI"
    assert comment["public"] is True
    assert captured["json"]["ticket"]["status"] == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py::test_post_public_reply_posts_public_comment_with_status -v`
Expected: FAIL with `AttributeError: 'ZendeskAdapter' object has no attribute 'post_public_reply'`.

- [ ] **Step 3: Add the port method**

In `ports.py`, inside `class ConversationLogPort(Protocol):` after `add_ticket_tag`:

```python
    async def post_public_reply(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        """Post a PUBLIC comment to an existing ticket (emailed to the requester
        by Zendesk), optionally setting ticket status."""
        ...
```

- [ ] **Step 4: Implement in `ZendeskAdapter`**

In `adapters/zendesk.py`, add after `add_ticket_tag`:

```python
    async def post_public_reply(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"
        ticket: dict[str, object] = {"comment": {"body": text, "public": True}}
        if status:
            ticket["status"] = status
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url, json={"ticket": ticket}, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
        except Exception as e:
            _log.error("zendesk_post_public_reply_failed", ticket_id=ticket_id, error=str(e))
```

- [ ] **Step 5: Implement the no-op**

In `adapters/noop_conversation_log.py`, add:

```python
    async def post_public_reply(
        self,
        ticket_id: str,  # noqa: ARG002
        text: str,  # noqa: ARG002
        status: str | None = None,  # noqa: ARG002
    ) -> None:
        return None
```

- [ ] **Step 6: Run test + quality gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean.

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/ports.py src/chatbot/features/chat/adapters/zendesk.py src/chatbot/features/chat/adapters/noop_conversation_log.py src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py
git commit -m "feat(chat): add post_public_reply to ConversationLogPort for email replies"
```

---

### Task 2: Channel-neutral state aliases in `OrchestratorService`

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py` (lines ~467-510 + line 353)
- Test: `apps/backend/src/chatbot/features/chat/test_email_state_aliases.py` (create)

**Interfaces:**
- Produces: `async def conversation_state(self, session_id: str) -> str`; `async def needs_handoff(self, session_id: str) -> bool`; `async def begin_handoff(self, session_id: str, summary: str, *, channel: str = "whatsapp", customer_name: str | None = None, customer_phone: str | None = None) -> None`.
- The existing `whatsapp_state`, `needs_whatsapp_handoff`, `begin_whatsapp_handoff` become thin wrappers that delegate (no behavior change).

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_email_state_aliases.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock

from chatbot.features.chat.service import OrchestratorService


def _orch() -> OrchestratorService:
    return OrchestratorService.__new__(OrchestratorService)


async def test_whatsapp_state_delegates_to_conversation_state() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="paused")
    assert await orch.whatsapp_state("email-1") == "paused"
    orch.conversation_state.assert_awaited_once_with("email-1")


async def test_needs_whatsapp_handoff_delegates_to_needs_handoff() -> None:
    orch = _orch()
    orch.needs_handoff = AsyncMock(return_value=True)
    assert await orch.needs_whatsapp_handoff("whatsapp-+60") is True
    orch.needs_handoff.assert_awaited_once_with("whatsapp-+60")


async def test_begin_whatsapp_handoff_delegates_with_whatsapp_channel() -> None:
    orch = _orch()
    orch.begin_handoff = AsyncMock()
    await orch.begin_whatsapp_handoff("whatsapp-+60", "summary", customer_phone="+60")
    orch.begin_handoff.assert_awaited_once_with(
        "whatsapp-+60", "summary", channel="WhatsApp", customer_name=None, customer_phone="+60"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_state_aliases.py -v`
Expected: FAIL (`conversation_state` / `needs_handoff` / `begin_handoff` not defined, or wrappers don't delegate).

- [ ] **Step 3: Rename the bodies to neutral methods + add wrappers**

In `service.py`, replace the three methods (currently `whatsapp_state` ~467, `needs_whatsapp_handoff` ~475, `begin_whatsapp_handoff` ~480) with neutral implementations plus delegating wrappers:

```python
    async def conversation_state(self, session_id: str) -> str:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return WHATSAPP_ACTIVE
        return str(session.state.get(_HANDOFF_STATE_KEY) or WHATSAPP_ACTIVE)

    async def whatsapp_state(self, session_id: str) -> str:
        return await self.conversation_state(session_id)

    async def needs_handoff(self, session_id: str) -> bool:
        if await self.conversation_state(session_id) != WHATSAPP_ACTIVE:
            return False
        return await self._handoff_triggered(session_id)

    async def needs_whatsapp_handoff(self, session_id: str) -> bool:
        return await self.needs_handoff(session_id)

    async def begin_handoff(
        self,
        session_id: str,
        summary: str,
        *,
        channel: str = "whatsapp",
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        state = session.state
        try:
            ticket_id = state.get("conversation_ticket_id")
            if not ticket_id:
                ticket_id = await self._conversation_log_port.ensure_conversation_ticket(
                    session_id=session_id,
                    subject=f"[{channel}] Conversation {session_id}",
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
                state["conversation_ticket_id"] = ticket_id
            await self._conversation_log_port.append_conversation_comment(
                ticket_id, f"[Handoff to human agent]\n{summary}", status="open"
            )
        except Exception as e:
            _log.error("begin_handoff_failed", session_id=session_id, error=str(e))
        state[_HANDOFF_STATE_KEY] = WHATSAPP_PAUSED
        await self._persist_session_state(session)

    async def begin_whatsapp_handoff(
        self,
        session_id: str,
        summary: str,
        *,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> None:
        await self.begin_handoff(
            session_id,
            summary,
            channel="WhatsApp",
            customer_name=customer_name,
            customer_phone=customer_phone,
        )
```

- [ ] **Step 4: Exclude `email-` sessions from the in-turn handoff escalation**

In `service.py` line 353, change:

```python
        if session_state.get("handoff_triggered") is True and not session_id.startswith("whatsapp-"):
```

to:

```python
        if session_state.get("handoff_triggered") is True and not (
            session_id.startswith("whatsapp-") or session_id.startswith("email-")
        ):
```

(Email handoff is driven by the webhook handler via `begin_handoff`, not the in-turn `_escalate_handoff` path.)

- [ ] **Step 5: Run tests (new + WhatsApp regression) + quality gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_state_aliases.py src/chatbot/features/chat/test_handback_csat.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean (WhatsApp flow unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/test_email_state_aliases.py
git commit -m "refactor(chat): channel-neutral handoff-state aliases; exclude email- from in-turn escalation"
```

---

### Task 3: `bind_email_ticket` + `email_draft_assist` config

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py` (add method)
- Modify: `apps/backend/src/chatbot/platform/config.py` (add setting)
- Test: `apps/backend/src/chatbot/features/chat/test_email_bind_ticket.py` (create)

**Interfaces:**
- Produces: `async def bind_email_ticket(self, session_id: str, ticket_id: str) -> None` — seeds `state["conversation_ticket_id"] = ticket_id` so the existing Zendesk email ticket is reused by `begin_handoff` / `record_csat` (never creates a duplicate).
- Produces: `Settings.email_draft_assist: bool` (default `False`).

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_email_bind_ticket.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from chatbot.features.chat.service import OrchestratorService


async def test_bind_email_ticket_sets_conversation_ticket_id() -> None:
    orch = OrchestratorService.__new__(OrchestratorService)
    session = SimpleNamespace(state={})
    orch._adk_sessions = SimpleNamespace(get_session=AsyncMock(return_value=session))
    orch._persist_session_state = AsyncMock()

    await orch.bind_email_ticket("email-77", "77")

    assert session.state["conversation_ticket_id"] == "77"
    orch._persist_session_state.assert_awaited_once_with(session)


async def test_bind_email_ticket_noop_when_already_bound() -> None:
    orch = OrchestratorService.__new__(OrchestratorService)
    session = SimpleNamespace(state={"conversation_ticket_id": "77"})
    orch._adk_sessions = SimpleNamespace(get_session=AsyncMock(return_value=session))
    orch._persist_session_state = AsyncMock()

    await orch.bind_email_ticket("email-77", "77")

    orch._persist_session_state.assert_not_awaited()


async def test_bind_email_ticket_noop_when_no_session() -> None:
    orch = OrchestratorService.__new__(OrchestratorService)
    orch._adk_sessions = SimpleNamespace(get_session=AsyncMock(return_value=None))
    orch._persist_session_state = AsyncMock()

    await orch.bind_email_ticket("email-77", "77")

    orch._persist_session_state.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_bind_ticket.py -v`
Expected: FAIL (`bind_email_ticket` not defined).

- [ ] **Step 3: Implement `bind_email_ticket`**

In `service.py`, add near `capture_conversation`:

```python
    async def bind_email_ticket(self, session_id: str, ticket_id: str) -> None:
        """Seed the existing Zendesk email ticket id into session state so the
        handoff/CSAT paths reuse it instead of creating a duplicate ticket."""
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        if session.state.get("conversation_ticket_id") != ticket_id:
            session.state["conversation_ticket_id"] = ticket_id
            await self._persist_session_state(session)
```

- [ ] **Step 4: Add the config setting**

In `config.py`, after `zendesk_support_webhook_secret` (line ~49):

```python
    email_draft_assist: bool = False
```

- [ ] **Step 5: Run test + quality gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_bind_ticket.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/service.py src/chatbot/platform/config.py src/chatbot/features/chat/test_email_bind_ticket.py
git commit -m "feat(chat): bind_email_ticket + email_draft_assist config"
```

---

### Task 4: Email webhook schema + router constants + no-reply guard

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/schemas.py` (add payload)
- Modify: `apps/backend/src/chatbot/features/chat/router.py` (constants + `_is_no_reply`)
- Test: `apps/backend/src/chatbot/features/chat/test_email_helpers.py` (create)

**Interfaces:**
- Produces: `ZendeskEmailWebhookPayload(ticket_id: str, text: str, requester_name: str | None = None, requester_email: str | None = None)`.
- Produces module-level in `router.py`: `_EMAIL_HANDOFF_MESSAGE`, `_EMAIL_SURVEY_MESSAGE`, `_EMAIL_CSAT_NUDGE`, `_EMAIL_CSAT_THANKS: str`; `def _is_no_reply(email: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_email_helpers.py`:

```python
from __future__ import annotations

import pytest

from chatbot.features.chat.router import _is_no_reply


@pytest.mark.parametrize(
    "email,expected",
    [
        ("no-reply@acme.com", True),
        ("noreply@acme.com", True),
        ("mailer-daemon@acme.com", True),
        ("postmaster@acme.com", True),
        ("donotreply@acme.com", True),
        ("alice@acme.com", False),
        (None, False),
        ("", False),
    ],
)
def test_is_no_reply(email, expected) -> None:
    assert _is_no_reply(email) is expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_helpers.py -v`
Expected: FAIL (`_is_no_reply` not defined / ImportError).

- [ ] **Step 3: Add the schema**

In `schemas.py`, after `ZendeskHandbackPayload`:

```python
class ZendeskEmailWebhookPayload(BaseModel):
    ticket_id: str
    text: str
    requester_name: str | None = None
    requester_email: str | None = None
```

- [ ] **Step 4: Add router constants + `_is_no_reply`**

In `router.py`, after the existing WhatsApp message constants (`_CSAT_THANKS` block):

```python
_EMAIL_HANDOFF_MESSAGE = (
    "Thanks for reaching out. I'm connecting you with a specialist from our team "
    "who will reply to this email shortly."
)
_EMAIL_SURVEY_MESSAGE = (
    "Thanks for contacting Proton support! How would you rate your experience? "
    "Reply with a number from 1 to 5 (1 = poor, 5 = excellent)."
)
_EMAIL_CSAT_NUDGE = "Please reply with a single number from 1 to 5 to rate your experience."
_EMAIL_CSAT_THANKS = (
    "Thank you for your feedback! Reply any time if you need further help."
)

_NO_REPLY_RE = re.compile(r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster)", re.IGNORECASE)


def _is_no_reply(email: str | None) -> bool:
    """True for automated/system senders we must never auto-reply to (loop guard)."""
    return bool(email and _NO_REPLY_RE.search(email))
```

- [ ] **Step 5: Add the schema import**

In `router.py`, add `ZendeskEmailWebhookPayload` to the existing `from chatbot.features.chat.schemas import (...)` block.

- [ ] **Step 6: Run test + quality gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_helpers.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean.

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/schemas.py src/chatbot/features/chat/router.py src/chatbot/features/chat/test_email_helpers.py
git commit -m "feat(chat): email webhook schema, message constants, no-reply loop guard"
```

---

### Task 5: `zendesk_email_webhook` handler — active path (AI reply + handoff)

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py` (route + handler)
- Test: `apps/backend/src/chatbot/features/chat/test_email_webhook.py` (create)

**Interfaces:**
- Consumes: `OrchestratorService.conversation_state`, `needs_handoff`, `begin_handoff`, `bind_email_ticket`, `handle_turn`, `_conversation_log_port.post_public_reply`, `_conversation_log_port.append_conversation_comment`, `_settings.email_draft_assist`, `_settings.zendesk_support_webhook_secret`; `_is_no_reply`, `_EMAIL_HANDOFF_MESSAGE`.
- Produces: route `POST /webhooks/zendesk-email` → `zendesk_email_webhook`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/test_email_webhook.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _orch() -> MagicMock:
    orch = MagicMock()
    orch._settings = get_settings()
    orch._settings.zendesk_support_webhook_secret = ""
    orch._settings.email_draft_assist = False
    orch._conversation_log_port = MagicMock()
    orch._conversation_log_port.post_public_reply = AsyncMock()
    orch._conversation_log_port.append_conversation_comment = AsyncMock()
    orch.bind_email_ticket = AsyncMock()
    orch.conversation_state = AsyncMock(return_value="active")
    orch.needs_handoff = AsyncMock(return_value=False)
    orch.begin_handoff = AsyncMock()
    orch.handle_turn = AsyncMock(return_value=SimpleNamespace(reply="Here is your answer."))
    return orch


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def _body(**kw):
    base = {"ticket_id": "55", "text": "How do I reset my password?",
            "requester_name": "Alice", "requester_email": "alice@acme.com"}
    base.update(kw)
    return base


def test_active_posts_public_reply_pending() -> None:
    orch = _orch()
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    orch.handle_turn.assert_awaited_once_with(session_id="email-55", text="How do I reset my password?")
    orch._conversation_log_port.post_public_reply.assert_awaited_once_with(
        "55", "Here is your answer.", status="pending"
    )


def test_handoff_pauses_and_posts_ack() -> None:
    orch = _orch()
    orch.needs_handoff = AsyncMock(return_value=True)
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    orch.begin_handoff.assert_awaited_once()
    # the ack email is the LAST public reply; no status forced to solved
    args, kwargs = orch._conversation_log_port.post_public_reply.await_args
    assert args[0] == "55"


def test_draft_assist_posts_private_note() -> None:
    orch = _orch()
    orch._settings.email_draft_assist = True
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    orch._conversation_log_port.post_public_reply.assert_not_awaited()
    orch._conversation_log_port.append_conversation_comment.assert_awaited_once()


def test_paused_is_noop() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="paused")
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    orch.handle_turn.assert_not_awaited()
    orch._conversation_log_port.post_public_reply.assert_not_awaited()


def test_no_reply_sender_ignored() -> None:
    orch = _orch()
    res = _client(orch).post("/webhooks/zendesk-email", json=_body(requester_email="no-reply@acme.com"))
    assert res.status_code == 200
    assert res.json()["status"] == "ignored"
    orch.handle_turn.assert_not_awaited()


def test_blank_text_ignored() -> None:
    orch = _orch()
    res = _client(orch).post("/webhooks/zendesk-email", json=_body(text="   "))
    assert res.json()["status"] == "ignored"
    orch.handle_turn.assert_not_awaited()


def test_wrong_secret_rejected() -> None:
    orch = _orch()
    orch._settings.zendesk_support_webhook_secret = "expected"
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_webhook.py -v`
Expected: FAIL (404 — route not registered).

- [ ] **Step 3: Register the route**

In `router.py` `ChatRouter.__init__`, alongside the other webhook routes:

```python
        self.router.add_api_route(
            "/webhooks/zendesk-email", self.zendesk_email_webhook, methods=["POST"]
        )
```

- [ ] **Step 4: Implement the handler**

In `router.py`, add the method (uses the awaiting_survey branch added in Task 6 — for now it falls through to active; the survey call is added in Task 6):

```python
    async def zendesk_email_webhook(
        self,
        payload: ZendeskEmailWebhookPayload,
        x_proton_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, str]:
        """Inbound customer email, delivered by a Zendesk trigger.

        The ticket IS the conversation (session_id = email-<ticket_id>). We run
        the AI, post the reply as a public comment (Zendesk emails it), and route
        by conversation state. Paused sessions are handled by a human in Zendesk,
        so we no-op.
        """
        expected_secret = self.orchestrator._settings.zendesk_support_webhook_secret
        if expected_secret and (
            not x_proton_webhook_secret or x_proton_webhook_secret != expected_secret
        ):
            _log.warning("zendesk_email_webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        ticket_id = payload.ticket_id.strip()
        text = (payload.text or "").strip()
        if not ticket_id or not text:
            return {"status": "ignored"}
        if _is_no_reply(payload.requester_email):
            _log.info("zendesk_email_webhook_no_reply_skipped", ticket_id=ticket_id)
            return {"status": "ignored"}

        session_id = f"email-{ticket_id}"
        _log.info("zendesk_email_received", session_id=session_id)

        state = await self.orchestrator.conversation_state(session_id)
        if state == "awaiting_survey":
            await self._handle_email_survey(ticket_id, session_id, text)
            return {"status": "ok"}
        if state == "paused":
            return {"status": "paused"}

        result = await self.orchestrator.handle_turn(session_id=session_id, text=text)
        await self.orchestrator.bind_email_ticket(session_id, ticket_id)

        port = self.orchestrator._conversation_log_port
        if await self.orchestrator.needs_handoff(session_id):
            summary = result.reply or "Customer requested a human agent."
            await self.orchestrator.begin_handoff(
                session_id, summary, channel="email", customer_name=payload.requester_name
            )
            await port.post_public_reply(ticket_id, _EMAIL_HANDOFF_MESSAGE)
        else:
            reply = result.reply or ""
            if reply:
                if self.orchestrator._settings.email_draft_assist:
                    await port.append_conversation_comment(ticket_id, f"[AI draft]\n{reply}")
                else:
                    await port.post_public_reply(ticket_id, reply, status="pending")
        return {"status": "ok"}
```

- [ ] **Step 5: Add a temporary stub for `_handle_email_survey`**

So Task 5 compiles before Task 6 fills it in, add a minimal stub (replaced in Task 6):

```python
    async def _handle_email_survey(self, ticket_id: str, session_id: str, body: str) -> None:
        return None
```

- [ ] **Step 6: Run tests + quality gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_webhook.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean.

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_email_webhook.py
git commit -m "feat(chat): zendesk-email webhook — AI auto-reply, handoff, draft-assist, loop guards"
```

---

### Task 6: Email CSAT — survey reply handler + handback email branch

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py` (`_handle_email_survey` + handback `email-` branch)
- Test: `apps/backend/src/chatbot/features/chat/test_email_csat.py` (create)

**Interfaces:**
- Consumes: `OrchestratorService.parse_csat` (staticmethod), `record_csat(session_id, score, channel="email")`, `consume_survey_nudge`, `resume_ai`, `handle_turn`, `begin_survey`, `conversation_state`; `_conversation_log_port.post_public_reply`; `_EMAIL_CSAT_THANKS`, `_EMAIL_CSAT_NUDGE`, `_EMAIL_SURVEY_MESSAGE`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/test_email_csat.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _orch() -> MagicMock:
    orch = MagicMock()
    orch._settings = get_settings()
    orch._settings.zendesk_support_webhook_secret = ""
    orch._conversation_log_port = MagicMock()
    orch._conversation_log_port.post_public_reply = AsyncMock()
    orch.parse_csat = OrchestratorService.parse_csat  # real staticmethod
    orch.record_csat = AsyncMock(return_value=True)
    orch.consume_survey_nudge = AsyncMock(return_value=True)
    orch.resume_ai = AsyncMock()
    orch.begin_survey = AsyncMock()
    orch.bind_email_ticket = AsyncMock()
    orch.handle_turn = AsyncMock(return_value=SimpleNamespace(reply="ok"))
    orch._ticketing_port = MagicMock()
    orch._ticketing_port.unpause_ai_for_session = AsyncMock()
    return orch


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def _email(text: str):
    return {"ticket_id": "55", "text": text, "requester_email": "alice@acme.com"}


def test_valid_csat_records_email_channel() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="awaiting_survey")
    res = _client(orch).post("/webhooks/zendesk-email", json=_email("5"))
    assert res.status_code == 200
    orch.record_csat.assert_awaited_once_with("email-55", 5, channel="email")
    orch._conversation_log_port.post_public_reply.assert_awaited_once()  # thank-you


def test_invalid_csat_nudges_once() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="awaiting_survey")
    res = _client(orch).post("/webhooks/zendesk-email", json=_email("thanks!"))
    assert res.status_code == 200
    orch.record_csat.assert_not_awaited()
    orch.consume_survey_nudge.assert_awaited_once_with("email-55")
    orch._conversation_log_port.post_public_reply.assert_awaited_once()  # nudge


def test_second_invalid_resumes_ai() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="awaiting_survey")
    orch.consume_survey_nudge = AsyncMock(return_value=False)
    res = _client(orch).post("/webhooks/zendesk-email", json=_email("actually a new question"))
    assert res.status_code == 200
    orch.resume_ai.assert_awaited_once_with("email-55")
    orch.handle_turn.assert_awaited_once()


def test_handback_email_starts_survey_when_paused() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="paused")
    res = _client(orch).post("/webhooks/zendesk-handback", json={"session_id": "email-55"})
    assert res.status_code == 200
    orch.begin_survey.assert_awaited_once_with("email-55")
    orch._conversation_log_port.post_public_reply.assert_awaited_once()  # survey email


def test_handback_email_skips_survey_when_not_paused() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="awaiting_survey")
    res = _client(orch).post("/webhooks/zendesk-handback", json={"session_id": "email-55"})
    assert res.status_code == 200
    orch.begin_survey.assert_not_awaited()
    orch._conversation_log_port.post_public_reply.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_csat.py -v`
Expected: FAIL (stub `_handle_email_survey` no-ops; handback has no `email-` branch).

- [ ] **Step 3: Replace the `_handle_email_survey` stub**

In `router.py`, replace the stub from Task 5 with:

```python
    async def _handle_email_survey(self, ticket_id: str, session_id: str, body: str) -> None:
        port = self.orchestrator._conversation_log_port
        score = OrchestratorService.parse_csat(body)
        if score is not None:
            await self.orchestrator.record_csat(session_id, score, channel="email")
            await port.post_public_reply(ticket_id, _EMAIL_CSAT_THANKS)
            return
        if await self.orchestrator.consume_survey_nudge(session_id):
            await port.post_public_reply(ticket_id, _EMAIL_CSAT_NUDGE)
            return
        # Second invalid → resume AI and process this message as a normal turn.
        await self.orchestrator.resume_ai(session_id)
        result = await self.orchestrator.handle_turn(session_id=session_id, text=body)
        await self.orchestrator.bind_email_ticket(session_id, ticket_id)
        reply = getattr(result, "reply", "") or ""
        if reply:
            await port.post_public_reply(ticket_id, reply, status="pending")
```

Add the import at the top of `router.py` if not present: `from chatbot.features.chat.service import OrchestratorService` (already imported — confirm).

- [ ] **Step 4: Add the `email-` branch to `zendesk_handback_webhook`**

In `router.py` `zendesk_handback_webhook`, change the dispatch block. Replace:

```python
        if session_id.startswith("whatsapp-"):
            ...
            if await self.orchestrator.whatsapp_state(session_id) == "paused":
                await self.orchestrator.begin_survey(session_id)
                if self._twilio_adapter is not None:
                    to = "whatsapp:" + session_id[len("whatsapp-"):]
                    await self._twilio_adapter.send_message(
                        conversation_id=to, text=_SURVEY_MESSAGE
                    )
        else:
```

with:

```python
        if session_id.startswith("whatsapp-"):
            if await self.orchestrator.conversation_state(session_id) == "paused":
                await self.orchestrator.begin_survey(session_id)
                if self._twilio_adapter is not None:
                    to = "whatsapp:" + session_id[len("whatsapp-"):]
                    await self._twilio_adapter.send_message(
                        conversation_id=to, text=_SURVEY_MESSAGE
                    )
        elif session_id.startswith("email-"):
            # Only on a genuine handoff (paused) -> solved transition; re-fires
            # from our own survey/CSAT writes are end-user-agnostic and ignored.
            if await self.orchestrator.conversation_state(session_id) == "paused":
                ticket_id = session_id[len("email-"):]
                await self.orchestrator.begin_survey(session_id)
                await self.orchestrator._conversation_log_port.post_public_reply(
                    ticket_id, _EMAIL_SURVEY_MESSAGE
                )
        else:
```

- [ ] **Step 5: Run tests + quality gates + full suite**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_email_csat.py src/chatbot/features/chat/test_handback_csat.py -v && .venv/bin/pytest src/ && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean (WhatsApp + web handback unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_email_csat.py
git commit -m "feat(chat): email CSAT survey reply handling + handback survey email"
```

---

### Task 7: Zendesk trigger + webhook provisioning script

**Files:**
- Create: `apps/backend/scripts/provision_zendesk_email_webhook.py`
- Test: none (operational script; dry-run guarded)

**Interfaces:**
- Consumes: `Settings` (subdomain, email, api_token, `zendesk_support_webhook_secret`). Reads secrets from settings — never prints them.

- [ ] **Step 1: Write the provisioning script**

Create `apps/backend/scripts/provision_zendesk_email_webhook.py`:

```python
"""Create the Zendesk webhook target + trigger that delivers inbound customer
emails to POST /webhooks/zendesk-email.

Usage (from apps/backend/):
    PUBLIC_BASE_URL=https://<tunnel>  .venv/bin/python scripts/provision_zendesk_email_webhook.py

Reads Zendesk creds + ZENDESK_SUPPORT_WEBHOOK_SECRET from settings/.env.
Never prints the secret.
"""

from __future__ import annotations

import base64
import os
import sys

import httpx

from chatbot.platform.config import get_settings


def _auth_header(settings) -> dict[str, str]:
    raw = f"{settings.zendesk_email}/token:{settings.zendesk_api_token}"
    enc = base64.b64encode(raw.encode()).decode()
    return {"Authorization": f"Basic {enc}", "Content-Type": "application/json"}


def main() -> int:
    settings = get_settings()
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        print("ERROR: set PUBLIC_BASE_URL to your public backend URL")
        return 1
    secret = settings.zendesk_support_webhook_secret
    if not secret:
        print("ERROR: ZENDESK_SUPPORT_WEBHOOK_SECRET is empty")
        return 1

    sub = settings.zendesk_subdomain
    api = f"https://{sub}.zendesk.com/api/v2"
    headers = _auth_header(settings)

    webhook_body = {
        "webhook": {
            "name": "Proton AI — inbound email",
            "endpoint": f"{base}/webhooks/zendesk-email",
            "http_method": "POST",
            "request_format": "json",
            "status": "active",
            "subscriptions": ["conditional_ticket_event"],
            "authentication": {
                "type": "api_key",
                "data": {"name": "X-Proton-Webhook-Secret", "value": secret},
                "add_position": "header",
            },
        }
    }
    with httpx.Client(timeout=15.0) as client:
        res = client.post(f"{api}/webhooks", json=webhook_body, headers=headers)
        res.raise_for_status()
        webhook_id = res.json()["webhook"]["id"]
        print(f"webhook created: id={webhook_id}")

        trigger_body = {
            "trigger": {
                "title": "AI: customer email -> webhook",
                "conditions": {
                    "all": [
                        {"field": "update_type", "operator": "is", "value": "Create"},
                        {"field": "comment_is_public", "operator": "is", "value": "true"},
                        {"field": "current_via_id", "operator": "is", "value": "4"},
                    ]
                },
                "actions": [
                    {
                        "field": "notification_webhook",
                        "value": [
                            webhook_id,
                            (
                                '{"ticket_id":"{{ticket.id}}",'
                                '"text":"{{ticket.latest_public_comment}}",'
                                '"requester_name":"{{ticket.requester.name}}",'
                                '"requester_email":"{{ticket.requester.email}}"}'
                            ),
                        ],
                    }
                ],
            }
        }
        res = client.post(f"{api}/triggers", json=trigger_body, headers=headers)
        res.raise_for_status()
        print(f"trigger created: id={res.json()['trigger']['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> Note: `current_via_id = 4` is Zendesk's "Mail" channel; combined with `comment_is_public = true` this fires only on inbound *email* end-user comments, not agent/AI replies. If your account also wants the web form, add a second trigger or broaden the via condition. Verify the via id in your Zendesk before relying on it; adjust if the API rejects it.

- [ ] **Step 2: Sanity-check it imports**

Run: `.venv/bin/python -c "import ast; ast.parse(open('scripts/provision_zendesk_email_webhook.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit (do NOT run against live Zendesk without user authorization)**

```bash
git add scripts/provision_zendesk_email_webhook.py
git commit -m "chore(chat): provisioning script for Zendesk inbound-email webhook + trigger"
```

---

### Task 8: Docs — CHANGELOG, README, .env.example

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `apps/backend/.env.example`

- [ ] **Step 1: CHANGELOG entry**

Add under `## [Unreleased]` (or a new dated section `## [2026-06-28]`), in *Added*:

```markdown
- **Email channel via Zendesk-native email** — inbound customer emails become Zendesk
  tickets; a trigger delivers each end-user comment to `/webhooks/zendesk-email`. The AI
  auto-replies as a public comment (Zendesk emails it), gated by the detection gate, with
  email loop guards. Handoff pauses the AI and a human takes the ticket; on Solve the
  unified CSAT survey runs over email (`record_csat(channel="email")`). Draft-assist mode
  (`EMAIL_DRAFT_ASSIST=true`) posts AI replies as private notes for an agent to send.
```

- [ ] **Step 2: README updates**

- Intro/Highlights: add **Email (via Zendesk)** to the channel list alongside web text, voice, WhatsApp.
- Endpoints table: add `POST /webhooks/zendesk-email` — inbound customer email (Zendesk trigger).
- Configuration table: add `EMAIL_DRAFT_ASSIST` (default `false`); note `ZENDESK_SUPPORT_WEBHOOK_SECRET` is reused for the email webhook.

- [ ] **Step 3: .env.example**

Add:

```bash
# Email channel (Zendesk-native). Reuses ZENDESK_SUPPORT_WEBHOOK_SECRET for webhook auth.
# Set to true to have the AI post replies as PRIVATE notes for an agent to send (draft-assist),
# instead of auto-emailing the customer.
EMAIL_DRAFT_ASSIST=false
```

- [ ] **Step 4: Run the full suite one last time**

Run: `.venv/bin/pytest src/ && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md apps/backend/.env.example
git commit -m "docs: email channel — changelog, readme, env example"
```

---

## Self-Review Notes

- **Spec coverage:** Data flow → Task 5; public reply → Task 1; `email-<ticket_id>` + ticket binding → Task 3/5; handoff (pause + ack, paused no-op) → Task 5; CSAT unified path → Task 6; loop guards (trigger via-id + public + pause + no-reply) → Tasks 4/5/7; channel-neutral aliases → Task 2; ticket status `pending` → Task 5; trigger/webhook → Task 7; config + docs → Tasks 3/8. All covered.
- **Out of scope (unchanged):** metrics/dashboard, NPS, web/WhatsApp retrofit, custom email domain.
- **Type consistency:** `post_public_reply(ticket_id, text, status=None)`, `conversation_state`, `needs_handoff`, `begin_handoff(channel=...)`, `bind_email_ticket(session_id, ticket_id)`, `record_csat(..., channel="email")` used identically across tasks.
- **Risk:** Zendesk trigger `current_via_id` value (Mail channel) is account-verifiable; Task 7 note flags it. The webhook/trigger must be created against live Zendesk only with user authorization.
