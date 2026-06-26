# WhatsApp Channel + Shared Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add WhatsApp (via Twilio) as an inbound channel that feeds the existing `OrchestratorService`, replies via the Twilio REST API, and mirrors every conversation into Zendesk through a detection gate (actionable → open ticket; routine → solved log).

**Architecture:** Twilio posts inbound WhatsApp messages to a new signature-verified webhook. The handler maps the sender to `session_id = whatsapp-{E164}`, runs the existing `handle_turn`, sends the reply out through a new `TwilioChannelAdapter` (a `ChatPort`), then calls a new `OrchestratorService.capture_conversation` which uses a pure detection gate to create/append a Zendesk ticket via a new `ConversationLogPort`. All changes are additive — the agent brain, existing webhooks, and the `_escalate_handoff` path are untouched.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, httpx (async), Google ADK/Gemini, pytest + pytest-asyncio, ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-06-24-whatsapp-twilio-zendesk-integration-design.md` (Phase A + the cross-cutting foundation). Phases B (Email) and C (Phone/ConversationRelay) are separate plans built on this foundation.

## Global Constraints

- **Python** `>=3.12`; all new code must pass `.venv/bin/mypy src/ --strict` and `.venv/bin/ruff check .`.
- **Line length** 100 (`ruff` `E501` is ignored but keep code tidy).
- **Run all commands from** `apps/backend/`. Tests: `.venv/bin/pytest src/`.
- **Tests are co-located** as `test_*.py` next to the code (per repo convention).
- **Async tests** use `@pytest.mark.asyncio`; `asyncio_mode = "auto"` is set.
- **Additive only:** do NOT modify `_escalate_handoff`, `create_ticket`, `add_private_note`, `pause/unpause`, or the Sunshine bridge. Add new methods/classes alongside them.
- **Session id convention:** `whatsapp-{E164}` (E.164 with leading `+`, no `whatsapp:` prefix).
- **Commits:** conventional format `<type>(<scope>): <desc>`; commit at the end of each task.
- **Branch:** do this work on a feature branch (e.g. `feat/whatsapp-twilio-zendesk`), not `main`.

---

### Task 1: Twilio + channel configuration

**Files:**
- Modify: `apps/backend/src/chatbot/platform/config.py`
- Modify: `apps/backend/.env.example`
- Test: `apps/backend/src/chatbot/platform/test_config_twilio.py`

**Interfaces:**
- Produces: `Settings.twilio_account_sid: str`, `Settings.twilio_auth_token: str`, `Settings.twilio_whatsapp_number: str`, `Settings.twilio_phone_number: str`, `Settings.twilio_webhook_base_url: str` (all default `""`).

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/platform/test_config_twilio.py`:

```python
from __future__ import annotations

from chatbot.platform.config import Settings


def test_twilio_settings_default_to_empty() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.twilio_account_sid == ""
    assert settings.twilio_auth_token == ""
    assert settings.twilio_whatsapp_number == ""
    assert settings.twilio_phone_number == ""
    assert settings.twilio_webhook_base_url == ""


def test_twilio_settings_read_from_init() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_whatsapp_number="whatsapp:+60123456789",
    )
    assert settings.twilio_account_sid == "AC123"
    assert settings.twilio_auth_token == "tok"
    assert settings.twilio_whatsapp_number == "whatsapp:+60123456789"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_twilio.py -v`
