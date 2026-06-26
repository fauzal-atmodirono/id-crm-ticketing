from __future__ import annotations

import pytest

from chatbot.features.chat.test_capture_conversation import _FakeLog, _LiveSessions, _orchestrator


@pytest.mark.asyncio
async def test_forward_posts_prefixed_private_note() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    await sessions.create_session(
        app_name="chatbot", user_id="whatsapp-+60123", session_id="whatsapp-+60123",
        state={"conversation_ticket_id": "T1"},
    )

    await orch.forward_whatsapp_to_agent("whatsapp-+60123", "is my car ready?")

    assert log.appended[-1][0] == "T1"
    assert "Customer (WhatsApp): is my car ready?" in log.appended[-1][1]
