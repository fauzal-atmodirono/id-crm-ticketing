from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.service import RoutingService
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.features.routing.sweeper import run_sweep, start_routing_sweeper
from chatbot.platform.config import Settings

NOW = 1_000_000.0


def _conv(
    conv_id: int,
    *,
    created_at: float,
    assignee_id: int | None = None,
    status: str = "open",
) -> dict[str, Any]:
    return {
        "id": conv_id,
        "status": status,
        "created_at": created_at,
        "meta": {"assignee": {"id": assignee_id} if assignee_id is not None else None},
    }


class _FakeAssigner:
    """Stands in for ``RoutingAssigner``: records every ``assign`` call and
    lets a test seed/observe the "current assignee" ``resolve_assignee``
    reads, without a real Chatwoot."""

    def __init__(self, assigned: dict[int, int] | None = None, channel: str = "web") -> None:
        self.assigned: dict[int, int] = dict(assigned) if assigned else {}
        self.channel = channel
        self.assign_calls: list[tuple[int, int]] = []

    async def resolve_channel(self, conversation_id: int) -> str:
        return self.channel

    async def resolve_assignee(self, conversation_id: int) -> int | None:
        return self.assigned.get(conversation_id)

    async def assign(self, conversation_id: int, agent_id: int) -> None:
        self.assign_calls.append((conversation_id, agent_id))
        self.assigned[conversation_id] = agent_id


class _FakePicker:
    """Stands in for ``RoutingService``: returns a fixed agent id (or
    ``None``, meaning "nobody eligible") and records every channel it was
    asked to pick for."""

    def __init__(self, agent_id: int | None) -> None:
        self.agent_id = agent_id
        self.calls: list[str] = []

    async def pick_agent(self, conv_channel: str) -> int | None:
        self.calls.append(conv_channel)
        return self.agent_id


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})

    def start(self) -> None:
        self.started = True


