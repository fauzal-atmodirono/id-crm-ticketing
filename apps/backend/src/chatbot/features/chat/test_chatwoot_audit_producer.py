"""Tests for the Chatwoot status-audit-log producer + first-response marker.

conversation_status_changed webhooks append an AuditEntry with the WIP-aware
from/to mapping; an outgoing (non-private) agent message records a FIRST_RESPONSE
marker (deduped).
"""

from __future__ import annotations

import asyncio
from typing import Any

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
from chatbot.features.chat.sla import FIRST_RESPONSE_STATE
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeRunner:
    async def run_async(self, **_: Any) -> None:
        return None


@pytest.fixture
def audit_setup() -> tuple[TestClient, InMemoryAuditLog]:
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


def test_status_changed_to_pending_logs_wip_transition(
    audit_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, audit = audit_setup
    # Chatwoot sends conversation-level status events FLAT (id/status top-level).
    res = client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_status_changed", "id": 42, "status": "pending"},
    )
    assert res.status_code == 200
    # A status change to a non-resolved state is logged but does not resume the AI.
    assert res.json() == {"status": "logged"}

    entries = asyncio.run(audit.list_for_ticket("42"))
    assert len(entries) == 1
    assert entries[0].from_state == ""  # first transition: prior unknown
    assert entries[0].to_state == "WIP"  # pending -> WIP
    assert entries[0].ticket_id == "42"
    assert entries[0].session_id == "chatwoot-conv-42"


def test_status_changed_snoozed_maps_to_wip(
    audit_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, audit = audit_setup
    client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_status_changed", "id": 43, "status": "snoozed"},
    )
    entries = asyncio.run(audit.list_for_ticket("43"))
    assert entries[-1].to_state == "WIP"


def test_status_changed_to_resolved_logs_solved(
    audit_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, audit = audit_setup
    res = client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_status_changed", "id": 44, "status": "resolved"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "resolved"}
    entries = asyncio.run(audit.list_for_ticket("44"))
    assert entries[-1].to_state == "SOLVED"


def test_from_state_chains_across_transitions(
    audit_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, audit = audit_setup
    client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_status_changed", "id": 45, "status": "open"},
    )
    client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_status_changed", "id": 45, "status": "pending"},
    )
    entries = asyncio.run(audit.list_for_ticket("45"))
    assert len(entries) == 2
    assert entries[0].to_state == "OPEN"
    # Second transition's from_state chains off the first's to_state.
    assert entries[1].from_state == "OPEN"
    assert entries[1].to_state == "WIP"


def test_outgoing_agent_reply_records_first_response_once(
    audit_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, audit = audit_setup
    reply = {
        "event": "message_created",
        "message_type": "outgoing",
        "private": False,
        "content": "Hi, an agent here.",
        "conversation": {"id": 46},
        "sender": {"id": 7, "name": "Agent Sam"},
    }
    client.post("/webhooks/chatwoot", json=reply)
    client.post("/webhooks/chatwoot", json=reply)  # second reply must NOT duplicate

    entries = asyncio.run(audit.list_for_ticket("46"))
    first = [e for e in entries if e.to_state == FIRST_RESPONSE_STATE]
    assert len(first) == 1


def test_private_note_does_not_record_first_response(
    audit_setup: tuple[TestClient, InMemoryAuditLog],
) -> None:
    client, audit = audit_setup
    client.post(
        "/webhooks/chatwoot",
        json={
            "event": "message_created",
            "message_type": "outgoing",
            "private": True,  # internal note, not a customer-facing reply
            "content": "internal",
            "conversation": {"id": 47},
        },
    )
    entries = asyncio.run(audit.list_for_ticket("47"))
    assert entries == []
