import pytest

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.case_state import CaseState, record_transition


def test_case_state_has_wip() -> None:
    assert CaseState.WIP == "WIP"
    assert {s.value for s in CaseState} == {"NEW", "OPEN", "WIP", "PENDING", "SOLVED"}


@pytest.mark.asyncio
async def test_record_transition_appends_one_entry() -> None:
    log = InMemoryAuditLog()
    await record_transition(
        log,
        ticket_id="T1",
        session_id="sim-1",
        actor="agent_ali",
        from_state=CaseState.OPEN,
        to_state=CaseState.WIP,
        at="2026-07-16T09:00:00+00:00",
        remark="picked up",
    )
    rows = await log.list_for_ticket("T1")
    assert len(rows) == 1
    assert rows[0].from_state == "OPEN" and rows[0].to_state == "WIP"
    assert rows[0].actor == "agent_ali" and rows[0].remark == "picked up"
