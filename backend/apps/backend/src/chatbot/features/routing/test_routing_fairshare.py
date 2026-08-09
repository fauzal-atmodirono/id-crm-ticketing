"""P6 task 6 -- fair-share selection within a routing tier.

Requirement 4.16: rotate assignments among equally-eligible agents instead of
always picking the same first match. `RoutingService.pick_agent` already
fetches `fetch_agent_open_counts` when the concurrency cap is on; this
package turns that load data into the *chooser* (fewest open conversations,
tied agents broken by least-recently-assigned) rather than only a ceiling --
but only within whichever tier channel-priority already selected, and only
when `settings.routing_fair_share_enabled` is on. Flag off must be
byte-identical to the pre-existing first-match behavior; see
`test_the_flag_off_reproduces_todays_first_match_selection` and
`test_every_existing_three_tier_routing_test_still_passes`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from chatbot.features.routing.custom_status import CustomStatus
from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.presence_store import PresenceEvent
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


@dataclass
class _FakeCustomStatusStore:
    """Stands in for `CustomStatusStore`. `outage=True` simulates a Firestore
    failure: `get()` returns `None` regardless of `statuses`, matching
    `CustomStatusStore.get`'s fail-open contract (unknown key AND outage both
    collapse to `None`).
    """

    statuses: dict[str, CustomStatus] = field(default_factory=dict)
    outage: bool = False

    async def get(self, key: str) -> CustomStatus | None:
        if self.outage:
            return None
        return self.statuses.get(key)


@dataclass
class _FakePresenceEventStore:
    """Stands in for `PresenceEventStore`, just the `latest` read `service.py` needs."""

    latest_by_agent: dict[int, PresenceEvent] = field(default_factory=dict)
    calls: list[int] = field(default_factory=list)
    """Every `agent_id` `latest()` was actually called with, in order --
    lets a test assert *which* agents' presence was looked up, not just the
    final pick, so a regression back to "check every online agent up
    front" (instead of lazily, tier by tier) would be caught."""

    async def latest(self, agent_id: int) -> PresenceEvent | None:
        self.calls.append(agent_id)
        return self.latest_by_agent.get(agent_id)


def _svc(
    agents: list[AgentRecord],
    priorities: list[AgentPriority],
    open_counts: dict[int, int] | None = None,
    max_concurrent: int = 0,
    fair_share: bool = False,
    custom_status_store: _FakeCustomStatusStore | None = None,
    presence_store: _FakePresenceEventStore | None = None,
) -> RoutingService:
    return RoutingService(
        presence=_make_fetcher(agents, open_counts),
        store=_make_store(priorities),
        settings=Settings(
            routing_max_concurrent_per_agent=max_concurrent,
            routing_fair_share_enabled=fair_share,
        ),
        custom_status_store=custom_status_store,
        presence_store=presence_store,
    )


@pytest.mark.asyncio
async def test_the_least_loaded_eligible_agent_is_picked() -> None:
    """Within a tied tier-1 tier, the agent with fewer open conversations wins."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["whatsapp"]),
        AgentPriority(agent_id=2, channel_priorities=["whatsapp"]),
    ]
    result = await _svc(agents, priorities, open_counts={1: 3, 2: 1}, fair_share=True).pick_agent(
        "whatsapp"
    )
    assert result == 2  # Bob: 1 open < Alice's 3


@pytest.mark.asyncio
async def test_a_tie_is_broken_by_least_recently_assigned() -> None:
    """Equal load: the agent who was assigned longest ago (or never) goes next."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["whatsapp"]),
        AgentPriority(agent_id=2, channel_priorities=["whatsapp"]),
    ]
    svc = _svc(agents, priorities, open_counts={1: 0, 2: 0}, fair_share=True)
    first = await svc.pick_agent("whatsapp")
    second = await svc.pick_agent("whatsapp")
    assert first == 1  # tied on load and both never-assigned -> lowest id wins
    assert second == 2  # Alice was just assigned; Bob is now the least-recent


@pytest.mark.asyncio
async def test_tier_order_is_unchanged_first_priority_still_beats_any_priority() -> None:
    """Fair share operates within a tier, never across tiers.

    A tier-1 agent buried in nine open conversations still beats an idle
    tier-3 agent -- channel priority picks the tier before load ever gets a
    vote. Getting this backwards would route WhatsApp work to whoever
    happens to be idle, defeating channel specialisation entirely.
    """
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),  # tier 1, heavily loaded
        AgentRecord(id=2, name="Ivan", availability_status="online"),  # tier 3, idle
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["whatsapp"]),
    ]
    result = await _svc(agents, priorities, open_counts={1: 9, 2: 0}, fair_share=True).pick_agent(
        "whatsapp"
    )
    assert result == 1  # tier-1 wins despite the load, over an idle tier-3 agent


