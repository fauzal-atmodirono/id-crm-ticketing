from __future__ import annotations

import pytest

from chatbot.features.chat.test_capture_conversation import (  # reuse doubles
    _FakeLog,
    _LiveSessions,
    _orchestrator,
)


@pytest.mark.asyncio
async def test_record_nps_writes_comment_tag_and_state() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot",
        user_id="sim-abc",
        session_id="sim-abc",
        state={"conversation_ticket_id": "T1"},
    )

    ok = await orch.record_nps("sim-abc", 9, channel="web")

    assert ok is True
    assert session.state["nps_score"] == 9
    assert any("9/10" in text for _t, text, _s in log.appended)
    assert ("T1", "nps_9") in log.tags


@pytest.mark.asyncio
async def test_record_nps_returns_false_when_no_session() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    assert await orch.record_nps("missing-session", 8) is False


@pytest.mark.asyncio
async def test_record_nps_does_not_touch_handoff_state() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot",
        user_id="sim-xyz",
        session_id="sim-xyz",
        state={
            "conversation_ticket_id": "T2",
            "whatsapp_handoff_state": "paused",
            "handoff_triggered": True,
        },
    )

    await orch.record_nps("sim-xyz", 10, channel="web")

    # NPS is decoupled: handoff/survey state must be left exactly as it was.
    assert session.state["whatsapp_handoff_state"] == "paused"
    assert session.state["handoff_triggered"] is True
    assert session.state["nps_score"] == 10


@pytest.mark.asyncio
async def test_record_nps_state_survives_log_failure() -> None:
    class _FailLog(_FakeLog):
        async def append_conversation_comment(
            self, ticket_id: str, text: str, status: str | None = None
        ) -> None:
            raise RuntimeError("Zendesk connection failed")

    log = _FailLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot",
        user_id="sim-fail",
        session_id="sim-fail",
        state={"conversation_ticket_id": "T3"},
    )

    ok = await orch.record_nps("sim-fail", 7, channel="web")

    assert ok is True
    assert session.state["nps_score"] == 7
