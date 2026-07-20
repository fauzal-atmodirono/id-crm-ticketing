"""Tests for GET /cases/{ticket_id}/audit."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeRunner:
    async def run_async(self, **_: object) -> None:
        return None


@pytest.fixture
def audit_endpoint_setup() -> tuple[TestClient, InMemoryAuditLog]:
    settings = get_settings()

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )
    audit_log = InMemoryAuditLog()

    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator, audit_log=audit_log))
    return TestClient(app), audit_log


def test_case_audit_returns_entries_for_ticket(
    audit_endpoint_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, audit_log = audit_endpoint_setup

    # Add two audit entries to the log
    entry1 = AuditEntry(
        ticket_id="TKT-123",
        session_id="whatsapp-+60123",
        actor="customer",
        from_state="OPEN",
        to_state="PENDING",
        at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC).isoformat(),
        remark="Customer escalated issue",
    )
    entry2 = AuditEntry(
        ticket_id="TKT-123",
        session_id="whatsapp-+60123",
        actor="sla-automation",
        from_state="PENDING",
        to_state="WIP",
        at=datetime(2026, 7, 1, 10, 30, 0, tzinfo=UTC).isoformat(),
        remark="SLA escalate",
    )

    asyncio.run(audit_log.append(entry1))
    asyncio.run(audit_log.append(entry2))

    # GET /cases/TKT-123/audit
    res = client.get("/cases/TKT-123/audit")

    assert res.status_code == 200
    data = res.json()
    assert data["ticket_id"] == "TKT-123"
    assert len(data["audit"]) == 2
    # Check first entry
    assert data["audit"][0]["ticket_id"] == "TKT-123"
    assert data["audit"][0]["actor"] == "customer"
    assert data["audit"][0]["from_state"] == "OPEN"
    assert data["audit"][0]["to_state"] == "PENDING"
    assert data["audit"][0]["remark"] == "Customer escalated issue"
    # Check second entry
    assert data["audit"][1]["ticket_id"] == "TKT-123"
    assert data["audit"][1]["actor"] == "sla-automation"
    assert data["audit"][1]["from_state"] == "PENDING"
    assert data["audit"][1]["to_state"] == "WIP"
    assert data["audit"][1]["remark"] == "SLA escalate"


def test_case_audit_returns_empty_list_when_no_entries(
    audit_endpoint_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, _audit_log = audit_endpoint_setup

    res = client.get("/cases/TKT-999/audit")

    assert res.status_code == 200
    data = res.json()
    assert data["ticket_id"] == "TKT-999"
    assert data["audit"] == []


def test_case_audit_returns_503_when_not_configured() -> None:
    """When audit_log is None, endpoint should return 503."""
    settings = get_settings()

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )

    app = create_app(settings)
    # Don't pass audit_log, so it defaults to None
    app.include_router(build_chat_router(orchestrator))
    client = TestClient(app)

    res = client.get("/cases/TKT-123/audit")

    assert res.status_code == 503
    assert "not configured" in res.json()["detail"]
