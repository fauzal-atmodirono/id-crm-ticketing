from __future__ import annotations

import pytest

from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.test_capture_conversation import (  # reuse doubles
    _FakeLog,
    _LiveSessions,
    _orchestrator,
)


def test_parse_csat_accepts_1_to_5() -> None:
    assert OrchestratorService.parse_csat("4") == 4
    assert OrchestratorService.parse_csat("I'd say 5, thanks") == 5
    assert OrchestratorService.parse_csat("rating: 1") == 1


def test_parse_csat_rejects_out_of_range_or_text() -> None:
    assert OrchestratorService.parse_csat("great service") is None
    assert OrchestratorService.parse_csat("10") is None
    assert OrchestratorService.parse_csat("") is None


@pytest.mark.asyncio
async def test_record_csat_writes_comment_tag_and_resets_state() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot", user_id="whatsapp-+60123", session_id="whatsapp-+60123",
        state={"conversation_ticket_id": "T1", "whatsapp_handoff_state": "awaiting_survey"},
    )

    ok = await orch.record_csat("whatsapp-+60123", 4, channel="whatsapp")

    assert ok is True
    assert session.state["csat_score"] == 4
    assert session.state["whatsapp_handoff_state"] == "active"
    assert any("4/5" in text for _t, text, _s in log.appended)
    assert ("T1", "csat_4") in log.tags
