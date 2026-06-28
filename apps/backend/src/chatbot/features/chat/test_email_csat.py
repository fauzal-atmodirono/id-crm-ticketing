from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import _EMAIL_CSAT_THANKS, build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _orch() -> MagicMock:
    orch = MagicMock()
    orch._settings = get_settings()
    orch._settings.zendesk_support_webhook_secret = ""
    orch._conversation_log_port = MagicMock()
    orch._conversation_log_port.post_public_reply = AsyncMock()
    orch._conversation_log_port.get_latest_public_comment = AsyncMock(return_value=("", None, None))
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


def _email(text: str) -> dict[str, str]:
    return {"ticket_id": "55", "text": text, "requester_email": "alice@acme.com"}


def test_valid_csat_records_email_channel() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="awaiting_survey")
    res = _client(orch).post("/webhooks/zendesk-email", json=_email("5"))
    assert res.status_code == 200
    orch.record_csat.assert_awaited_once_with("email-55", 5, channel="email")
    # M2: thank-you must re-solve the ticket so the customer's reply doesn't
    # leave it reopened after CSAT is recorded.
    orch._conversation_log_port.post_public_reply.assert_awaited_once_with(
        "55", _EMAIL_CSAT_THANKS, status="solved"
    )


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
    orch.bind_email_ticket.assert_awaited_once_with("email-55", "55")
    orch._conversation_log_port.post_public_reply.assert_awaited_once()


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
