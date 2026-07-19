from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.routing.service import RoutingService
from chatbot.platform.config import Settings


class _FakeClient:
    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._responses = responses

    async def _request(
        self, method: str, path: str, payload: Any = None
    ) -> Any:
        self.calls.append((method, path, payload))
        for (m, sub), resp in self._responses.items():
            if m == method and sub in path:
                return resp
        return {}


def _adapter(
    fake: _FakeClient,
    *,
    routing_enabled: bool = False,
    routing_svc: RoutingService | None = None,
    team_id: int = 3,
) -> ChatwootAdapter:
    a = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_agent_team_id=team_id,
            routing_enabled=routing_enabled,
        )
    )
    a._request = fake._request  # type: ignore[method-assign]
    if routing_svc is not None:
        a._routing_service = routing_svc  # type: ignore[attr-defined]
    return a


def _make_routing_svc(agent_id: int | None) -> RoutingService:
    svc = RoutingService.__new__(RoutingService)

    async def pick(channel: str) -> int | None:
        return agent_id

    svc.pick_agent = pick  # type: ignore[method-assign]
    return svc


@pytest.mark.asyncio
async def test_routing_disabled_uses_team_assignment() -> None:
    """When routing_enabled=False, the existing team assignment is used."""
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = _adapter(fake, routing_enabled=False, team_id=5)
    await adapter.create_ticket(
        session_id="s1", title="T", body="B", urgency="medium"
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"team_id": 5}


@pytest.mark.asyncio
async def test_routing_enabled_uses_agent_assignment() -> None:
    """When routing picks an agent, assignment uses assignee_id not team_id."""
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    svc = _make_routing_svc(agent_id=42)
    adapter = _adapter(fake, routing_enabled=True, routing_svc=svc, team_id=5)
    await adapter.create_ticket(
        session_id="s2", title="T", body="B", urgency="medium"
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"assignee_id": 42}


@pytest.mark.asyncio
async def test_routing_returns_none_falls_back_to_team() -> None:
    """When routing picks no agent (all busy), fall back to team assignment."""
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    svc = _make_routing_svc(agent_id=None)
    adapter = _adapter(fake, routing_enabled=True, routing_svc=svc, team_id=5)
    await adapter.create_ticket(
        session_id="s3", title="T", body="B", urgency="medium"
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"team_id": 5}


@pytest.mark.asyncio
async def test_open_handoff_uses_routing_when_enabled() -> None:
    """open_handoff also routes via RoutingService when enabled."""
    from chatbot.features.chat.models import HandoffOpenPayload

    fake = _FakeClient({("POST", "/conversations"): {"id": 77}})
    svc = _make_routing_svc(agent_id=9)
    adapter = _adapter(fake, routing_enabled=True, routing_svc=svc, team_id=5)
    await adapter.open_handoff(
        HandoffOpenPayload(
            session_id="s4",
            customer_name="Test User",
            customer_email="test@example.com",
            ai_summary="help",
            transcript=(),
            urgency="low",
            reason="help_request",
            language="en",
            category=None,
            subcategory=None,
            division=None,
            department=None,
            sla_minutes=None,
            customer_phone=None,
        )
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"assignee_id": 9}


@pytest.mark.asyncio
async def test_open_handoff_pic_team_preserved_when_routing_disabled() -> None:
    """Phase 2 regression: when routing_enabled=False, PIC-derived team_id is used
    (not the global chatwoot_agent_team_id) so department→PIC team routing is
    preserved.
    """
    from chatbot.features.chat.models import HandoffOpenPayload

    fake = _FakeClient({("POST", "/conversations"): {"id": 88}})
    adapter = _adapter(fake, routing_enabled=False, team_id=3)  # global team=3

    # Stub pic_registry: lookup("sales") returns an object with chatwoot_team_id=99
    # pic_name is required by _pic_label which also reads the pic object.
    pic_stub = types.SimpleNamespace(chatwoot_team_id=99, pic_name="Sales PIC")
    registry_stub = types.SimpleNamespace(lookup=lambda dept: pic_stub)
    adapter._pic_registry = registry_stub  # type: ignore[attr-defined]

    await adapter.open_handoff(
        HandoffOpenPayload(
            session_id="s5",
            customer_name="Test User",
            customer_email="test@example.com",
            ai_summary="complaint",
            transcript=(),
            urgency="medium",
            reason="help_request",
            language="en",
            category=None,
            subcategory=None,
            division=None,
            department="dept_sales",
            sla_minutes=None,
            customer_phone=None,
        )
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    # Must be the PIC team (99), NOT the global team (3)
    assert payload == {"team_id": 99}
