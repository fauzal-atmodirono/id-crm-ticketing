from __future__ import annotations

import pytest

from chatbot.features.chat.test_capture_conversation import _FakeLog, _LiveSessions, _orchestrator


@pytest.mark.asyncio
async def test_whatsapp_state_defaults_active() -> None:
    orch = _orchestrator(_FakeLog())
    assert await orch.whatsapp_state("whatsapp-+60000") == "active"


@pytest.mark.asyncio
async def test_begin_handoff_opens_ticket_and_pauses() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot", user_id="whatsapp-+60123", session_id="whatsapp-+60123",
        state={"chat_history": [], "handoff_triggered": True},
    )

    assert await orch.needs_whatsapp_handoff("whatsapp-+60123") is True
    await orch.begin_whatsapp_handoff("whatsapp-+60123", "Customer wants a human.")

    assert session.state["whatsapp_handoff_state"] == "paused"
    assert log.ensure_calls == 1
    _ticket, body, status = log.appended[0]
    assert status == "open"
    assert "Customer wants a human." in body
    # Once paused, it no longer "needs" a fresh handoff.
    assert await orch.needs_whatsapp_handoff("whatsapp-+60123") is False
