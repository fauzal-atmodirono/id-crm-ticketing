"""P1 task 6 — acknowledgement as a signal distinct from first agent reply.

Appendix B asks for two different things: acknowledge the customer within
minutes (B-WA-14) and *update* them within working hours (B-EM-05). Today the
engine has one signal — `first_reply_created_at` — so those two requirements
are indistinguishable, and a case that was acknowledged still reads as
un-answered.

This adds an explicit `ACKNOWLEDGED` audit state and makes the ack breach read
it. The update/resolution breach deliberately keeps reading its own signal: an
acknowledgement buys time on the ack clock, never on the resolution clock.

With `sla_acknowledgement_enabled` off, every path here reproduces today's
behaviour exactly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.sla import (
    ACKNOWLEDGED_STATE,
    FIRST_RESPONSE_STATE,
    NO_RESPONSE_BREACH,
    UNRESOLVED_BREACH,
    scan_conversations,
)
from chatbot.platform.config import Settings

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
# Older than both the 2h response threshold and the 48h resolution threshold,
# so a single fixture can show the two breaches moving independently.
THREE_DAYS_AGO = NOW - timedelta(days=3)


def _settings(**overrides: Any) -> Settings:
    # sla_acknowledgement_enabled is pinned, not left to the default: these
    # tests assert both sides of that flag, and an env var set in the shell
    # would otherwise decide the answer for them.
    base: dict[str, Any] = {
        "sla_response_hours": 2,
        "sla_resolution_hours": 48,
        "sla_scan_interval_minutes": 15,
        "chatwoot_inbox_id": 1,
        "sla_acknowledgement_enabled": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _conv(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "created_at": int(THREE_DAYS_AGO.timestamp()),
        "status": "open",
        "inbox_id": 7,
    }
    base.update(fields)
    return base


async def _scan(*, convs, settings, audit=None):
    log = audit or InMemoryAuditLog()
    fired = await scan_conversations(settings, log, now=NOW, fetch=lambda _s: convs)
    return {e.to_state for e in fired}, log


async def _with_ack(ticket_id: str = "1") -> InMemoryAuditLog:
    audit = InMemoryAuditLog()
    await audit.append(
        AuditEntry(
            ticket_id=ticket_id,
            session_id=f"chatwoot-conv-{ticket_id}",
            actor="escalation-reply",
            from_state="OPEN",
            to_state=ACKNOWLEDGED_STATE,
            at=NOW.isoformat(),
            remark="PIC replied by email",
        )
    )
    return audit


# --- the scan side --------------------------------------------------------


async def test_an_explicit_acknowledgement_is_recorded_without_an_agent_reply():
    audit = await _with_ack()
    states, _ = await _scan(
        convs=[_conv()],
        settings=_settings(sla_acknowledgement_enabled=True),
        audit=audit,
    )
    assert NO_RESPONSE_BREACH not in states


async def test_an_agent_reply_still_counts_as_acknowledgement_when_the_flag_is_off():
    states, _ = await _scan(
        convs=[_conv(first_reply_created_at=int(NOW.timestamp()))],
        settings=_settings(),
    )
    assert NO_RESPONSE_BREACH not in states


async def test_an_acknowledgement_is_ignored_when_the_flag_is_off():
    """Ship-dark guarantee: the new state changes nothing until opted in."""
    audit = await _with_ack()
    states, _ = await _scan(convs=[_conv()], settings=_settings(), audit=audit)
    assert NO_RESPONSE_BREACH in states


async def test_an_agent_reply_still_counts_when_the_flag_is_on():
    states, _ = await _scan(
        convs=[_conv(first_reply_created_at=int(NOW.timestamp()))],
        settings=_settings(sla_acknowledgement_enabled=True),
    )
    assert NO_RESPONSE_BREACH not in states


async def test_the_ack_breach_reads_acknowledgement_and_the_update_breach_reads_first_reply():
    """The requirement. Acknowledging stops the ack clock and nothing else —
    the case is still unresolved and must still breach on resolution."""
    audit = await _with_ack()
    states, _ = await _scan(
        convs=[_conv()],
        settings=_settings(sla_acknowledgement_enabled=True),
        audit=audit,
    )
    assert NO_RESPONSE_BREACH not in states
    assert UNRESOLVED_BREACH in states


async def test_acknowledged_and_first_response_are_independent_states():
    audit = await _with_ack()
    recorded = {e.to_state for e in await audit.list_for_ticket("1")}
    assert ACKNOWLEDGED_STATE in recorded
    assert FIRST_RESPONSE_STATE not in recorded


# --- the endpoint the reply linker calls ----------------------------------


class _Settings:
    proton_backend_key = "secret"


def _entries(audit, ticket_id: str):
    """Read the audit rows from a sync test. ``InMemoryAuditLog`` holds no loop
    state, so a throwaway loop is safe here and keeps these tests sync — the
    TestClient drives its own portal and deadlocks if called from an async
    test."""
    return asyncio.run(audit.list_for_ticket(ticket_id))


def _client(audit):
    app = FastAPI()
    app.include_router(
        build_escalation_router(
            notifier=None,
            chatwoot_request=None,
            settings=_Settings(),
            audit=audit,
        )
    )
    return TestClient(app)


def test_a_pic_email_reply_linked_by_the_reply_linker_records_an_acknowledgement():
    audit = InMemoryAuditLog()
    res = _client(audit).post(
        "/escalation/acknowledge",
        headers={"x-api-key": "secret"},
        json={"conversation_id": "42", "actor": "pic@test", "remark": "replied by email"},
    )
    assert res.status_code == 200

    entries = _entries(audit, "42")
    assert [e.to_state for e in entries] == [ACKNOWLEDGED_STATE]
    assert entries[0].actor == "pic@test"


def test_a_second_acknowledgement_does_not_append_a_duplicate_entry():
    audit = InMemoryAuditLog()
    client = _client(audit)
    body = {"conversation_id": "42", "actor": "pic@test"}
    assert client.post(
        "/escalation/acknowledge", headers={"x-api-key": "secret"}, json=body
    ).status_code == 200
    assert client.post(
        "/escalation/acknowledge", headers={"x-api-key": "secret"}, json=body
    ).status_code == 200

    assert len(_entries(audit, "42")) == 1


def test_the_acknowledge_endpoint_requires_the_api_key():
    assert _client(InMemoryAuditLog()).post(
        "/escalation/acknowledge", json={"conversation_id": "42"}
    ).status_code == 401


def test_the_acknowledge_endpoint_is_absent_when_no_audit_log_is_wired():
    """Fail-safe: an unwired audit log must 404, not 500 on every call."""
    app = FastAPI()
    app.include_router(
        build_escalation_router(
            notifier=None, chatwoot_request=None, settings=_Settings()
        )
    )
    res = TestClient(app).post(
        "/escalation/acknowledge",
        headers={"x-api-key": "secret"},
        json={"conversation_id": "42"},
    )
    assert res.status_code == 404
