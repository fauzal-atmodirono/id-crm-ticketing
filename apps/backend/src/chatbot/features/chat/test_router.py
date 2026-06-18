from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

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
        # Yields an empty async generator (mocking no response / fallback)
        for _i in range(0):
            yield None


@pytest.fixture
def client() -> TestClient:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    # Orchestrator with mock runner factory to bypass real Vertex AI calls
    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        stt_port=voice_client,
        tts_port=voice_client,
        runner_factory=lambda _agent: _FakeRunner(),
    )

    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator))
    return TestClient(app)


def test_chatwoot_webhook_ignored_if_not_message_created(client: TestClient) -> None:
    payload = {
        "event": "conversation_status_changed",
        "conversation": {"id": 123},
        "sender": {"id": 456, "name": "Test"},
    }
    response = client.post("/webhooks/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


def test_chatwoot_webhook_processed_correctly(client: TestClient) -> None:
    payload = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "Hello",
        "conversation": {"id": 123},
        "sender": {"id": 456, "name": "Test"},
    }
    response = client.post("/webhooks/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_twilio_voice_welcome_returns_valid_twiml(client: TestClient) -> None:
    data = {
        "CallSid": "CA12345",
        "From": "+62812345678",
    }
    response = client.post("/webhooks/voice/twilio", data=data)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "<Response>" in response.text
    assert "<Gather" in response.text
    assert 'language="id-ID"' in response.text


def test_twilio_voice_process_empty_speech_asks_to_repeat(client: TestClient) -> None:
    data = {
        "CallSid": "CA12345",
        "SpeechResult": "",
    }
    response = client.post("/webhooks/voice/twilio/process", data=data)
    assert response.status_code == 200
    assert "<Response>" in response.text
    assert "ulangi kembali" in response.text


def test_twilio_voice_process_valid_speech_returns_response(client: TestClient) -> None:
    data = {
        "CallSid": "CA12345",
        "SpeechResult": "Saya butuh bantuan reset password",
    }
    response = client.post("/webhooks/voice/twilio/process", data=data)
    assert response.status_code == 200
    assert "<Response>" in response.text
    # Check that Twilio voice responses say the reply or fallback
    assert "<Say" in response.text