Expected: FAIL — `AttributeError`/validation error (fields don't exist).

- [ ] **Step 3: Add the settings fields**

In `config.py`, after the Zammad settings block (around line 68), add:

```python
    # Twilio (WhatsApp Phase A; Phone Phase C). Empty by default so dev
    # environments without Twilio credentials still boot.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""  # e.g. "whatsapp:+60123456789"
    twilio_phone_number: str = ""  # e.g. "+60123456789"
    twilio_webhook_base_url: str = ""  # public https base for webhooks (ngrok in dev)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_twilio.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Document the env vars**

Append to `apps/backend/.env.example`:

```bash
# --- Twilio (WhatsApp + Phone) ---
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=      # whatsapp:+E164 (shared number)
TWILIO_PHONE_NUMBER=         # +E164 (same underlying number, Phase C)
TWILIO_WEBHOOK_BASE_URL=     # public https base, e.g. https://xxxx.ngrok.app
```

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/platform/config.py src/chatbot/platform/test_config_twilio.py .env.example
git commit -m "feat(config): add Twilio settings for WhatsApp + phone channels"
```

---

### Task 2: Twilio request-signature verification

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/twilio_signature.py`
- Test: `apps/backend/src/chatbot/features/chat/test_twilio_signature.py`

**Interfaces:**
- Produces: `verify_twilio_signature(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool`

Twilio signs `X-Twilio-Signature` as `base64(HMAC-SHA1(auth_token, url + concat(sorted(k+v))))` over the POST form fields.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_twilio_signature.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac

from chatbot.features.chat.twilio_signature import verify_twilio_signature

_TOKEN = "test_auth_token"
_URL = "https://example.com/webhooks/twilio-whatsapp"
_PARAMS = {"From": "whatsapp:+60123456789", "Body": "Hello", "MessageSid": "SM1"}


def _expected_signature(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = hmac.new(token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("utf-8")


def test_accepts_valid_signature() -> None:
    sig = _expected_signature(_TOKEN, _URL, _PARAMS)
    assert verify_twilio_signature(_TOKEN, _URL, _PARAMS, sig) is True


def test_rejects_tampered_body() -> None:
    sig = _expected_signature(_TOKEN, _URL, _PARAMS)
    tampered = {**_PARAMS, "Body": "Goodbye"}
    assert verify_twilio_signature(_TOKEN, _URL, tampered, sig) is False


def test_rejects_missing_signature() -> None:
    assert verify_twilio_signature(_TOKEN, _URL, _PARAMS, None) is False


def test_rejects_wrong_token() -> None:
    sig = _expected_signature(_TOKEN, _URL, _PARAMS)
    assert verify_twilio_signature("other_token", _URL, _PARAMS, sig) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_twilio_signature.py -v`
Expected: FAIL — `ModuleNotFoundError: twilio_signature`.

- [ ] **Step 3: Implement the helper**

Create `apps/backend/src/chatbot/features/chat/twilio_signature.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac


def verify_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str | None,
) -> bool:
    """Validate Twilio's X-Twilio-Signature for a form-encoded webhook POST.

    Twilio computes base64(HMAC-SHA1(auth_token, url + concat(sorted(k+v)))).
    """
    if not signature:
        return False
    data = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    mac = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_twilio_signature.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/twilio_signature.py src/chatbot/features/chat/test_twilio_signature.py
git commit -m "feat(chat): add Twilio webhook signature verification helper"
```

---

### Task 3: TwilioChannelAdapter (outbound WhatsApp via REST)

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/adapters/twilio_channel.py`
- Test: `apps/backend/src/chatbot/features/chat/adapters/test_twilio_channel.py`

**Interfaces:**
- Consumes: `Settings.twilio_account_sid/twilio_auth_token/twilio_whatsapp_number`.
- Produces: `TwilioChannelAdapter(settings).send_message(conversation_id: str, text: str) -> None` (implements `ChatPort`), where `conversation_id` is the `whatsapp:+E164` destination. Internal helper `_post_message(client, to, text) -> None` for testing.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/adapters/test_twilio_channel.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter


def _adapter() -> TwilioChannelAdapter:
    settings = SimpleNamespace(
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_whatsapp_number="whatsapp:+60111111111",
    )
    return TwilioChannelAdapter(settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_post_message_calls_twilio_messages_endpoint() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=201, raise_for_status=lambda: None))

    await adapter._post_message(client, "whatsapp:+60123456789", "Hi there")

    url = client.post.call_args.args[0]
    assert url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    sent = client.post.call_args.kwargs["data"]
    assert sent["From"] == "whatsapp:+60111111111"
    assert sent["To"] == "whatsapp:+60123456789"
    assert sent["Body"] == "Hi there"
    # Basic auth = (account_sid, auth_token)
    assert client.post.call_args.kwargs["auth"] == ("AC123", "tok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_twilio_channel.py -v`
Expected: FAIL — `ModuleNotFoundError: twilio_channel`.

- [ ] **Step 3: Implement the adapter**

Create `apps/backend/src/chatbot/features/chat/adapters/twilio_channel.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from chatbot.features.chat.ports import ChatPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class TwilioChannelAdapter(ChatPort):
    """Sends outbound WhatsApp messages through the Twilio REST API.

    `conversation_id` is the customer's WhatsApp address ("whatsapp:+E164").
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _messages_url(self) -> str:
        return (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self._settings.twilio_account_sid}/Messages.json"
        )

    async def _post_message(self, client: Any, to: str, text: str) -> None:
        res = await client.post(
            self._messages_url(),
            data={
                "From": self._settings.twilio_whatsapp_number,
                "To": to,
                "Body": text,
            },
            auth=(self._settings.twilio_account_sid, self._settings.twilio_auth_token),
            timeout=10.0,
        )
        res.raise_for_status()

    async def send_message(self, conversation_id: str, text: str) -> None:
        _log.info("sending_twilio_whatsapp_reply", to=conversation_id)
        try:
            async with httpx.AsyncClient() as client:
                await self._post_message(client, conversation_id, text)
        except Exception as e:
            _log.error("twilio_whatsapp_reply_failed", to=conversation_id, error=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_twilio_channel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/adapters/twilio_channel.py src/chatbot/features/chat/adapters/test_twilio_channel.py
git commit -m "feat(chat): add TwilioChannelAdapter for outbound WhatsApp"
```

---

### Task 4: `flag_for_ticket_tool` agent tool

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/agents.py`
- Test: `apps/backend/src/chatbot/features/chat/test_flag_for_ticket_tool.py`

**Interfaces:**
- Produces: an agent tool `flag_for_ticket_tool(tool_context, reason)` that sets `state["ticket_flagged"] = True` and `state["ticket_reason"] = reason`. Registered on the support agent.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_flag_for_ticket_tool.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
)
from chatbot.features.chat.agents import build_ai_agent
from chatbot.platform.config import get_settings


def _find_tool(agent: object, name: str) -> object:
    for tool in agent.tools:  # type: ignore[attr-defined]
        func = getattr(tool, "func", tool)
        if getattr(func, "__name__", "") == name:
            return func
    raise AssertionError(f"tool {name} not registered")


@pytest.mark.asyncio
async def test_flag_for_ticket_tool_sets_state() -> None:
    agent = build_ai_agent(
        get_settings(), InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter()
    )
    tool = _find_tool(agent, "flag_for_ticket_tool")
    ctx = SimpleNamespace(state={})

    result = await tool(ctx, reason="complaint")  # type: ignore[operator]

    assert ctx.state["ticket_flagged"] is True
    assert ctx.state["ticket_reason"] == "complaint"
    assert "complaint" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_flag_for_ticket_tool.py -v`
Expected: FAIL — `AssertionError: tool flag_for_ticket_tool not registered`.

- [ ] **Step 3: Add the tool and register it**

In `agents.py`, add this tool right after `emit_handoff_tool` (before the `return Agent(...)`):

```python
    async def flag_for_ticket_tool(
        tool_context: ToolContext,
        reason: str,
    ) -> str:
        """Mark this conversation as needing a tracked support ticket.

        Call this when the conversation is actionable — a complaint, a
        service/warranty request, a sales lead, or an unresolved issue — even if
        the customer is NOT asking for a human yet.

        Args:
            tool_context: Context injected by the ADK runner.
            reason: Short reason (e.g. complaint, service_request, sales_lead).
        """
        tool_context.state["ticket_flagged"] = True
        tool_context.state["ticket_reason"] = reason
        return f"[internal] conversation flagged for ticket (Reason: {reason})."
```

Then add `flag_for_ticket_tool` to the `tools=[...]` list in `return Agent(...)`:

```python
        tools=[
            search_kb_tool,
            emit_handoff_tool,
            flag_for_ticket_tool,
            show_models_tool,
            classify_ticket_tool,
            book_test_drive_tool,
        ],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_flag_for_ticket_tool.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/agents.py src/chatbot/features/chat/test_flag_for_ticket_tool.py
git commit -m "feat(chat): add flag_for_ticket_tool for detection-gated ticketing"
```

---

### Task 5: Detection gate (pure decision function)

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/detection.py`
- Test: `apps/backend/src/chatbot/features/chat/test_detection.py`

**Interfaces:**
- Produces: `should_open_ticket(state: dict[str, Any]) -> bool` — True when `ticket_flagged` or `handoff_triggered` is True, or sentiment is strongly negative.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_detection.py`:

```python
from __future__ import annotations

from chatbot.features.chat.detection import should_open_ticket


def test_routine_conversation_is_not_opened() -> None:
    assert should_open_ticket({"sentiment": "positive"}) is False
    assert should_open_ticket({}) is False


def test_ticket_flag_opens() -> None:
    assert should_open_ticket({"ticket_flagged": True}) is True


def test_handoff_opens() -> None:
    assert should_open_ticket({"handoff_triggered": True}) is True


def test_negative_sentiment_opens() -> None:
    assert should_open_ticket({"sentiment": "negative"}) is True
    assert should_open_ticket({"sentiment": "VERY_NEGATIVE"}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_detection.py -v`
Expected: FAIL — `ModuleNotFoundError: detection`.

- [ ] **Step 3: Implement the gate**

Create `apps/backend/src/chatbot/features/chat/detection.py`:

```python
from __future__ import annotations

from typing import Any

_NEGATIVE_SENTIMENTS = {"negative", "very_negative", "angry", "frustrated"}


def should_open_ticket(state: dict[str, Any]) -> bool:
    """Decide whether a conversation warrants an OPEN, actionable Zendesk ticket.

    Hybrid gate: the AI's `flag_for_ticket_tool`, a handoff, or strongly negative
    sentiment all open a ticket. Everything else is a routine conversation that is
    still logged to Zendesk, but as a solved record.
    """
    if state.get("ticket_flagged") is True:
        return True
    if state.get("handoff_triggered") is True:
        return True
    sentiment = str(state.get("sentiment") or "").lower()
    return sentiment in _NEGATIVE_SENTIMENTS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_detection.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/detection.py src/chatbot/features/chat/test_detection.py
git commit -m "feat(chat): add detection gate deciding open ticket vs solved log"
```

---

### Task 6: ConversationLogPort + Zendesk methods + NoOp

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/ports.py`
- Modify: `apps/backend/src/chatbot/features/chat/adapters/zendesk.py`
- Create: `apps/backend/src/chatbot/features/chat/adapters/noop_conversation_log.py`
- Test: `apps/backend/src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py`

**Interfaces:**
- Produces (Protocol `ConversationLogPort`):
  - `ensure_conversation_ticket(session_id: str, subject: str, customer_name: str | None, customer_phone: str | None) -> str` — returns `ticket_id`, creating one (keyed by `external_id = session_id`) on first call, returning the cached id thereafter.
  - `append_conversation_comment(ticket_id: str, text: str, status: str | None = None) -> None` — posts a private comment, optionally setting ticket status (e.g. `open` / `solved`).
- `ZendeskAdapter` and `NoOpConversationLog` both implement it.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py`:

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
        handoff_store="memory",
    )
    return ZendeskAdapter(settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_conversation_ticket_creates_once_and_caches() -> None:
    adapter = _adapter()
    response = MagicMock(status_code=201, raise_for_status=lambda: None)
    response.json = lambda: {"ticket": {"id": 4242}}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        first = await adapter.ensure_conversation_ticket(
            "whatsapp-+60123456789", "[WhatsApp] chat", "Yuda", "+60123456789"
        )
        second = await adapter.ensure_conversation_ticket(
            "whatsapp-+60123456789", "[WhatsApp] chat", "Yuda", "+60123456789"
        )

    assert first == "4242"
    assert second == "4242"
    # Created exactly once; second call is served from cache.
    assert client.post.call_count == 1
    body = client.post.call_args.kwargs["json"]["ticket"]
    assert body["external_id"] == "whatsapp-+60123456789"


@pytest.mark.asyncio
async def test_append_conversation_comment_sets_status_private() -> None:
    adapter = _adapter()
    response = MagicMock(status_code=200, raise_for_status=lambda: None)
    client = MagicMock()
    client.put = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        await adapter.append_conversation_comment("4242", "USER: hi", status="solved")

    payload = client.put.call_args.kwargs["json"]["ticket"]
    assert payload["comment"]["public"] is False
    assert payload["comment"]["body"] == "USER: hi"
    assert payload["status"] == "solved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py -v`
Expected: FAIL — `AttributeError: ensure_conversation_ticket`.

- [ ] **Step 3: Add the `ConversationLogPort` protocol**

In `ports.py`, append at the end:

```python
class ConversationLogPort(Protocol):
    """Port for mirroring a full conversation into the support system as a ticket.

    Distinct from TicketingPort.create_ticket (which is escalation-shaped): this
    is per-conversation capture, deciding open ticket vs solved log via the
    detection gate.
    """

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        """Find-or-create the conversation's ticket (external_id == session_id).
        Returns the ticket id."""
        ...

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        """Append a private comment, optionally setting ticket status."""
        ...
```

- [ ] **Step 4: Implement the methods on `ZendeskAdapter`**

In `zendesk.py`, add `from chatbot.features.chat.ports import ConversationLogPort` to the ports import, add `ConversationLogPort` to the class bases:

```python
class ZendeskAdapter(ChatPort, TicketingPort, KnowledgePort, ConversationLogPort):
```

Add `self._conv_tickets: dict[str, str] = {}` at the end of `__init__`, then add these methods (after `add_private_note`):

```python
    # --- ConversationLogPort (per-conversation capture) ---
    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        cached = self._conv_tickets.get(session_id)
        if cached:
            return cached

        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets.json"
        requester: dict[str, str] = {
            "name": customer_name or f"Proton AI Customer ({session_id})",
            "email": f"{session_id}@proton.devoteam.example",
        }
        if customer_phone:
            requester["phone"] = customer_phone
        payload = {
            "ticket": {
                "subject": subject,
                "external_id": session_id,
                "requester": requester,
                "comment": {"body": "Conversation started.", "public": False},
            }
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    url, json=payload, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
                ticket_id = str(res.json().get("ticket", {}).get("id", "MOCK-ZEN-TKT"))
        except Exception as e:
            _log.error("zendesk_ensure_conversation_ticket_failed", error=str(e))
            ticket_id = "MOCK-ZEN-TKT"
        self._conv_tickets[session_id] = ticket_id
        return ticket_id

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"
        ticket: dict[str, object] = {"comment": {"body": text, "public": False}}
        if status:
            ticket["status"] = status
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url, json={"ticket": ticket}, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
        except Exception as e:
            _log.error("zendesk_append_conversation_comment_failed", ticket_id=ticket_id, error=str(e))
```

- [ ] **Step 5: Implement the NoOp adapter**

Create `apps/backend/src/chatbot/features/chat/adapters/noop_conversation_log.py`:

```python
from __future__ import annotations

from chatbot.features.chat.ports import ConversationLogPort


class NoOpConversationLog(ConversationLogPort):
    """Used when no support system is configured — conversation capture is skipped."""

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        return ""

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        return None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/ports.py src/chatbot/features/chat/adapters/zendesk.py src/chatbot/features/chat/adapters/noop_conversation_log.py src/chatbot/features/chat/adapters/test_zendesk_conversation_log.py
git commit -m "feat(chat): add ConversationLogPort with Zendesk + NoOp implementations"
```

---

### Task 7: `OrchestratorService.capture_conversation`

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py`
- Test: `apps/backend/src/chatbot/features/chat/test_capture_conversation.py`

**Interfaces:**
- Consumes: `should_open_ticket` (Task 5), `ConversationLogPort` (Task 6).
- Produces:
  - New optional ctor param `conversation_log_port: ConversationLogPort | None = None` (defaults to `NoOpConversationLog`), stored as `self._conversation_log_port`.
  - `async def capture_conversation(self, session_id: str, *, channel: str = "whatsapp", customer_name: str | None = None, customer_phone: str | None = None) -> None` — appends only the messages added since the last capture; sets status `open`/`solved` per the gate.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_capture_conversation.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.ports import ConversationLogPort
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


class _FakeLog(ConversationLogPort):
    def __init__(self) -> None:
        self.appended: list[tuple[str, str, str | None]] = []
        self.ensure_calls = 0

    async def ensure_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> str:
        self.ensure_calls += 1
        return "T1"

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        self.appended.append((ticket_id, text, status))


def _orchestrator(log: ConversationLogPort) -> OrchestratorService:
    settings = get_settings()
    return OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        conversation_log_port=log,
        runner_factory=lambda _agent: None,
    )


async def _seed_history(orch: OrchestratorService, session_id: str, msgs: list[dict[str, Any]]) -> None:
    session = await orch._adk_sessions.create_session(
        app_name="chatbot", user_id=session_id, session_id=session_id,
        state={"session_id": session_id, "chat_history": msgs},
    )
    assert session is not None


@pytest.mark.asyncio
async def test_routine_conversation_logs_solved() -> None:
    log = _FakeLog()
    orch = _orchestrator(log)
    await _seed_history(orch, "whatsapp-+60123", [
        {"role": "user", "text": "what time do you open?"},
        {"role": "assistant", "text": "9am-6pm daily."},
    ])

    await orch.capture_conversation("whatsapp-+60123", channel="WhatsApp")

    assert log.ensure_calls == 1
    assert len(log.appended) == 1
    _ticket, body, status = log.appended[0]
    assert status == "solved"
    assert "what time do you open?" in body


@pytest.mark.asyncio
async def test_flagged_conversation_logs_open() -> None:
    log = _FakeLog()
    orch = _orchestrator(log)
    await _seed_history(orch, "whatsapp-+60999", [
        {"role": "user", "text": "my car broke down, this is unacceptable"},
        {"role": "assistant", "text": "I'm sorry, let me help."},
    ])
    # Simulate the gate firing via the agent tool.
    session = await orch._adk_sessions.get_session(
        app_name="chatbot", user_id="whatsapp-+60999", session_id="whatsapp-+60999"
    )
    session.state["ticket_flagged"] = True

    await orch.capture_conversation("whatsapp-+60999")

    assert log.appended[0][2] == "open"


@pytest.mark.asyncio
async def test_only_new_messages_appended() -> None:
    log = _FakeLog()
    orch = _orchestrator(log)
    await _seed_history(orch, "whatsapp-+60777", [
        {"role": "user", "text": "first"},
        {"role": "assistant", "text": "reply one"},
    ])
    await orch.capture_conversation("whatsapp-+60777")

    session = await orch._adk_sessions.get_session(
        app_name="chatbot", user_id="whatsapp-+60777", session_id="whatsapp-+60777"
    )
    session.state["chat_history"].extend(
        [{"role": "user", "text": "second"}, {"role": "assistant", "text": "reply two"}]
    )
    await orch.capture_conversation("whatsapp-+60777")

    assert len(log.appended) == 2
    assert "second" in log.appended[1][1]
    assert "first" not in log.appended[1][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_capture_conversation.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'conversation_log_port'`.

- [ ] **Step 3: Wire the port into the constructor**

In `service.py`, add the import near the other local imports:

```python
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.detection import should_open_ticket
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    HumanAgentBridgePort,
    KnowledgePort,
    TextToSpeechPort,
    TicketingPort,
)
```

(Adjust the existing `from chatbot.features.chat.ports import (...)` block to include `ConversationLogPort` rather than adding a second import.)

Add the parameter to `__init__` (after `handoff_bridge`):

```python
        conversation_log_port: ConversationLogPort | None = None,
```

And in the body (near the other `self._...` assignments):

```python
        self._conversation_log_port: ConversationLogPort = (
            conversation_log_port or NoOpConversationLog()
        )
        self._logged_counts: dict[str, int] = {}
```

- [ ] **Step 4: Implement `capture_conversation`**

Add this method to `OrchestratorService` (e.g. after `handle_turn`):

```python
    async def capture_conversation(
        self,
        session_id: str,
        *,
        channel: str = "whatsapp",
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> None:
        """Mirror new conversation turns into the support system via the gate.

        Appends only the messages added since the last capture. Opens the ticket
        when the detection gate fires, otherwise logs as a solved record.
        """
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        state = session.state if session else {}
        history = state.get("chat_history", []) or []

        start = self._logged_counts.get(session_id, 0)
        new_msgs = history[start:]
        if not new_msgs:
            return

        status = "open" if should_open_ticket(state) else "solved"
        subject = f"[{channel}] Conversation {session_id}"
        try:
            ticket_id = await self._conversation_log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=subject,
                customer_name=customer_name,
                customer_phone=customer_phone,
            )
            body = "\n".join(f"{m['role'].upper()}: {m['text']}" for m in new_msgs)
            await self._conversation_log_port.append_conversation_comment(
                ticket_id, body, status=status
            )
            self._logged_counts[session_id] = len(history)
        except Exception as e:
            _log.error("capture_conversation_failed", session_id=session_id, error=str(e))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_capture_conversation.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/test_capture_conversation.py
git commit -m "feat(chat): add capture_conversation with detection-gated Zendesk logging"
```

---

### Task 8: `/webhooks/twilio-whatsapp` route

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py`
- Test: `apps/backend/src/chatbot/features/chat/test_router_twilio_whatsapp.py`

**Interfaces:**
- Consumes: `verify_twilio_signature` (Task 2), `TwilioChannelAdapter` (Task 3), `orchestrator.handle_turn` + `orchestrator.capture_conversation` (Task 7).
- Produces: `ChatRouter(..., twilio_adapter: ChatPort | None = None)` and a `POST /webhooks/twilio-whatsapp` route; `build_chat_router(..., twilio_adapter=None)`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_router_twilio_whatsapp.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
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
    mac = hmac.new(token.encode(), s.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


@pytest.fixture
def setup() -> tuple[TestClient, AsyncMock, str]:
    settings = get_settings()
    settings.twilio_auth_token = "test_token"  # type: ignore[misc]
    settings.twilio_account_sid = "AC1"  # type: ignore[misc]
    settings.twilio_whatsapp_number = "whatsapp:+60111"  # type: ignore[misc]

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )
    twilio = AsyncMock()
    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator, twilio_adapter=twilio))
    return TestClient(app), twilio, "test_token"


def test_rejects_bad_signature(setup: tuple[TestClient, AsyncMock, str]) -> None:
    client, _twilio, _token = setup
    res = client.post(
        "/webhooks/twilio-whatsapp",
        data={"From": "whatsapp:+60123", "Body": "hi", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "wrong"},
    )
    assert res.status_code == 401


def test_valid_request_runs_turn_and_replies(setup: tuple[TestClient, AsyncMock, str]) -> None:
    client, twilio, token = setup
    params = {"From": "whatsapp:+60123", "Body": "hi", "MessageSid": "SM1"}
    url = "http://testserver/webhooks/twilio-whatsapp"
    sig = _sign(token, url, params)

    res = client.post(
        "/webhooks/twilio-whatsapp", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    # Empty FakeRunner → fallback reply is produced and sent via Twilio.
    assert twilio.send_message.await_count == 1
    assert twilio.send_message.await_args.kwargs["conversation_id"] == "whatsapp:+60123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_router_twilio_whatsapp.py -v`
Expected: FAIL — `build_chat_router() got an unexpected keyword argument 'twilio_adapter'`.

- [ ] **Step 3: Add imports + ctor param + route registration**

In `router.py`, add imports:

```python
from chatbot.features.chat.ports import ChatPort, HumanAgentBridgePort
from chatbot.features.chat.twilio_signature import verify_twilio_signature
```

(Merge `ChatPort` into the existing ports import line.)

Extend `ChatRouter.__init__` signature and body:

```python
    def __init__(
        self,
        orchestrator: OrchestratorService,
        handoff_bridge: HandoffBridge | None = None,
        human_agent_bridge: HumanAgentBridgePort | None = None,
        twilio_adapter: ChatPort | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self._handoff_bridge = handoff_bridge
        self._human_agent_bridge = human_agent_bridge
        self._twilio_adapter = twilio_adapter
        self.router = APIRouter(tags=["chat"])
```

Register the route alongside the other webhooks (after the `zendesk-handback` registration):

```python
        self.router.add_api_route(
            "/webhooks/twilio-whatsapp", self.twilio_whatsapp_webhook, methods=["POST"]
        )
```

- [ ] **Step 4: Implement the handler**

Add this method to `ChatRouter` (in the CRM webhooks section):

```python
    async def twilio_whatsapp_webhook(self, request: Request) -> Response:
        """Inbound WhatsApp message from Twilio. Verify signature, run the AI,
        reply via Twilio REST, and mirror the conversation into Zendesk."""
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        token = self.orchestrator._settings.twilio_auth_token
        signature = request.headers.get("X-Twilio-Signature")
        if token and not verify_twilio_signature(token, str(request.url), params, signature):
            _log.warning("twilio_whatsapp_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        from_addr = params.get("From", "")
        body = (params.get("Body") or "").strip()
        if not from_addr or not body:
            return Response(status_code=200)

        e164 = from_addr.replace("whatsapp:", "")
        session_id = f"whatsapp-{e164}"
        profile_name = params.get("ProfileName")
        _log.info("twilio_whatsapp_received", session_id=session_id)

        result = await self.orchestrator.handle_turn(session_id=session_id, text=body)

        if result.reply and self._twilio_adapter is not None:
            await self._twilio_adapter.send_message(
                conversation_id=from_addr, text=result.reply
            )

        await self.orchestrator.capture_conversation(
            session_id,
            channel="WhatsApp",
            customer_name=profile_name,
            customer_phone=e164,
        )
        return Response(status_code=200)
```

- [ ] **Step 5: Thread the param through `build_chat_router`**

```python
def build_chat_router(
    orchestrator: OrchestratorService,
    handoff_bridge: HandoffBridge | None = None,
    human_agent_bridge: HumanAgentBridgePort | None = None,
    twilio_adapter: ChatPort | None = None,
) -> APIRouter:
    """Builds and returns the configured FastAPI router instance."""
    return ChatRouter(
        orchestrator=orchestrator,
        handoff_bridge=handoff_bridge,
        human_agent_bridge=human_agent_bridge,
        twilio_adapter=twilio_adapter,
    ).router
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_router_twilio_whatsapp.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_router_twilio_whatsapp.py
git commit -m "feat(chat): add /webhooks/twilio-whatsapp inbound route"
```

---

### Task 9: Wire WhatsApp into application bootstrap

**Files:**
- Modify: `apps/backend/src/chatbot/main.py`
- Test: `apps/backend/src/chatbot/features/chat/test_bootstrap_whatsapp.py`

**Interfaces:**
- Consumes: everything above. Wires `TwilioChannelAdapter` + `ConversationLogPort` into the orchestrator and router when configured.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_bootstrap_whatsapp.py`:

```python
from __future__ import annotations

from chatbot.main import bootstrap_application


def test_twilio_whatsapp_route_is_registered() -> None:
    app = bootstrap_application()
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/webhooks/twilio-whatsapp" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_bootstrap_whatsapp.py -v`
Expected: FAIL — route not registered (no `twilio_adapter` wired).

- [ ] **Step 3: Wire adapters in `main.py`**

Add imports near the other adapter imports:

```python
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    HumanAgentBridgePort,
    KnowledgePort,
    TextToSpeechPort,
    TicketingPort,
)
```

(Merge `ConversationLogPort` into the existing ports import.)

After the human-agent-bridge block (and before `orchestrator = OrchestratorService(...)`), add:

```python
    # --- Conversation capture (per-conversation Zendesk logging) ---
    conversation_log_port: ConversationLogPort
    if zendesk_client is not None:
        conversation_log_port = zendesk_client
    else:
        conversation_log_port = NoOpConversationLog()

    # --- Twilio channel (outbound WhatsApp) ---
    twilio_adapter: ChatPort | None = None
    if settings.twilio_account_sid and settings.twilio_auth_token:
        twilio_adapter = TwilioChannelAdapter(settings)
```

Pass `conversation_log_port` into the orchestrator:

```python
    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=tts_port,
        human_agent_bridge=human_agent_bridge,
        handoff_bridge=handoff_bridge,
        conversation_log_port=conversation_log_port,
    )
```

Pass `twilio_adapter` into the router:

```python
    app.include_router(
        build_chat_router(
            orchestrator=orchestrator,
            handoff_bridge=handoff_bridge,
            human_agent_bridge=human_agent_bridge,
            twilio_adapter=twilio_adapter,
        )
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_bootstrap_whatsapp.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + lint + types**

Run:
```bash
.venv/bin/pytest src/
.venv/bin/ruff check . --fix
.venv/bin/mypy src/ --strict
```
Expected: all tests pass; ruff clean; mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/main.py src/chatbot/features/chat/test_bootstrap_whatsapp.py
git commit -m "feat(chat): wire Twilio WhatsApp channel + conversation logging into bootstrap"
```

---

## Manual Verification (after Task 9)

Not a code task — do this once with real credentials to validate end-to-end:

1. Set `TWILIO_*` and `ZENDESK_*` in `.env`; set `CRM_PROVIDER=zendesk`.
2. Start the backend: `.venv/bin/uvicorn chatbot.main:app --reload`.
3. Expose it: `ngrok http 8000`; set the Twilio WhatsApp sandbox "When a message comes in" webhook to `https://<ngrok>/webhooks/twilio-whatsapp` (POST). Put the ngrok base in `TWILIO_WEBHOOK_BASE_URL`.
4. Join the WhatsApp sandbox from your phone, send "what models do you have?" → expect an AI reply in WhatsApp, and a **solved** Zendesk ticket with the transcript.
5. Send "my new car has a major fault, I want to complain" → expect the agent to call `flag_for_ticket_tool` (or sentiment to fire) → an **open** Zendesk ticket.

## Notes for the implementer

- **Scope:** this plan delivers AI-answered WhatsApp + detection-gated Zendesk capture + escalation creating a ticket (the existing `_escalate_handoff` path is reused unchanged). **Two-way live agent reply back over WhatsApp** (agent types in Zendesk → customer's WhatsApp) is NOT in this plan — on handoff the human follows up from Zendesk. Flag this if the demo needs live two-way handoff on WhatsApp.
- **Ticket creation is eager** (created on the first message, status set per gate) rather than the spec's lazy/idle-flush model. This is simpler and demo-safe; the lazy variant (idle-timeout sweep, §4.3 of the spec) is a later refinement.
- **`_conv_tickets` is in-memory** (single instance). For multi-instance/production, persist the `session_id → ticket_id` map (Firestore) or look up by `external_id` via the Zendesk search API.
- Do not touch `_escalate_handoff`, `create_ticket`, or the Sunshine bridge — capture is a separate path.

## Self-Review (completed by plan author)

- **Spec coverage:** webhook + signature (Task 2, 8) ✓; async REST reply (Task 3, 8) ✓; detection gate hybrid (Task 4 tool, Task 5 rules) ✓; Zendesk capture open vs solved (Task 6, 7) ✓; config/env (Task 1) ✓; wiring (Task 9) ✓; per-channel adapter selection by session prefix (Task 8/9) ✓. Deferred (noted): lazy/idle closed-log flush, two-way WhatsApp handoff, ticket-map persistence.
- **Placeholders:** none — every code/test step has full content.
- **Type consistency:** `ensure_conversation_ticket` / `append_conversation_comment` signatures match across ports.py, zendesk.py, noop, the `_FakeLog` test double, and `capture_conversation`. `verify_twilio_signature(auth_token, url, params, signature)` consistent in helper, route, and tests. `build_chat_router(..., twilio_adapter=)` consistent in router + main.
