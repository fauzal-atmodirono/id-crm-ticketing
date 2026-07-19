from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.service import RoutingService
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.platform.config import Settings


def _make_fetcher(agents: list[AgentRecord]) -> PresenceFetcher:
    fetcher = PresenceFetcher(Settings())

    async def _fetch() -> list[AgentRecord]:
        return agents

    fetcher.fetch_agents = _fetch  # type: ignore[method-assign]
    return fetcher


def _make_store(priorities: list[AgentPriority]) -> ChannelPriorityStore:
    store = ChannelPriorityStore(Settings())

    async def _list_all() -> list[AgentPriority]:
        return priorities

    store.list_all = _list_all  # type: ignore[method-assign]
    return store


def _svc(
    agents: list[AgentRecord], priorities: list[AgentPriority]
) -> RoutingService:
    return RoutingService(
        presence=_make_fetcher(agents),
        store=_make_store(priorities),
    )


@pytest.mark.asyncio
async def test_picks_tier1_priority_match_online() -> None:
    """Agent whose first-priority channel matches and is online is picked."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 1  # Alice: WhatsApp is first-priority


@pytest.mark.asyncio
async def test_skips_busy_tier1_falls_to_tier2() -> None:
    """When tier-1 agents (first-priority match) are all busy, pick a tier-2 agent."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),   # tier-1 but busy
        AgentRecord(id=2, name="Bob", availability_status="online"),   # tier-2 (second priority)
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 2  # Bob: WhatsApp at position 1, but online


@pytest.mark.asyncio
async def test_fallback_to_unprioritised_idle_agent() -> None:
    """When all prioritised agents are busy, pick an online agent with no priority config (tier 3)."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=3, name="Carol", availability_status="online"),  # no priority record
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 3  # Carol: online, no priority config → idle fallback


@pytest.mark.asyncio
async def test_returns_none_when_all_busy_or_offline() -> None:
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=2, name="Bob", availability_status="offline"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]),
        AgentPriority(agent_id=2, channel_priorities=["WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result is None


@pytest.mark.asyncio
async def test_no_priorities_configured_picks_any_online() -> None:
    """When no priorities are stored at all, fall back to any online agent."""
    agents = [
        AgentRecord(id=5, name="Eve", availability_status="online"),
        AgentRecord(id=6, name="Frank", availability_status="offline"),
    ]
    result = await _svc(agents, []).pick_agent("email")
    assert result == 5


@pytest.mark.asyncio
async def test_channel_match_is_case_insensitive() -> None:
    agents = [AgentRecord(id=1, name="Alice", availability_status="online")]
    priorities = [AgentPriority(agent_id=1, channel_priorities=["whatsapp"])]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 1
