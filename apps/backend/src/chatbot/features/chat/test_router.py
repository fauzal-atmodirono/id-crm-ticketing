from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import _EMPTY_REPLY_FALLBACK, OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeRunner:
    async def run_async(self, **_: Any) -> AsyncGenerator[Any, None]:
        for _i in range(0):
            yield None


@pytest.fixture
def client() -> TestClient:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
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


def test_chat_turn_returns_shape(client: TestClient) -> None:
    response = client.post(
        "/chat/turn",
        json={"session_id": "frontend-test", "text": "Hello"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "reply" in body
    assert "language" in body
    assert "sentiment" in body
    assert "handoff" in body


def test_voice_turn_returns_mp3_with_reply_header(client: TestClient) -> None:
    audio_payload = b"\x00" * 64
    response = client.post(
        "/voice/turn",
        data={"session_id": "voice-test"},
        files={"audio": ("clip.ogg", audio_payload, "audio/ogg")},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    # FakeRunner yields nothing → empty reply → retry → graceful fallback reply,
    # which IS synthesized so the user never gets a blank turn.
    assert unquote(response.headers["X-Reply-Text"]) == _EMPTY_REPLY_FALLBACK
    assert response.content != b""


@pytest.mark.asyncio
async def test_zendesk_handback_webhook_success() -> None:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=voice_client,
        runner_factory=lambda _agent: _FakeRunner(),
    )

    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator))
    local_client = TestClient(app)

    # 1. Pause the AI
    await ticketing_port.pause_ai_for_session("session-handback-test")
    assert await ticketing_port.is_ai_paused("session-handback-test") is True

    # 2. Call handback endpoint
    payload = {"session_id": "session-handback-test", "status": "solved"}

    # Send secret header if required by settings
    headers = {}
    if settings.zendesk_support_webhook_secret:
        headers["x-proton-webhook-secret"] = settings.zendesk_support_webhook_secret

    response = local_client.post("/webhooks/zendesk-handback", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "unpaused", "session_id": "session-handback-test"}

    # 3. Verify AI is unpaused
    assert await ticketing_port.is_ai_paused("session-handback-test") is False
