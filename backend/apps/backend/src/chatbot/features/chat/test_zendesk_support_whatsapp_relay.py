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
    orch = MagicMock()
    orch._settings.zendesk_support_webhook_secret = ""  # bypass secret check in handler
    app.include_router(
        build_chat_router(orch, handoff_bridge=handoff_bridge, twilio_adapter=twilio)
    )
    return TestClient(app)


def test_agent_reply_on_support_ticket_relayed_to_whatsapp() -> None:
    twilio = AsyncMock()
    handoff = MagicMock()
    client = _client(twilio, handoff)

    # Body shape matches ZendeskSupportWebhookPayload: session_id, text, author_name.
    # The comment text field is `text` (not `comment_text`).
    res = client.post(
        "/webhooks/zendesk-support",
        json={
            "session_id": "whatsapp-+60123",
            "text": "Hi, your car is ready.",
            "author_name": "Agent Smith",
        },
    )

    assert res.status_code == 200
    twilio.send_message.assert_awaited_once()
    assert twilio.send_message.await_args.kwargs["conversation_id"] == "whatsapp:+60123"
    assert "your car is ready" in twilio.send_message.await_args.kwargs["text"]
