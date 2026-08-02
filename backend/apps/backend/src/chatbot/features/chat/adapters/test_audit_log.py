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


@pytest.mark.asyncio
async def test_list_filtered_no_filters_returns_all_entries_newest_first() -> None:
    log = InMemoryAuditLog()
    await log.append(
        AuditEntry(
            ticket_id="1",
            session_id="s1",
            actor="alice",
            from_state="open",
            to_state="pending",
            at="2026-08-01T10:00:00Z",
            remark="",
        )
    )
    await log.append(
        AuditEntry(
            ticket_id="2",
            session_id="s2",
            actor="bob",
            from_state="open",
            to_state="pending",
            at="2026-08-01T11:00:00Z",
            remark="",
        )
    )
    results = await log.list_filtered()
    assert [r.actor for r in results] == ["bob", "alice"]


@pytest.mark.asyncio
async def test_list_filtered_by_actor() -> None:
    log = InMemoryAuditLog()
    await log.append(
        AuditEntry(
            ticket_id="1",
            session_id="s1",
            actor="alice",
            from_state="open",
            to_state="pending",
            at="2026-08-01T10:00:00Z",
            remark="",
        )
    )
    await log.append(
        AuditEntry(
            ticket_id="2",
            session_id="s2",
            actor="bob",
            from_state="open",
            to_state="pending",
            at="2026-08-01T11:00:00Z",
            remark="",
        )
    )
    results = await log.list_filtered(actor="alice")
    assert [r.ticket_id for r in results] == ["1"]


@pytest.mark.asyncio
async def test_list_filtered_by_date_range() -> None:
    log = InMemoryAuditLog()
    await log.append(
        AuditEntry(
            ticket_id="1",
            session_id="s1",
            actor="alice",
            from_state="open",
            to_state="pending",
            at="2026-08-01T10:00:00Z",
            remark="",
        )
    )
    await log.append(
        AuditEntry(
            ticket_id="2",
            session_id="s2",
            actor="bob",
            from_state="open",
            to_state="pending",
            at="2026-08-02T11:00:00Z",
            remark="",
        )
    )
    results = await log.list_filtered(from_ts="2026-08-02T00:00:00Z")
    assert [r.ticket_id for r in results] == ["2"]


@pytest.mark.asyncio
async def test_list_filtered_respects_limit() -> None:
    log = InMemoryAuditLog()
    for i in range(5):
        await log.append(
            AuditEntry(
                ticket_id=str(i),
                session_id=f"s{i}",
                actor="alice",
                from_state="open",
                to_state="pending",
                at=f"2026-08-01T1{i}:00:00Z",
                remark="",
            )
        )
    results = await log.list_filtered(limit=2)
    assert len(results) == 2
