from __future__ import annotations

import pytest

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.service import RoutingService
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.platform.config import Settings


def _make_fetcher(
    agents: list[AgentRecord], open_counts: dict[int, int] | None = None
) -> PresenceFetcher:
    fetcher = PresenceFetcher(Settings())

    async def _fetch() -> list[AgentRecord]:
        return agents

    async def _fetch_open_counts() -> dict[int, int]:
        return open_counts or {}

    fetcher.fetch_agents = _fetch  # type: ignore[method-assign]
    fetcher.fetch_agent_open_counts = _fetch_open_counts  # type: ignore[method-assign]
    return fetcher


def _make_store(priorities: list[AgentPriority]) -> ChannelPriorityStore:
    store = ChannelPriorityStore(Settings())

    async def _list_all() -> list[AgentPriority]:
        return priorities

    store.list_all = _list_all  # type: ignore[method-assign]
    return store


def _svc(
    agents: list[AgentRecord],
    priorities: list[AgentPriority],
    open_counts: dict[int, int] | None = None,
    max_concurrent: int = 0,
) -> RoutingService:
    return RoutingService(
        presence=_make_fetcher(agents, open_counts),
        store=_make_store(priorities),
        settings=Settings(routing_max_concurrent_per_agent=max_concurrent),
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


# --- Concurrent-conversation cap (routing_max_concurrent_per_agent) ---


@pytest.mark.asyncio
async def test_cap_skips_tier1_agent_at_or_over_cap() -> None:
    """An agent who would otherwise win tier 1 is excluded once at/over the cap."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    # Alice would normally win tier 1, but she's at the cap (2 open >= cap 2).
    result = await _svc(
        agents, priorities, open_counts={1: 2}, max_concurrent=2
    ).pick_agent("WhatsApp")
    assert result == 2  # Bob: tier-2 match, under the cap (0 open < 2)


@pytest.mark.asyncio
async def test_cap_allows_agent_under_cap() -> None:
    """An agent whose open count is below the cap still wins tier 1."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    result = await _svc(
        agents, priorities, open_counts={1: 1}, max_concurrent=2
    ).pick_agent("WhatsApp")
    assert result == 1  # Alice: 1 open < cap of 2, still wins tier 1


@pytest.mark.asyncio
async def test_cap_disabled_by_default_matches_prior_behavior() -> None:
    """routing_max_concurrent_per_agent=0 (default) is unlimited: byte-identical to before.

    Re-runs the exact assertions of every pre-existing pick_agent test with the
    default (unset) cap, confirming this change is a no-op when the cap is off.
    """
    # test_picks_tier1_priority_match_online
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    assert await _svc(agents, priorities).pick_agent("WhatsApp") == 1

    # test_skips_busy_tier1_falls_to_tier2
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    assert await _svc(agents, priorities).pick_agent("WhatsApp") == 2

    # test_fallback_to_unprioritised_idle_agent
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=3, name="Carol", availability_status="online"),
    ]
    priorities2 = [AgentPriority(agent_id=1, channel_priorities=["WhatsApp"])]
    assert await _svc(agents, priorities2).pick_agent("WhatsApp") == 3

    # test_returns_none_when_all_busy_or_offline
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=2, name="Bob", availability_status="offline"),
    ]
    priorities3 = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]),
        AgentPriority(agent_id=2, channel_priorities=["WhatsApp"]),
    ]
    assert await _svc(agents, priorities3).pick_agent("WhatsApp") is None

    # test_no_priorities_configured_picks_any_online
    agents = [
        AgentRecord(id=5, name="Eve", availability_status="online"),
        AgentRecord(id=6, name="Frank", availability_status="offline"),
    ]
    assert await _svc(agents, []).pick_agent("email") == 5

    # test_channel_match_is_case_insensitive
    agents = [AgentRecord(id=1, name="Alice", availability_status="online")]
    priorities4 = [AgentPriority(agent_id=1, channel_priorities=["whatsapp"])]
    assert await _svc(agents, priorities4).pick_agent("WhatsApp") == 1
