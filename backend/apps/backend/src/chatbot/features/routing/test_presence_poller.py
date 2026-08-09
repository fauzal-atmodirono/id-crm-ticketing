"""P6 task 3 -- the presence poller.

`test_one_poll_makes_exactly_one_chatwoot_api_call` guards the cost: a
per-agent loop here would multiply Chatwoot API usage by headcount every
60 seconds. `PresenceFetcher.fetch_agents()` is already account-wide, and
this test is what stops a future "just fetch each agent's latest status"
refactor from silently reintroducing an N-call poll.

`test_a_status_set_through_set_status_is_not_double_recorded_by_the_poll` is
the defect most likely to sink this task: a custom status like "lunch"
mirrors onto native `busy` (task 2). A naive `latest.status !=
fetched.availability_status` comparison sees `"lunch" != "busy"` and would
append a spurious `busy` event on every tick, permanently destroying the
agent's real status in the event log. This test pins the poller to mapping
the stored status back through the catalogue before comparing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chatbot.features.routing.custom_status import CustomStatus
from chatbot.features.routing.presence import AgentRecord
from chatbot.features.routing.presence_poller import PresencePoller
from chatbot.features.routing.presence_store import PresenceEvent
from chatbot.platform.config import get_settings


def _dt(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


class _FakeFetcher:
    """Stands in for `PresenceFetcher`. `fail_times` lets a test simulate the
    first N calls raising (a Chatwoot outage) before succeeding."""

    def __init__(self, agents: list[AgentRecord], *, fail_times: int = 0) -> None:
        self.agents = agents
        self._fail_times = fail_times
        self.calls = 0

    async def fetch_agents(self) -> list[AgentRecord]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("chatwoot unreachable")
        return self.agents


class _FakePresenceStore:
    """Enough of `PresenceEventStore` (`latest` + `append`) for the poller to
    depend on, without dragging Firestore mocking into these tests --
    task 1's own suite already exercises the real store."""

    def __init__(self, events: list[PresenceEvent] | None = None) -> None:
        self.appended: list[PresenceEvent] = list(events or [])

    async def append(self, event: PresenceEvent) -> None:
        self.appended.append(event)

    async def latest(self, agent_id: int) -> PresenceEvent | None:
        events = [e for e in self.appended if e.agent_id == agent_id]
        return events[-1] if events else None


class _FakeCustomStatusStore:
    """Stands in for `CustomStatusStore.get` -- just the one catalogue entry
    ("lunch" mirrors to native "busy") the mirrored-status tests need."""

    def __init__(self, statuses: dict[str, CustomStatus] | None = None) -> None:
        self._statuses = statuses or {
            "lunch": CustomStatus(
                key="lunch",
                label="Lunch",
                color="#e8a33d",
                routable=False,
                native="busy",
                counts_as_unavailable=True,
            ),
        }

    async def get(self, key: str) -> CustomStatus | None:
        return self._statuses.get(key)


def _poller(
    fetcher: _FakeFetcher,
    presence_store: _FakePresenceStore | None = None,
    custom_status_store: _FakeCustomStatusStore | None = None,
) -> PresencePoller:
    return PresencePoller(
        get_settings(),
        fetcher=fetcher,
        presence_store=presence_store or _FakePresenceStore(),
        custom_status_store=custom_status_store or _FakeCustomStatusStore(),
    )


@pytest.mark.asyncio
async def test_a_changed_status_appends_one_event() -> None:
    presence_store = _FakePresenceStore(
        events=[PresenceEvent(agent_id=1, status="online", at=_dt(8), source="poll", previous=None)]
    )
    fetcher = _FakeFetcher([AgentRecord(id=1, name="Alice", availability_status="busy")])
    poller = _poller(fetcher, presence_store=presence_store)

    await poller.poll(now=_dt(9))

    assert len(presence_store.appended) == 2
    new_event = presence_store.appended[-1]
    assert new_event.agent_id == 1
    assert new_event.status == "busy"
    assert new_event.previous == "online"
    assert new_event.source == "poll"
    assert new_event.at == _dt(9)


