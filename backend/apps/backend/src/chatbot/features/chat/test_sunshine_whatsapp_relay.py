from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.models import AgentMessageEvent
from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(human_bridge: MagicMock, handoff_bridge: MagicMock, twilio: AsyncMock) -> TestClient:
    settings = get_settings()
    app = create_app(settings)
    app.include_router(
        build_chat_router(
            MagicMock(),  # orchestrator (unused on the sunshine path)
            handoff_bridge=handoff_bridge,
            human_agent_bridge=human_bridge,
            twilio_adapter=twilio,
        )
    )
    return TestClient(app)


def _bridges(session_id: str) -> tuple[MagicMock, MagicMock]:
    human = MagicMock()
    human.verify_webhook_signature.return_value = True
    human.parse_webhook_events.return_value = [
        AgentMessageEvent(
            conversation_id="conv1",
            author_name="Agent",
            text="Hi, I can help with your complaint.",
            timestamp=datetime.now(UTC),
        )
    ]
    handoff = MagicMock()
    handoff.publish = AsyncMock()
    handoff.session_id_for = AsyncMock(return_value=session_id)
    return human, handoff


def test_agent_reply_relayed_to_whatsapp() -> None:
    human, handoff = _bridges("whatsapp-+60123")
    twilio = AsyncMock()
    res = _client(human, handoff, twilio).post(
        "/webhooks/sunshine", content=b"{}", headers={"x-api-key": "k"}
    )

    assert res.status_code == 200
    twilio.send_message.assert_awaited_once()
    assert twilio.send_message.await_args.kwargs["conversation_id"] == "whatsapp:+60123"
    assert "Hi, I can help with your complaint." in twilio.send_message.await_args.kwargs["text"]


def test_web_session_is_not_relayed_to_whatsapp() -> None:
    human, handoff = _bridges("web-session-abc")
    twilio = AsyncMock()
    res = _client(human, handoff, twilio).post(
        "/webhooks/sunshine", content=b"{}", headers={"x-api-key": "k"}
    )

    assert res.status_code == 200
    twilio.send_message.assert_not_awaited()
