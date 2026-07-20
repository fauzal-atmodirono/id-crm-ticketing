from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import _EMAIL_HANDOFF_MESSAGE, build_chat_router
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
    orch._conversation_log_port.get_latest_public_comment = AsyncMock(return_value=("", None, None))
    orch.bind_email_ticket = AsyncMock()
    orch.conversation_state = AsyncMock(return_value="active")
    orch.needs_handoff = AsyncMock(return_value=False)
    orch.begin_handoff = AsyncMock()
    orch.handle_turn = AsyncMock(return_value=SimpleNamespace(reply="Here is your answer."))
    # dedup guard: no prior exchange by default → nothing is skipped
    orch.get_email_dedup = AsyncMock(return_value=(None, None))
    orch.remember_email_exchange = AsyncMock()
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
    orch.bind_email_ticket.assert_awaited_once_with("email-55", "55")


def test_handoff_pauses_and_posts_ack() -> None:
    orch = _orch()
    orch.needs_handoff = AsyncMock(return_value=True)
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    # the ack email is the LAST public reply; no status forced to solved
    args, _kwargs = orch._conversation_log_port.post_public_reply.await_args
    assert args[0] == "55"
    assert args[1] == _EMAIL_HANDOFF_MESSAGE
    orch.begin_handoff.assert_awaited_once_with(
        "email-55", ANY, channel="email", customer_name="Alice"
    )


def test_draft_assist_posts_private_note() -> None:
    orch = _orch()
    orch._settings.email_draft_assist = True
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    orch._conversation_log_port.post_public_reply.assert_not_awaited()
    orch._conversation_log_port.append_conversation_comment.assert_awaited_once_with(
        "55", "[AI draft]\nHere is your answer."
    )


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


def test_duplicate_inbound_is_skipped() -> None:
    """A re-fired trigger / Zendesk double-notification for the SAME customer
    comment must not be processed twice (no duplicate AI reply)."""
    orch = _orch()
    orch.get_email_dedup = AsyncMock(return_value=("How do I reset my password?", None))
    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    assert res.json()["status"] == "duplicate"
    orch.handle_turn.assert_not_awaited()
    orch._conversation_log_port.post_public_reply.assert_not_awaited()


def test_own_reply_fed_back_is_skipped() -> None:
    """If the AI's own public reply is fed back as customer input (requester ==
    Zendesk agent identity collision), it must be ignored — no reply loop."""
    orch = _orch()
    orch.get_email_dedup = AsyncMock(return_value=(None, "Here is your answer."))
    res = _client(orch).post("/webhooks/zendesk-email", json=_body(text="Here is your answer."))
    assert res.status_code == 200
    assert res.json()["status"] == "duplicate"
    orch.handle_turn.assert_not_awaited()


def test_new_followup_is_processed_and_remembered() -> None:
    """A genuinely new follow-up (different from the last inbound and the last
    reply) is processed, and both the inbound and the reply are recorded for
    future dedup."""
    orch = _orch()
    orch.get_email_dedup = AsyncMock(return_value=("an older question", "an older reply"))
    res = _client(orch).post("/webhooks/zendesk-email", json=_body(text="A brand new question"))
    assert res.status_code == 200
    orch.handle_turn.assert_awaited_once_with(session_id="email-55", text="A brand new question")
    orch.remember_email_exchange.assert_any_await("email-55", inbound="A brand new question")
    orch.remember_email_exchange.assert_any_await("email-55", reply="Here is your answer.")


def test_inbound_is_claimed_before_the_turn_runs() -> None:
    """The inbound must be recorded BEFORE the (slow) AI turn so concurrent
    re-fired triggers — which arrive while the first turn is still running — see
    the claim and dedup, instead of all racing through and double-replying."""
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

    res = _client(orch).post("/webhooks/zendesk-email", json=_body())
    assert res.status_code == 200
    assert order[0] == "remember_inbound", f"inbound claimed too late: {order}"
    assert "turn" in order


def test_active_fetches_comment_when_text_absent() -> None:
    """When the trigger sends only ticket_id (no text field), the handler must
    fetch the customer's comment from Zendesk and use it as the turn text."""
    orch = _orch()
    orch._conversation_log_port.get_latest_public_comment = AsyncMock(
        return_value=("Reset my password", "Bob", "bob@acme.com")
    )
    # body without text or requester fields (ticket-id-only trigger payload)
    body_no_text: dict[str, str] = {"ticket_id": "55"}
    res = _client(orch).post("/webhooks/zendesk-email", json=body_no_text)
    assert res.status_code == 200
    orch._conversation_log_port.get_latest_public_comment.assert_awaited_once_with("55")
    orch.handle_turn.assert_awaited_once_with(session_id="email-55", text="Reset my password")
    orch._conversation_log_port.post_public_reply.assert_awaited_once_with(
        "55", "Here is your answer.", status="pending"
    )
