import pytest

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.ports import AuditEntry


def _entry(ticket_id: str = "T1", to_state: str = "WIP") -> AuditEntry:
    return AuditEntry(
        ticket_id=ticket_id,
        session_id="whatsapp-+60",
        actor="agent_ali",
        from_state="OPEN",
        to_state=to_state,
        at="2026-07-16T09:00:00+00:00",
        remark="started",
    )


@pytest.mark.asyncio
async def test_append_then_list_for_ticket() -> None:
    log = InMemoryAuditLog()
    await log.append(_entry())
    await log.append(_entry(to_state="PENDING"))
    rows = await log.list_for_ticket("T1")
    assert [r.to_state for r in rows] == ["WIP", "PENDING"]


@pytest.mark.asyncio
async def test_list_isolates_by_ticket() -> None:
    log = InMemoryAuditLog()
    await log.append(_entry(ticket_id="T1"))
    await log.append(_entry(ticket_id="T2"))
    assert len(await log.list_for_ticket("T1")) == 1