@pytest.mark.asyncio
async def test_an_aged_unassigned_conversation_is_assigned() -> None:
    settings = Settings(
        routing_enabled=True, routing_sweep_enabled=True, routing_sweep_min_age_seconds=120
    )
    conv = _conv(1, created_at=NOW - 200)  # 200s old, past the 120s min-age gate
    assigner = _FakeAssigner()
    picker = _FakePicker(agent_id=7)

    async def fetch(_settings: Settings) -> list[dict[str, Any]]:
        return [conv]

    result = await run_sweep(settings, picker, assigner, fetch_conversations=fetch, now=lambda: NOW)
    assert assigner.assign_calls == [(1, 7)]
    assert result == {"scanned": 1, "assigned": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_a_fresh_unassigned_conversation_is_left_to_the_event_path() -> None:
    """The min-age gate is the race guard: without it, the sweeper and the
    event-driven handoff path (which runs essentially immediately at
    handoff time) would both try to assign the same brand-new
    conversation. A fresh conversation must not even reach ``pick_agent``.
    """
    settings = Settings(
        routing_enabled=True, routing_sweep_enabled=True, routing_sweep_min_age_seconds=120
    )
    conv = _conv(1, created_at=NOW - 30)  # only 30s old
    assigner = _FakeAssigner()
    picker = _FakePicker(agent_id=7)

    async def fetch(_settings: Settings) -> list[dict[str, Any]]:
        return [conv]

    result = await run_sweep(settings, picker, assigner, fetch_conversations=fetch, now=lambda: NOW)
    assert assigner.assign_calls == []
    assert picker.calls == []  # never even asked -- left entirely to the event path
    assert result == {"scanned": 0, "assigned": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_an_already_assigned_conversation_is_skipped() -> None:
    settings = Settings(
        routing_enabled=True, routing_sweep_enabled=True, routing_sweep_min_age_seconds=120
    )
    conv = _conv(1, created_at=NOW - 300, assignee_id=42)
    assigner = _FakeAssigner()
    picker = _FakePicker(agent_id=7)

    async def fetch(_settings: Settings) -> list[dict[str, Any]]:
        return [conv]

    result = await run_sweep(settings, picker, assigner, fetch_conversations=fetch, now=lambda: NOW)
    assert assigner.assign_calls == []
    assert picker.calls == []
    assert result == {"scanned": 0, "assigned": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_no_eligible_agent_leaves_the_conversation_unassigned_without_error() -> None:
    """``pick_agent`` returning ``None`` (everyone busy/offline/over-cap) is
    a normal outcome, not a failure: it must be logged and skipped, never
    raised."""
    settings = Settings(
        routing_enabled=True, routing_sweep_enabled=True, routing_sweep_min_age_seconds=120
    )
    conv = _conv(1, created_at=NOW - 300)
    assigner = _FakeAssigner()
    picker = _FakePicker(agent_id=None)

    async def fetch(_settings: Settings) -> list[dict[str, Any]]:
        return [conv]

    result = await run_sweep(settings, picker, assigner, fetch_conversations=fetch, now=lambda: NOW)
    assert assigner.assign_calls == []
    assert result == {"scanned": 1, "assigned": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_two_concurrent_sweeps_do_not_double_assign() -> None:
    """Two independent protections, doing different jobs.

    Phase 1 is the fast-path guard and the real protection in the common,
    single-process case: the module-level lock makes a second overlapping
    tick return immediately -- before it even lists conversations -- while
    an earlier tick is still mid-flight.

    Phase 2 is the actual correctness backstop: even when nothing shares a
    lock at all (a second replica in a multi-replica deployment, or simply
    the check-then-acquire window around the lock itself), the pre-write
    ``resolve_assignee`` re-check still catches a conversation that was
    assigned in the gap between this tick's list fetch and its write.
    Removing this re-check because "the lock already handles it" would
    silently reopen exactly that gap -- the lock cannot reach across
    processes, and a reader adding a second replica later is the reader
    this docstring (and ``run_sweep``'s) is for.
    """
    settings = Settings(
        routing_enabled=True, routing_sweep_enabled=True, routing_sweep_min_age_seconds=120
    )
    conv = _conv(1, created_at=NOW - 300)
    picker = _FakePicker(agent_id=7)

    # --- Phase 1: the in-process lock (shared default, as every real
    # scheduler tick uses it -- no `lock=` passed by either call here).
    assigner = _FakeAssigner()
    fetch_calls = 0

    async def slow_fetch(_settings: Settings) -> list[dict[str, Any]]:
        nonlocal fetch_calls
        fetch_calls += 1
        await asyncio.sleep(0.05)
        return [conv]

    task_a = asyncio.create_task(
        run_sweep(settings, picker, assigner, fetch_conversations=slow_fetch, now=lambda: NOW)
    )
    await asyncio.sleep(0)  # let task_a acquire the lock and start its slow fetch
    task_b = asyncio.create_task(
        run_sweep(settings, picker, assigner, fetch_conversations=slow_fetch, now=lambda: NOW)
    )
    _result_a, result_b = await asyncio.gather(task_a, task_b)

    assert assigner.assign_calls == [(1, 7)]  # exactly once
    assert fetch_calls == 1  # task_b was skipped before it ever listed conversations
    assert result_b == {"scanned": 0, "assigned": 0, "skipped": 0}

    # --- Phase 2: the pre-write re-check, for when the lock can't help --
    # a fresh, independent `Lock()` stands in for a second replica that
    # shares no in-process state with the first at all.
    assigner2 = _FakeAssigner(assigned={1: 99})  # "already assigned by the other replica"

    async def fast_fetch(_settings: Settings) -> list[dict[str, Any]]:
        return [conv]  # this tick's own list fetch is stale: still shows it unassigned

    result = await run_sweep(
        settings,
        picker,
        assigner2,
        fetch_conversations=fast_fetch,
        now=lambda: NOW,
        lock=asyncio.Lock(),
    )
    assert assigner2.assign_calls == []
    assert assigner2.assigned[1] == 99  # unchanged
    assert result == {"scanned": 1, "assigned": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_the_sweep_respects_the_per_agent_concurrency_cap() -> None:
    """The cap is enforced inside ``pick_agent`` -- routing every candidate
    through it is what makes the sweeper honour the cap for free. This test
    uses a real ``RoutingService`` (not a fake picker) so that a sweeper
    which reimplemented its own cap check, instead of delegating, would
    fail it: Alice is pinned at the cap and must never be chosen."""
    settings = Settings(
        routing_enabled=True,
        routing_sweep_enabled=True,
        routing_sweep_min_age_seconds=120,
        routing_max_concurrent_per_agent=2,
    )
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    fetcher = PresenceFetcher(settings)

    async def _fetch_agents() -> list[AgentRecord]:
        return agents

    async def _fetch_open_counts() -> dict[int, int]:
        return {1: 2}  # Alice is exactly at the cap; Bob has none on record

    fetcher.fetch_agents = _fetch_agents  # type: ignore[method-assign]
    fetcher.fetch_agent_open_counts = _fetch_open_counts  # type: ignore[method-assign]

    store = ChannelPriorityStore(settings)

    async def _list_all() -> list[AgentPriority]:
        return []

    store.list_all = _list_all  # type: ignore[method-assign]

    routing_svc = RoutingService(presence=fetcher, store=store, settings=settings)
    assigner = _FakeAssigner()
    conv = _conv(1, created_at=NOW - 300)

    async def fetch(_settings: Settings) -> list[dict[str, Any]]:
        return [conv]

    result = await run_sweep(
        settings, routing_svc, assigner, fetch_conversations=fetch, now=lambda: NOW
    )
    assert assigner.assigned[1] == 2  # Bob, never the capped-out Alice
    assert result == {"scanned": 1, "assigned": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_the_flag_off_runs_no_sweep() -> None:
    conv = _conv(1, created_at=NOW - 300)
    picker = _FakePicker(agent_id=7)

    async def fetch(_settings: Settings) -> list[dict[str, Any]]:
        return [conv]

    # Both flags off -- no sweep at all. Stated explicitly rather than left to
    # `Settings()`'s defaults: the both-flag-states gate
    # (deploy/scripts/check-suites-both-flag-states.sh) runs this whole suite
    # with ROUTING_SWEEP_ENABLED=true in the environment, and pydantic-settings
    # reads os.environ regardless of `_env_file`, so a bare `Settings()` here
    # would silently assert the flag-ON behaviour on that run.
    assigner = _FakeAssigner()
    result = await run_sweep(
        Settings(routing_enabled=False, routing_sweep_enabled=False),
        picker,
        assigner,
        fetch_conversations=fetch,
        now=lambda: NOW,
    )
    assert result == {"scanned": 0, "assigned": 0, "skipped": 0}
    assert assigner.assign_calls == []

    # routing_enabled on, but routing_sweep_enabled still off -- still no
    # sweep. The two flags are gated independently.
    result = await run_sweep(
        Settings(routing_enabled=True, routing_sweep_enabled=False),
        picker,
        assigner,
        fetch_conversations=fetch,
        now=lambda: NOW,
    )
    assert result == {"scanned": 0, "assigned": 0, "skipped": 0}
    assert assigner.assign_calls == []

    # routing_sweep_enabled on, but the master routing_enabled switch still
    # off -- still no sweep.
    result = await run_sweep(
        Settings(routing_enabled=False, routing_sweep_enabled=True),
        picker,
        assigner,
        fetch_conversations=fetch,
        now=lambda: NOW,
    )
    assert result == {"scanned": 0, "assigned": 0, "skipped": 0}
    assert assigner.assign_calls == []

    # And the scheduler-starter side: with both defaults off, no job is
    # ever registered and the scheduler is never started.
    fake_sched = _FakeScheduler()
    assert start_routing_sweeper(Settings(), picker, assigner, scheduler=fake_sched) is None
    assert fake_sched.jobs == []
    assert fake_sched.started is False
