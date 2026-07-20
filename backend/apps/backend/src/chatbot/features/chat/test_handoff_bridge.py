from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chatbot.features.chat.adapters.handoff_store import InMemoryHandoffStore
from chatbot.features.chat.handoff_bridge import HandoffBridge
from chatbot.features.chat.models import AgentMessageEvent


@pytest.mark.asyncio
async def test_handoff_bridge_saves_transcript_and_messages() -> None:
    store = InMemoryHandoffStore()
    bridge = HandoffBridge(store)

    session_id = "test-session"
    conv_id = "test-conv"
    initial_transcript = [
        {"role": "user", "text": "hello", "timestamp": datetime.now(UTC).isoformat()},
        {
            "role": "assistant",
            "text": "how can I help?",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    ]

    # Register handoff
    await bridge.register(session_id, conv_id, transcript=initial_transcript)

    # Check store transcript
    assert store._transcripts[session_id] == initial_transcript

    # Save a user message
    await bridge.save_message(session_id, "user", "transfer me please")
    assert len(store._transcripts[session_id]) == 3
    assert store._transcripts[session_id][2]["role"] == "user"
    assert store._transcripts[session_id][2]["text"] == "transfer me please"

    # Publish agent message (should automatically call save_message)
    event = AgentMessageEvent(
        conversation_id=conv_id,
        author_name="Agent Joe",
        text="Hello, I am Joe",
        timestamp=datetime.now(UTC),
    )
    await bridge.publish(event)

    assert len(store._transcripts[session_id]) == 4
    assert store._transcripts[session_id][3]["role"] == "agent"
    assert store._transcripts[session_id][3]["text"] == "Hello, I am Joe"
