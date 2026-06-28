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


def _body(**kw: str) -> dict[str, str]:
    base: dict[str, str] = {
        "ticket_id": "55",
        "text": "How do I reset my password?",
        "requester_name": "Alice",
        "requester_email": "alice@acme.com",
    }
    base.update(kw)
    return base


def test_active_posts_public_reply_pending() -> None:
    orch = _orch()
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    orch.handle_turn.assert_awaited_once_with(
        session_id="email-55", text="How do I reset my password?"
    )
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
    args, _kwargs = orch._conversation_log_port.post_public_reply.await_args
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
    res = _client(orch).post(
        "/webhooks/zendesk-email", json=_body(requester_email="no-reply@acme.com")
    )
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
