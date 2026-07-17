"""Chatwoot EMAIL-inbox branch of /webhooks/chatwoot.

Email-on-Chatwoot fills the channel gap left by the Zendesk->Chatwoot migration:
an email lands in a Chatwoot EMAIL-type inbox, Chatwoot opens a conversation and
fires a message_created webhook, and we run the AI + post a PUBLIC reply so
Chatwoot emails it back. The conversation IS the email thread, so it reuses the
shared _handle_email_turn flow (session_id = email-<conv_id>, ticket_id = conv_id).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import _EMAIL_HANDOFF_MESSAGE, build_chat_router
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app

_EMAIL_INBOX = 9


def _orch(**settings_overrides: Any) -> MagicMock:
    kwargs: dict[str, Any] = {
        "crm_provider": "chatwoot",
        "phone_enabled": False,
        "chatwoot_webhook_secret": "",
        "chatwoot_inbox_id": 7,  # API-channel inbox, distinct from the email inbox
        "chatwoot_email_inbox_id": _EMAIL_INBOX,
        "email_draft_assist": False,
    }
    kwargs.update(settings_overrides)
    settings = Settings(**kwargs)
    orch = MagicMock()
    orch._settings = settings
    orch._conversation_log_port = MagicMock()
    orch._conversation_log_port.post_public_reply = AsyncMock()
    orch._conversation_log_port.append_conversation_comment = AsyncMock()
    orch.bind_email_ticket = AsyncMock()
    orch.conversation_state = AsyncMock(return_value="active")
    orch.needs_handoff = AsyncMock(return_value=False)
    orch.begin_handoff = AsyncMock()
    orch.handle_turn = AsyncMock(return_value=SimpleNamespace(reply="Here is your answer."))
    orch.get_email_dedup = AsyncMock(return_value=(None, None))
    orch.remember_email_exchange = AsyncMock()
    return orch


def _client(orch: MagicMock) -> TestClient:
    app = create_app(orch._settings)
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def _payload(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event": "message_created",
        "message_type": "incoming",
        "private": False,
        "content": "How do I reset my password?",
        "conversation": {"id": 42, "inbox_id": _EMAIL_INBOX},
        "sender": {"id": 1, "name": "Alice", "email": "alice@acme.com"},
    }
    base.update(kw)
    return base


def test_incoming_email_runs_ai_and_posts_public_reply() -> None:
    orch = _orch()
    res = _client(orch).post("/webhooks/chatwoot", json=_payload())
    assert res.status_code == 200
    orch.handle_turn.assert_awaited_once_with(
        session_id="email-42", text="How do I reset my password?"
    )
    orch._conversation_log_port.post_public_reply.assert_awaited_once_with(
        "42", "Here is your answer.", status="pending"
    )
    orch.bind_email_ticket.assert_awaited_once_with("email-42", "42")


def test_draft_assist_posts_private_note() -> None:
    orch = _orch(email_draft_assist=True)
    res = _client(orch).post("/webhooks/chatwoot", json=_payload())
    assert res.status_code == 200
    orch._conversation_log_port.post_public_reply.assert_not_awaited()
    orch._conversation_log_port.append_conversation_comment.assert_awaited_once_with(
        "42", "[AI draft]\nHere is your answer."
    )


def test_handoff_pauses_and_posts_ack() -> None:
    orch = _orch()
    orch.needs_handoff = AsyncMock(return_value=True)
    res = _client(orch).post("/webhooks/chatwoot", json=_payload())
    assert res.status_code == 200
    args, _kwargs = orch._conversation_log_port.post_public_reply.await_args
    assert args[0] == "42"
    assert args[1] == _EMAIL_HANDOFF_MESSAGE
    orch.begin_handoff.assert_awaited_once_with(
        "email-42", ANY, channel="email", customer_name="Alice"
    )


def test_non_email_inbox_does_not_trigger_email_path() -> None:
    # A message in the API-channel inbox (chatwoot_inbox_id, not the email inbox)
    # must NOT take the email path — it falls through to the native branches.
    orch = _orch()
    res = _client(orch).post(
        "/webhooks/chatwoot",
        json=_payload(conversation={"id": 42, "inbox_id": 7}),
    )
    assert res.status_code == 200
    # Handoff-console default: incoming in the API inbox is ignored, AI never runs.
    assert res.json() == {"status": "ignored_incoming"}
    orch.handle_turn.assert_not_awaited()
    orch._conversation_log_port.post_public_reply.assert_not_awaited()


def test_outgoing_message_is_ignored() -> None:
    # Our own AI reply / an agent reply comes back as outgoing — never answer it.
    orch = _orch()
    res = _client(orch).post(
        "/webhooks/chatwoot",
        json=_payload(message_type="outgoing"),
    )
    assert res.status_code == 200
    orch.handle_turn.assert_not_awaited()
    orch._conversation_log_port.post_public_reply.assert_not_awaited()


def test_private_note_is_ignored() -> None:
    orch = _orch()
    res = _client(orch).post(
        "/webhooks/chatwoot",
        json=_payload(private=True),
    )
    assert res.status_code == 200
    orch.handle_turn.assert_not_awaited()


def test_no_reply_sender_ignored() -> None:
    orch = _orch()
    res = _client(orch).post(
        "/webhooks/chatwoot",
        json=_payload(sender={"id": 1, "name": "Mailer", "email": "no-reply@acme.com"}),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ignored"
    orch.handle_turn.assert_not_awaited()


def test_paused_conversation_is_noop() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="paused")
    res = _client(orch).post("/webhooks/chatwoot", json=_payload())
    assert res.status_code == 200
    assert res.json()["status"] == "paused"
    orch.handle_turn.assert_not_awaited()
    orch._conversation_log_port.post_public_reply.assert_not_awaited()


def test_blank_content_ignored() -> None:
    orch = _orch()
    res = _client(orch).post("/webhooks/chatwoot", json=_payload(content="   "))
    assert res.status_code == 200
    assert res.json()["status"] == "ignored"
    orch.handle_turn.assert_not_awaited()


def test_duplicate_inbound_is_skipped() -> None:
    orch = _orch()
    orch.get_email_dedup = AsyncMock(return_value=("How do I reset my password?", None))
    res = _client(orch).post("/webhooks/chatwoot", json=_payload())
    assert res.status_code == 200
    assert res.json()["status"] == "duplicate"
    orch.handle_turn.assert_not_awaited()


def test_own_reply_fed_back_is_skipped() -> None:
    orch = _orch()
    orch.get_email_dedup = AsyncMock(return_value=(None, "Here is your answer."))
    res = _client(orch).post("/webhooks/chatwoot", json=_payload(content="Here is your answer."))
    assert res.status_code == 200
    assert res.json()["status"] == "duplicate"
    orch.handle_turn.assert_not_awaited()


def test_inbound_claimed_before_turn_runs() -> None:
    orch = _orch()
    order: list[str] = []

    async def _remember(*_a: object, **kwargs: object) -> None:
        if "inbound" in kwargs:
            order.append("remember_inbound")

    async def _turn(*_a: object, **_k: object) -> SimpleNamespace:
        order.append("turn")
        return SimpleNamespace(reply="Here is your answer.")

    orch.remember_email_exchange = AsyncMock(side_effect=_remember)
    orch.handle_turn = AsyncMock(side_effect=_turn)

    res = _client(orch).post("/webhooks/chatwoot", json=_payload())
    assert res.status_code == 200
    assert order[0] == "remember_inbound", f"inbound claimed too late: {order}"
    assert "turn" in order


def test_email_disabled_when_inbox_unset() -> None:
    # chatwoot_email_inbox_id=0 disables email routing: an "email-shaped" payload
    # falls through to the native branches instead of the email flow.
    orch = _orch(chatwoot_email_inbox_id=0, chatwoot_inbox_id=0)
    res = _client(orch).post("/webhooks/chatwoot", json=_payload())
    assert res.status_code == 200
    # No email inbox → not the email path; with default bot_replies_to_incoming
    # False, an incoming message is ignored, AI never runs.
    assert res.json()["status"] == "ignored_incoming"
    orch.handle_turn.assert_not_awaited()
