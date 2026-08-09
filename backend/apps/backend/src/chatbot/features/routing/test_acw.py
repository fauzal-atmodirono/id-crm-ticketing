"""P6 task 5 -- After-Call-Work (ACW).

Two properties matter more than the rest:

`test_acw_auto_exits_after_the_timeout` is the safety property `acw_timeout
_seconds` exists for at all -- without it, an agent who forgets to leave
wrap-up is silently removed from routing for the rest of their shift, which
gets reported (and debugged) as a routing bug, not a wrap-up bug.

`test_an_agent_already_offline_is_not_moved_into_acw` -- an agent who hung
up and went home must not be resurrected into a wrap-up state.

Every fake here routes through the REAL `SEED_STATUSES` catalogue (task 2)
for the `acw`/`available` entries rather than inventing routable/counts_as_
unavailable values for this test file -- `test_an_agent_in_acw_is_not_
routable` and `test_acw_does_not_count_as_unavailable_for_the_threshold_
alerts` would be worthless if they asserted against a hardcoded bool instead
of the same catalogue `pick_agent`/the threshold-alert scan actually read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from chatbot.features.routing.acw import (
    ACW_EXIT_STATUS_KEY,
    ACW_STATUS_KEY,
    ACWController,
    start_acw_sweeper,
)
from chatbot.features.routing.custom_status import SEED_STATUSES, CustomStatus
from chatbot.features.routing.presence import AgentRecord
from chatbot.features.routing.presence_store import PresenceEvent
from chatbot.platform.config import Settings


def _settings(*, acw_enabled: bool = True, acw_timeout_seconds: int = 120) -> Settings:
    return Settings(
        _env_file=None, acw_enabled=acw_enabled, acw_timeout_seconds=acw_timeout_seconds
    )


def _catalogue_entry(key: str) -> CustomStatus:
    return next(s for s in SEED_STATUSES if s.key == key)


class _FakeAssigner:
    def __init__(self, agent_id: int | None) -> None:
        self._agent_id = agent_id
        self.calls: list[int] = []

    async def resolve_assignee(self, conversation_id: int) -> int | None:
        self.calls.append(conversation_id)
        return self._agent_id


class _RaisingAssigner:
    async def resolve_assignee(self, conversation_id: int) -> int | None:
        raise RuntimeError("chatwoot down")


class _FakeStatusStoreAndPresence:
    """Stands in for BOTH `CustomStatusStore` and `PresenceEventStore` --
    `ACWController` only needs `get`/`set_status` from the former and
    `latest` from the latter, and a single fake covering both keeps the
    "one ordered event log" behaviour easy to assert on directly.

    `set_status` mirrors the REAL `CustomStatusStore.set_status`'s ordering
    contract (native write first, event appended only after) but the
    "native write" here is just a recorded tuple -- this file is not
    re-testing task 2's Chatwoot-write plumbing, only that ACWController
    drives `set_status` correctly.
    """

    def __init__(self) -> None:
        self.native_writes: list[tuple[int, str]] = []
        self.events: list[PresenceEvent] = []

    async def get(self, key: str) -> CustomStatus | None:
        for status in SEED_STATUSES:
            if status.key == key:
                return status
        return None

    async def set_status(
        self, agent_id: int, key: str, *, source: str = "agent", now: datetime | None = None
    ) -> bool:
        status = await self.get(key)
        if status is None:
            return False
        self.native_writes.append((agent_id, status.native))
        previous_events = [e for e in self.events if e.agent_id == agent_id]
        previous = previous_events[-1].status if previous_events else None
        self.events.append(
            PresenceEvent(
                agent_id=agent_id,
                status=key,
                at=now or datetime.now(UTC),
                source=source,
                previous=previous,
            )
        )
        return True

    async def latest(self, agent_id: int) -> PresenceEvent | None:
        matches = [e for e in self.events if e.agent_id == agent_id]
        return matches[-1] if matches else None


class _FakePresenceFetcher:
    def __init__(
        self,
        availability: dict[int, str] | None = None,
        agents: list[AgentRecord] | None = None,
    ) -> None:
        self._availability = availability or {}
        self._agents = agents or []
        self.availability_checks: list[int] = []

    async def fetch_agent_availability(self, agent_id: int) -> str:
        self.availability_checks.append(agent_id)
        return self._availability.get(agent_id, "online")

    async def fetch_agents(self) -> list[AgentRecord]:
        return self._agents


def _controller(
    assigner: _FakeAssigner | _RaisingAssigner,
    store: _FakeStatusStoreAndPresence,
    fetcher: _FakePresenceFetcher,
    *,
    acw_enabled: bool = True,
) -> ACWController:
    return ACWController(
        _settings(acw_enabled=acw_enabled),
        assigner,
        custom_status_store=store,
        presence_store=store,
        presence_fetcher=fetcher,
    )


async def test_a_call_ending_puts_the_agent_into_acw():
    assigner = _FakeAssigner(agent_id=42)
    store = _FakeStatusStoreAndPresence()
    controller = _controller(assigner, store, _FakePresenceFetcher())

    agent_id = await controller.start_after_call(conversation_id=101)

    assert agent_id == 42
    assert assigner.calls == [101]
    assert store.native_writes == [(42, _catalogue_entry(ACW_STATUS_KEY).native)]
    assert store.events[-1].agent_id == 42
    assert store.events[-1].status == ACW_STATUS_KEY
    assert store.events[-1].source == "system"


async def test_an_agent_in_acw_is_not_routable():
    assigner = _FakeAssigner(agent_id=42)
    store = _FakeStatusStoreAndPresence()
    controller = _controller(assigner, store, _FakePresenceFetcher())

    await controller.start_after_call(conversation_id=101)

    entered_status = await store.get(store.events[-1].status)
    assert entered_status is not None
    assert entered_status.routable is False


async def test_an_agent_can_leave_acw_manually():
    assigner = _FakeAssigner(agent_id=42)
    store = _FakeStatusStoreAndPresence()
    controller = _controller(assigner, store, _FakePresenceFetcher())
    await controller.start_after_call(conversation_id=101)

    left = await controller.leave(42)

    assert left is True
    assert store.events[-1].status == ACW_EXIT_STATUS_KEY
    assert store.events[-1].source == "agent"
    assert store.events[-1].previous == ACW_STATUS_KEY


async def test_acw_auto_exits_after_the_timeout():
    store = _FakeStatusStoreAndPresence()
    controller = _controller(_FakeAssigner(42), store, _FakePresenceFetcher())
    t0 = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    await controller.enter(42, now=t0)

    # Not yet due: well inside the 120s timeout.
    still_due = await controller.expire_if_due(42, now=t0 + timedelta(seconds=30))
    assert still_due is False
    assert store.events[-1].status == ACW_STATUS_KEY

    # Past the timeout: auto-exits, attributed to the system, not the agent.
    exited = await controller.expire_if_due(42, now=t0 + timedelta(seconds=121))
    assert exited is True
    assert store.events[-1].status == ACW_EXIT_STATUS_KEY
    assert store.events[-1].source == "system"


async def test_the_acw_duration_is_recorded_as_a_presence_event():
    store = _FakeStatusStoreAndPresence()
    controller = _controller(_FakeAssigner(42), store, _FakePresenceFetcher())
    entered_at = datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC)
    left_at = entered_at + timedelta(seconds=90)

    await controller.enter(42, now=entered_at)
    await controller.leave(42, now=left_at)

    acw_events = [e for e in store.events if e.status == ACW_STATUS_KEY]
    assert len(acw_events) == 1
    # The duration is DERIVABLE from the two timestamped events -- exactly
    # PresenceEventStore's own convention (task 1) -- not stored anywhere
    # as a separate duration field.
    exit_event = store.events[-1]
    duration = exit_event.at - acw_events[0].at
    assert duration == timedelta(seconds=90)
    assert exit_event.previous == ACW_STATUS_KEY


async def test_acw_does_not_count_as_unavailable_for_the_threshold_alerts():
    assigner = _FakeAssigner(agent_id=42)
    store = _FakeStatusStoreAndPresence()
    controller = _controller(assigner, store, _FakePresenceFetcher())

    await controller.start_after_call(conversation_id=101)

    entered_status = await store.get(ACW_STATUS_KEY)
    assert entered_status is not None
    assert entered_status.counts_as_unavailable is False


async def test_an_agent_already_offline_is_not_moved_into_acw():
    assigner = _FakeAssigner(agent_id=42)
    store = _FakeStatusStoreAndPresence()
    fetcher = _FakePresenceFetcher(availability={42: "offline"})
    controller = _controller(assigner, store, fetcher)

    agent_id = await controller.start_after_call(conversation_id=101)

    assert agent_id is None
    assert store.native_writes == []
    assert store.events == []
    assert fetcher.availability_checks == [42]


async def test_the_flag_off_leaves_call_end_handling_unchanged():
    assigner = _FakeAssigner(agent_id=42)
    store = _FakeStatusStoreAndPresence()
    fetcher = _FakePresenceFetcher()
    controller = _controller(assigner, store, fetcher, acw_enabled=False)

    agent_id = await controller.start_after_call(conversation_id=101)

    assert agent_id is None
    # Completely unchanged: not even the assignee lookup happens -- the
    # exact "byte-identical" property this task's flag exists to protect.
    assert assigner.calls == []
    assert fetcher.availability_checks == []
    assert store.native_writes == []
    assert store.events == []


async def test_start_after_call_skips_when_resolve_assignee_fails():
    """Not one of the eight named tests, but the direct counterpart to
    `resolve_assignee`'s own fail-open contract: a Chatwoot outage during
    assignee resolution must not raise out of the call-end webhook."""
    store = _FakeStatusStoreAndPresence()
    controller = _controller(_RaisingAssigner(), store, _FakePresenceFetcher())

    agent_id = await controller.start_after_call(conversation_id=101)

    assert agent_id is None
    assert store.events == []


async def test_sweep_exits_every_agent_past_the_timeout():
    """Not one of the eight named tests -- covers the explicit "belt"
    sweep mechanism itself, restart-safe by construction since it re-reads
    the stored event timestamp rather than any in-process timer."""
    store = _FakeStatusStoreAndPresence()
    controller = _controller(_FakeAssigner(1), store, _FakePresenceFetcher())
    t0 = datetime(2026, 8, 9, 8, 0, 0, tzinfo=UTC)
    await controller.enter(1, now=t0)
    await controller.enter(2, now=t0)

    agents = [
        AgentRecord(id=1, name="A", availability_status="busy"),
        AgentRecord(id=2, name="B", availability_status="busy"),
    ]
    fetcher = _FakePresenceFetcher(agents=agents)
    controller = _controller(_FakeAssigner(1), store, fetcher)

    exited = await controller.sweep(now=t0 + timedelta(seconds=200))

    assert exited == 2
    assert (await store.latest(1)).status == ACW_EXIT_STATUS_KEY  # type: ignore[union-attr]
    assert (await store.latest(2)).status == ACW_EXIT_STATUS_KEY  # type: ignore[union-attr]


# --- the timeout sweeper's scheduler wiring (P6 task 11) ------------------


class _RecordingScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, object]] = []
        self.started = False

    def add_job(self, func: object, **kwargs: object) -> None:
        self.jobs.append(kwargs)

    def start(self) -> None:
        self.started = True


def test_the_acw_sweeper_does_not_exist_with_the_flag_off():
    """Flag off must mean no scheduler at all, not a scheduler that ticks and
    finds nothing: the latter still calls Chatwoot's agent list every minute on
    a tenant that never asked for After-Call-Work."""
    sched = _RecordingScheduler()
    controller = _controller(
        _FakeAssigner(1), _FakeStatusStoreAndPresence(), _FakePresenceFetcher()
    )

    result = start_acw_sweeper(
        _settings(acw_enabled=False), controller, scheduler=sched, job=lambda: None
    )

    assert result is None
    assert sched.jobs == []
    assert sched.started is False


def test_the_acw_sweeper_ticks_on_the_presence_poll_cadence():
    """One tunable for one class of work: the sweep is the same shape and cost
    as a presence-poller tick, so it reuses that interval rather than adding a
    second number for an operator to get wrong."""
    sched = _RecordingScheduler()
    settings = _settings()
    controller = _controller(
        _FakeAssigner(1), _FakeStatusStoreAndPresence(), _FakePresenceFetcher()
    )

    result = start_acw_sweeper(settings, controller, scheduler=sched, job=lambda: None)

    assert result is sched
    assert sched.started is True
    assert len(sched.jobs) == 1
    assert sched.jobs[0]["id"] == "acw_timeout_sweep"
    assert sched.jobs[0]["seconds"] == settings.presence_poll_seconds
