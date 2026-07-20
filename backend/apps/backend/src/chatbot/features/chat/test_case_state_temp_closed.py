"""Tests for TEMP_CLOSED state + case_state custom-attribute propagation."""
from __future__ import annotations

from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.case_state import (
    CHATWOOT_CASE_STATE_ATTR,
    CaseState,
    record_transition,
)
from chatbot.features.chat.router import ChatRouter
from chatbot.features.chat.schemas import ChatwootWebhookPayload
from chatbot.platform.config import Settings


def test_temp_closed_is_in_case_state() -> None:
    assert CaseState.TEMP_CLOSED == "TEMP_CLOSED"


def test_chatwoot_case_state_attr_constant() -> None:
    assert CHATWOOT_CASE_STATE_ATTR == "case_state"


async def test_record_transition_stores_temp_closed() -> None:
    audit = InMemoryAuditLog()
    await record_transition(
        audit,
        ticket_id="99",
        session_id="chatwoot-conv-99",
        actor="agent",
        from_state=CaseState.WIP,
        to_state=CaseState.TEMP_CLOSED,
        at="2026-07-18T10:00:00+00:00",
        remark="temporarily closed pending parts",
    )
    entries = await audit.list_for_ticket("99")
    assert len(entries) == 1
    assert entries[0].to_state == "TEMP_CLOSED"


async def test_router_status_transition_writes_case_state_attribute() -> None:
    """_log_status_transition writes case_state to Chatwoot custom_attributes."""
    cw_calls: list[tuple[str, str, Any]] = []

    class _FakeTicketing:
        async def _request(self, method: str, path: str, payload: Any = None) -> dict:
            cw_calls.append((method, path, payload))
            return {}

    class _FakeOrchestrator:
        _settings = Settings(_env_file=None, chatwoot_webhook_secret="")
        _ticketing_port = _FakeTicketing()

    audit = InMemoryAuditLog()
    router = ChatRouter.__new__(ChatRouter)
    router.orchestrator = _FakeOrchestrator()  # type: ignore[assignment]
    router._audit_log = audit
    router._handoff_bridge = None
    router._human_agent_bridge = None
    router._twilio_adapter = None

    payload = ChatwootWebhookPayload.model_validate({
        "event": "conversation_status_changed",
        "message_type": "outgoing",
        "private": False,
        "content": None,
        "conversation": {"id": 55, "status": "pending", "inbox_id": 0},
        "sender": {"name": "Agen1", "email": None},
    })

    await router._log_status_transition("55", payload)

    attr_calls = [pl for _, p, pl in cw_calls if "/custom_attributes" in p]
    assert len(attr_calls) == 1
    assert attr_calls[0]["custom_attributes"][CHATWOOT_CASE_STATE_ATTR] == "WIP"
