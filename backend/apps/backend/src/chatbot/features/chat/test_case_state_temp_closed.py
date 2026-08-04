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


class _FakeTicketing:
    """Stateful enough to matter: a status-change webhook fires on EVERY
    Chatwoot status transition -- by far the most common trigger of any
    custom-attributes writer in this codebase (Package C Task 5 review
    round 2, Critical 1). Tracks each conversation's custom attributes
    across calls, exactly like ChatwootAdapter's real GET/POST semantics
    (POST /custom_attributes REPLACES the whole object; GET /conversations/
    {id} returns whatever was last POSTed), so a bare-assign write here is
    provably caught rather than assumed fixed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._custom_attrs: dict[str, dict[str, Any]] = {}

    async def _request(self, method: str, path: str, payload: Any = None) -> dict:
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/custom_attributes"):
            conv_id = path.split("/")[-2]
            self._custom_attrs[conv_id] = dict((payload or {}).get("custom_attributes", {}))
            return {}
        if (
            method == "GET"
            and path.startswith("/conversations/")
            and path.rsplit("/", 1)[-1].isdigit()
        ):
            conv_id = path.rsplit("/", 1)[-1]
            return {"custom_attributes": dict(self._custom_attrs.get(conv_id, {}))}
        return {}

    async def _merge_custom_attributes(self, ticket_id: str, attributes: dict[str, Any]) -> None:
        """Mirrors ChatwootAdapter._merge_custom_attributes exactly (GET,
        union, POST) but routed through THIS fake's own _request, so it
        shares state with any other write against the SAME conversation --
        the point of this fake existing at all."""
        res = await self._request("GET", f"/conversations/{ticket_id}")
        current = res.get("custom_attributes") if isinstance(res, dict) else None
        merged = {**(current or {}), **attributes}
        await self._request(
            "POST", f"/conversations/{ticket_id}/custom_attributes", {"custom_attributes": merged}
        )


async def test_router_status_transition_writes_case_state_attribute() -> None:
    """_log_status_transition writes case_state to Chatwoot custom_attributes."""
    ticketing = _FakeTicketing()

    class _FakeOrchestrator:
        _settings = Settings(_env_file=None, chatwoot_webhook_secret="")
        _ticketing_port = ticketing

    audit = InMemoryAuditLog()
    router = ChatRouter.__new__(ChatRouter)
    router.orchestrator = _FakeOrchestrator()  # type: ignore[assignment]
    router._audit_log = audit
    router._handoff_bridge = None
    router._human_agent_bridge = None
    router._twilio_adapter = None

    payload = ChatwootWebhookPayload.model_validate(
        {
            "event": "conversation_status_changed",
            "message_type": "outgoing",
            "private": False,
            "content": None,
            "conversation": {"id": 55, "status": "pending", "inbox_id": 0},
            "sender": {"name": "Agen1", "email": None},
        }
    )

    await router._log_status_transition("55", payload)

    attr_calls = [pl for _, p, pl in ticketing.calls if p.endswith("/custom_attributes")]
    assert len(attr_calls) == 1
    assert attr_calls[0]["custom_attributes"][CHATWOOT_CASE_STATE_ATTR] == "WIP"


async def test_router_status_transition_does_not_clobber_existing_attributes() -> None:
    """Package C Task 5 review round 2, Critical 1: this webhook fires on
    EVERY status change -- e.g. every time a conversation is resolved --
    so a bare-assign write here was silently erasing case_category/
    recording_url/external_id/vehicle_model on every single resolve. Seeds
    a pre-existing attribute (as if a recorded call or classified case had
    already written it), runs the REAL status-transition write, and
    asserts it survives alongside the new case_state."""
    ticketing = _FakeTicketing()
    ticketing._custom_attrs["55"] = {
        "case_category": "Aftersales",
        "recording_url": "https://x/RE1",
    }

    class _FakeOrchestrator:
        _settings = Settings(_env_file=None, chatwoot_webhook_secret="")
        _ticketing_port = ticketing

    router = ChatRouter.__new__(ChatRouter)
    router.orchestrator = _FakeOrchestrator()  # type: ignore[assignment]
    router._audit_log = InMemoryAuditLog()
    router._handoff_bridge = None
    router._human_agent_bridge = None
    router._twilio_adapter = None

    payload = ChatwootWebhookPayload.model_validate(
        {
            "event": "conversation_status_changed",
            "message_type": "outgoing",
            "private": False,
            "content": None,
            "conversation": {"id": 55, "status": "resolved", "inbox_id": 0},
            "sender": {"name": "Agen1", "email": None},
        }
    )

    await router._log_status_transition("55", payload)

    final = ticketing._custom_attrs["55"]
    assert final["case_category"] == "Aftersales"  # survived
    assert final["recording_url"] == "https://x/RE1"  # survived
    assert final[CHATWOOT_CASE_STATE_ATTR] == "SOLVED"  # newly written
