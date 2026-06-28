from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock, twilio: AsyncMock, handoff: MagicMock) -> TestClient:
    settings = get_settings()
    app = create_app(settings)
    app.include_router(build_chat_router(orch, handoff_bridge=handoff, twilio_adapter=twilio))
    return TestClient(app)


def test_handback_whatsapp_starts_text_survey() -> None:
    orch = MagicMock()
    # Use real settings so zendesk_support_webhook_secret defaults to "" (avoids 401).
    orch._settings = get_settings()
    orch._settings.zendesk_support_webhook_secret = ""
    orch.begin_survey = AsyncMock()
    # Genuine handoff -> solved transition: session is currently paused.
    orch.whatsapp_state = AsyncMock(return_value="paused")
    # The handler calls _ticketing_port.unpause_ai_for_session — must be awaitable.
    orch._ticketing_port.unpause_ai_for_session = AsyncMock()
    twilio = AsyncMock()
    handoff = MagicMock()
    # The handler calls _handoff_bridge.unregister — must be awaitable.
    handoff.unregister = AsyncMock()
    handoff.publish_survey = AsyncMock()

    client = _client(orch, twilio, handoff)
    res = client.post("/webhooks/zendesk-handback", json={"session_id": "whatsapp-+60123"})

    assert res.status_code == 200
    orch.begin_survey.assert_awaited_once_with("whatsapp-+60123")
    twilio.send_message.assert_awaited_once()
    handoff.publish_survey.assert_not_awaited()  # WhatsApp uses text, not SSE


def test_handback_whatsapp_skips_survey_when_not_paused() -> None:
    """Re-fires of the handback trigger (e.g. from our own CSAT comment/tag
    writes on the solved ticket) must NOT re-send the survey: only a paused
    session starts a survey."""
    orch = MagicMock()
    orch._settings = get_settings()
    orch._settings.zendesk_support_webhook_secret = ""
    orch.begin_survey = AsyncMock()
    # Survey already sent (awaiting_survey) — not a fresh handoff.
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")
    orch._ticketing_port.unpause_ai_for_session = AsyncMock()
    twilio = AsyncMock()
    handoff = MagicMock()
    handoff.unregister = AsyncMock()
    handoff.publish_survey = AsyncMock()

    client = _client(orch, twilio, handoff)
    res = client.post("/webhooks/zendesk-handback", json={"session_id": "whatsapp-+60123"})

    assert res.status_code == 200
    orch.begin_survey.assert_not_awaited()  # no duplicate survey
    twilio.send_message.assert_not_awaited()


def test_handback_web_publishes_survey_event() -> None:
    orch = MagicMock()
    # Use real settings so zendesk_support_webhook_secret defaults to "" (avoids 401).
    orch._settings = get_settings()
    orch._settings.zendesk_support_webhook_secret = ""
    orch.begin_survey = AsyncMock()
    # The handler calls _ticketing_port.unpause_ai_for_session — must be awaitable.
    orch._ticketing_port.unpause_ai_for_session = AsyncMock()
    twilio = AsyncMock()
    handoff = MagicMock()
    # The handler calls _handoff_bridge.unregister — must be awaitable.
    handoff.unregister = AsyncMock()
    handoff.publish_survey = AsyncMock()

    client = _client(orch, twilio, handoff)
    res = client.post("/webhooks/zendesk-handback", json={"session_id": "sim-abc"})

    assert res.status_code == 200
    orch.begin_survey.assert_awaited_once_with("sim-abc")
    handoff.publish_survey.assert_awaited_once_with("sim-abc")
    twilio.send_message.assert_not_awaited()


def test_handback_web_survey_published_before_unregister() -> None:
    """publish_survey must be called before unregister so the SSE event is queued
    before the stream-closing None sentinel is pushed to subscribers."""
    call_order: list[str] = []

    orch = MagicMock()
    orch._settings = get_settings()
    orch._settings.zendesk_support_webhook_secret = ""
    orch.begin_survey = AsyncMock()
    orch._ticketing_port.unpause_ai_for_session = AsyncMock()

    twilio = AsyncMock()

    handoff = MagicMock()

    async def _publish_survey(_session_id: str) -> None:
        call_order.append("publish_survey")

    async def _unregister(_session_id: str) -> None:
        call_order.append("unregister")

    handoff.publish_survey = AsyncMock(side_effect=_publish_survey)
    handoff.unregister = AsyncMock(side_effect=_unregister)

    client = _client(orch, twilio, handoff)
    res = client.post("/webhooks/zendesk-handback", json={"session_id": "sim-abc"})

    assert res.status_code == 200
    assert call_order == ["publish_survey", "unregister"], (
        f"Expected publish_survey before unregister, got: {call_order}"
    )
