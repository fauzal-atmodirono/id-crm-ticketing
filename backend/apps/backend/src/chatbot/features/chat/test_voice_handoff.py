from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.features.chat.adapters.handoff_store import InMemoryHandoffStore
from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.handoff_bridge import HandoffBridge
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


@pytest.mark.asyncio
async def test_handle_voice_turn_under_handoff() -> None:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    # Set up handoff bridge & human agent bridge mock
    store = InMemoryHandoffStore()
    handoff_bridge = HandoffBridge(store)
    human_agent_bridge = AsyncMock()

    # Register active handoff
    await handoff_bridge.register("session-123", "conv-123")

    # Mock is_ai_paused to return True (human is active)
    await ticketing_port.pause_ai_for_session("session-123")

    svc = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=voice_client,
        human_agent_bridge=human_agent_bridge,
        handoff_bridge=handoff_bridge,
    )

    # Mock self._genai_client.aio.models.generate_content to simulate transcription
    mock_response = MagicMock()
    mock_response.text = "This is a transcribed test message."
    svc._genai_client = MagicMock()
    svc._genai_client.aio = MagicMock()
    svc._genai_client.aio.models = MagicMock()
    svc._genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    # Process turn
    audio_reply, result = await svc.handle_voice_turn(
        session_id="session-123",
        audio_bytes=b"fake WAV audio data",
        audio_mime_type="audio/wav",
    )

    # Verify results
    assert audio_reply == b""
    assert result.reply is None
    assert result.forwarded_to_agent is True
    assert result.user_transcription == "This is a transcribed test message."

    # Verify that the message was forwarded and saved
    human_agent_bridge.forward_customer_message.assert_awaited_once_with(
        conversation_id="conv-123",
        user_external_id="session-123",
        text="This is a transcribed test message.",
    )

    # Check that transcript was updated in the store
    assert len(store._transcripts["session-123"]) == 1
    assert store._transcripts["session-123"][0]["role"] == "user"
    assert store._transcripts["session-123"][0]["text"] == "This is a transcribed test message."