@pytest.mark.asyncio
async def test_a_non_routable_custom_status_excludes_an_online_agent() -> None:
    """`routable=False` is an additional filter on top of native `online`."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["whatsapp"]),
        AgentPriority(agent_id=2, channel_priorities=["whatsapp"]),
    ]
    lunch = CustomStatus(
        key="lunch",
        label="Lunch",
        color="#e8a33d",
        routable=False,
        native="busy",
        counts_as_unavailable=True,
    )
    now = datetime.now(UTC)
    presence_store = _FakePresenceEventStore(
        latest_by_agent={
            1: PresenceEvent(agent_id=1, status="lunch", at=now, source="agent", previous=None)
        }
    )
    status_store = _FakeCustomStatusStore(statuses={"lunch": lunch})
    result = await _svc(
        agents,
        priorities,
        open_counts={1: 0, 2: 0},
        fair_share=True,
        custom_status_store=status_store,
        presence_store=presence_store,
    ).pick_agent("whatsapp")
    assert result == 2  # Alice is on Lunch (not routable) despite native "online"


@pytest.mark.asyncio
async def test_a_custom_status_store_outage_falls_back_to_the_native_status_filter() -> None:
    """A catalogue outage degrades to exactly today's native-status filter.

    `CustomStatusStore.get` returns `None` for both an unknown key AND an
    outage -- `None` must never be read as "not routable" (would silently
    halt routing) and never as "routable" (would keep an unroutable agent
    eligible forever). The only degradation is "no extra information": the
    native `online` check remains the sole gate.
    """
    agents = [AgentRecord(id=1, name="Alice", availability_status="online")]
    priorities = [AgentPriority(agent_id=1, channel_priorities=["whatsapp"])]
    now = datetime.now(UTC)
    presence_store = _FakePresenceEventStore(
        latest_by_agent={
            1: PresenceEvent(agent_id=1, status="lunch", at=now, source="agent", previous=None)
        }
    )
    status_store = _FakeCustomStatusStore(outage=True)
    result = await _svc(
        agents,
        priorities,
        open_counts={1: 0},
        fair_share=True,
        custom_status_store=status_store,
        presence_store=presence_store,
    ).pick_agent("whatsapp")
    assert result == 1  # outage -> get() returns None -> fail open, still eligible


@pytest.mark.asyncio
async def test_routability_is_checked_lazily_and_never_for_a_tier_that_is_never_tried() -> None:
    """The perf half of this fix: a tier-3 idle-pool agent's presence
    status must never be looked up when a tier-1 candidate already
    satisfies the request -- checking every online agent up front (instead
    of only the tier actually used) was extra `PresenceEventStore.latest`
    round trips for agents whose routability could never change the
    outcome."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),  # tier 1
        AgentRecord(id=2, name="Ivan", availability_status="online"),  # tier 3, idle
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["whatsapp"]),
    ]
    presence_store = _FakePresenceEventStore()
    status_store = _FakeCustomStatusStore()
    result = await _svc(
        agents,
        priorities,
        open_counts={1: 0, 2: 0},
        fair_share=True,
        custom_status_store=status_store,
        presence_store=presence_store,
    ).pick_agent("whatsapp")
    assert result == 1
    assert presence_store.calls == [1]  # agent 2's status was never checked


@pytest.mark.asyncio
async def test_the_flag_off_reproduces_todays_first_match_selection() -> None:
    """`routing_fair_share_enabled=False` (default): first match wins regardless of load."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["whatsapp"]),
        AgentPriority(agent_id=2, channel_priorities=["whatsapp"]),
    ]
    # Alice is first in priority order and far more loaded; fair share would
    # pick Bob, but with the flag off, first-match wins regardless of load,
    # and the open-count fetch is never even consulted for selection.
    result = await _svc(agents, priorities, open_counts={1: 9, 2: 0}, fair_share=False).pick_agent(
        "whatsapp"
    )
    assert result == 1


@pytest.mark.asyncio
async def test_every_existing_three_tier_routing_test_still_passes() -> None:
    """Re-runs the pre-existing `pick_agent` assertions with fair share off.

    Deliberate duplication of `test_routing_service.py`'s
    `test_cap_disabled_by_default_matches_prior_behavior` shape, mandated by
    the task plan: this file's own regression net, independent of the
    original suite continuing to exist untouched.
    """
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    assert await _svc(agents, priorities).pick_agent("WhatsApp") == 1

    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    assert await _svc(agents, priorities).pick_agent("WhatsApp") == 2

    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=3, name="Carol", availability_status="online"),
    ]
    priorities2 = [AgentPriority(agent_id=1, channel_priorities=["WhatsApp"])]
    assert await _svc(agents, priorities2).pick_agent("WhatsApp") == 3

    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=2, name="Bob", availability_status="offline"),
    ]
    priorities3 = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]),
        AgentPriority(agent_id=2, channel_priorities=["WhatsApp"]),
    ]
    assert await _svc(agents, priorities3).pick_agent("WhatsApp") is None

    agents = [
        AgentRecord(id=5, name="Eve", availability_status="online"),
        AgentRecord(id=6, name="Frank", availability_status="offline"),
    ]
    assert await _svc(agents, []).pick_agent("email") == 5

    agents = [AgentRecord(id=1, name="Alice", availability_status="online")]
    priorities4 = [AgentPriority(agent_id=1, channel_priorities=["whatsapp"])]
    assert await _svc(agents, priorities4).pick_agent("WhatsApp") == 1

    # Concurrency-cap tests, also unaffected by fair share being off.
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    assert (
        await _svc(agents, priorities, open_counts={1: 2}, max_concurrent=2).pick_agent("WhatsApp")
        == 2
    )
    assert (
        await _svc(agents, priorities, open_counts={1: 1}, max_concurrent=2).pick_agent("WhatsApp")
        == 1
    )


@pytest.mark.asyncio
async def test_ten_conversations_across_two_equal_agents_split_five_five() -> None:
    """The observable outcome requirement 4.16 is really asking for: rotation.

    Two equally-loaded, equally-eligible agents handed ten conversations in a
    row split them five-five instead of one agent taking all ten.
    """
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["whatsapp"]),
        AgentPriority(agent_id=2, channel_priorities=["whatsapp"]),
    ]
    svc = _svc(agents, priorities, open_counts={1: 0, 2: 0}, fair_share=True)
    picks = [await svc.pick_agent("whatsapp") for _ in range(10)]
    assert picks.count(1) == 5
    assert picks.count(2) == 5
