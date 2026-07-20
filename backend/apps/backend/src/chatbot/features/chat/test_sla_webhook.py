"""Tests for POST /webhooks/zendesk-sla-escalation."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeRunner:
    async def run_async(self, **_: Any) -> None:
        return None


@pytest.fixture
def sla_setup(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, AsyncMock, InMemoryAuditLog]:
    settings = get_settings()
    # setattr (not direct assignment) so the mutation to the @cache'd singleton
    # auto-reverts after the test — otherwise the secret leaks session-wide.
    monkeypatch.setattr(settings, "sla_webhook_secret", "sla-secret-123")

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )
    twilio = AsyncMock()
    audit_log = InMemoryAuditLog()

    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator, twilio_adapter=twilio, audit_log=audit_log))
    return TestClient(app), twilio, audit_log


def test_sla_escalation_records_audit_and_sends_wa(
    sla_setup: tuple[TestClient, AsyncMock, InMemoryAuditLog],
) -> None:
    client, twilio, audit_log = sla_setup

    res = client.post(
        "/webhooks/zendesk-sla-escalation",
        json={
            "ticket_id": "TKT-999",
            "session_id": "whatsapp-+60123",
            "level": "escalate",
            "pic_whatsapp": "+60123",
            "remark": "",
        },
        headers={"x-proton-webhook-secret": "sla-secret-123"},
    )

    assert res.status_code == 200
    assert res.json() == {"status": "recorded"}

    # Audit entry should exist with to_state == "WIP"
    entries = asyncio.run(audit_log.list_for_ticket("TKT-999"))
    assert len(entries) == 1
    assert entries[0].to_state == "WIP"
    assert entries[0].actor == "sla-automation"
    assert entries[0].ticket_id == "TKT-999"

    # WA alert should be sent to whatsapp:+60123
    twilio.send_message.assert_awaited_once()
    call_kwargs = twilio.send_message.await_args.kwargs
    assert call_kwargs["conversation_id"] == "whatsapp:+60123"


def test_sla_escalation_alarm_level_records_pending(
    sla_setup: tuple[TestClient, AsyncMock, InMemoryAuditLog],
) -> None:
    client, twilio, audit_log = sla_setup

    res = client.post(
        "/webhooks/zendesk-sla-escalation",
        json={
            "ticket_id": "TKT-888",
            "session_id": "",
            "level": "alarm",
            "pic_whatsapp": "",
            "remark": "first alarm",
        },
        headers={"x-proton-webhook-secret": "sla-secret-123"},
    )

    assert res.status_code == 200
    assert res.json() == {"status": "recorded"}

    entries = asyncio.run(audit_log.list_for_ticket("TKT-888"))
    assert len(entries) == 1
    assert entries[0].to_state == "PENDING"
    # No WA alert when pic_whatsapp is empty
    twilio.send_message.assert_not_awaited()


def test_sla_escalation_rejects_wrong_secret(
    sla_setup: tuple[TestClient, AsyncMock, InMemoryAuditLog],
) -> None:
    client, _twilio, _audit_log = sla_setup

    res = client.post(
        "/webhooks/zendesk-sla-escalation",
        json={"ticket_id": "TKT-777", "level": "escalate"},
        headers={"x-proton-webhook-secret": "WRONG"},
    )

    assert res.status_code == 401


def test_sla_escalation_rejects_missing_secret(
    sla_setup: tuple[TestClient, AsyncMock, InMemoryAuditLog],
) -> None:
    client, _twilio, _audit_log = sla_setup

    res = client.post(
        "/webhooks/zendesk-sla-escalation",
        json={"ticket_id": "TKT-666", "level": "escalate"},
    )

    assert res.status_code == 401