@pytest.mark.asyncio
async def test_an_unchanged_status_appends_nothing() -> None:
    only_event = PresenceEvent(agent_id=1, status="online", at=_dt(8), source="poll", previous=None)
    presence_store = _FakePresenceStore(events=[only_event])
    fetcher = _FakeFetcher([AgentRecord(id=1, name="Alice", availability_status="online")])
    poller = _poller(fetcher, presence_store=presence_store)

    await poller.poll(now=_dt(9))

    assert presence_store.appended == [only_event]


@pytest.mark.asyncio
async def test_a_new_agent_appearing_appends_an_initial_event() -> None:
    presence_store = _FakePresenceStore()
    fetcher = _FakeFetcher([AgentRecord(id=7, name="New Hire", availability_status="online")])
    poller = _poller(fetcher, presence_store=presence_store)

    await poller.poll(now=_dt(9))

    assert len(presence_store.appended) == 1
    event = presence_store.appended[0]
    assert event.agent_id == 7
    assert event.status == "online"
    assert event.previous is None
    assert event.source == "poll"


@pytest.mark.asyncio
async def test_an_agent_disappearing_from_the_account_appends_an_offline_event() -> None:
    presence_store = _FakePresenceStore()
    fetcher = _FakeFetcher([AgentRecord(id=5, name="Departed", availability_status="online")])
    poller = _poller(fetcher, presence_store=presence_store)

    await poller.poll(now=_dt(9))  # tick 1: agent 5 is seen, online

    fetcher.agents = []  # deactivated/deleted in Chatwoot -- no longer returned at all
    await poller.poll(now=_dt(10))  # tick 2: agent 5 has disappeared

    events = [e for e in presence_store.appended if e.agent_id == 5]
    assert len(events) == 2
    assert events[-1].status == "offline"
    assert events[-1].source == "poll"
    assert events[-1].previous == "online"

    await poller.poll(now=_dt(11))  # tick 3: still missing -- must not re-fire
    events = [e for e in presence_store.appended if e.agent_id == 5]
    assert len(events) == 2


@pytest.mark.asyncio
async def test_one_poll_makes_exactly_one_chatwoot_api_call() -> None:
    agents = [
        AgentRecord(id=i, name=f"Agent {i}", availability_status="online") for i in range(1, 6)
    ]
    fetcher = _FakeFetcher(agents)
    poller = _poller(fetcher)

    await poller.poll(now=_dt(9))

    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_a_fetch_failure_is_logged_and_the_next_tick_proceeds() -> None:
    presence_store = _FakePresenceStore()
    fetcher = _FakeFetcher(
        [AgentRecord(id=1, name="Alice", availability_status="online")], fail_times=1
    )
    poller = _poller(fetcher, presence_store=presence_store)

    await poller.poll(now=_dt(9))  # tick 1: fetch raises -- must not propagate
    assert presence_store.appended == []

    await poller.poll(now=_dt(10))  # tick 2: fetch succeeds -- polling has resumed
    assert len(presence_store.appended) == 1
    assert presence_store.appended[0].status == "online"
    assert presence_store.appended[0].source == "poll"


@pytest.mark.asyncio
async def test_a_status_set_through_set_status_is_not_double_recorded_by_the_poll() -> None:
    # Exactly what `CustomStatusStore.set_status` leaves behind after an
    # agent picks "Lunch": native Chatwoot status is `busy`, and the store's
    # latest event records the custom key "lunch".
    already_recorded = PresenceEvent(
        agent_id=1, status="lunch", at=_dt(9), source="agent", previous="available"
    )
    presence_store = _FakePresenceStore(events=[already_recorded])
    fetcher = _FakeFetcher([AgentRecord(id=1, name="Alice", availability_status="busy")])
    poller = _poller(fetcher, presence_store=presence_store)

    await poller.poll(now=_dt(9, 1))

    assert presence_store.appended == [already_recorded]
