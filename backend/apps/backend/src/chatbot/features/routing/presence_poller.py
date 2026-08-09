"""P6 task 3 -- the presence poller.

Chatwoot has no presence webhook: an agent who changes their own status from
Chatwoot's native UI (not through our `CustomStatusStore.set_status`) is
invisible to us unless something asks Chatwoot directly. This module is that
something -- a periodic, account-wide poll that diffs what Chatwoot reports
against what the presence-event store (task 1) last recorded, and appends an
event for whatever changed, tagged `source="poll"` per the design doc's four
event sources (`agent`/`admin`/`system`/`poll`).

Two behaviours are load-bearing:

**One poll, one Chatwoot API call, regardless of headcount.**
`PresenceFetcher.fetch_agents()` is already account-wide (`GET
/accounts/{id}/agents`, no per-agent loop) -- this poller calls it exactly
once per tick and diffs client-side. A per-agent call here would turn a
60-second timer into a 60-second multiplier on Chatwoot API usage, scaling
with the number of agents on the account instead of staying flat.

**The comparison is against the NATIVE value the store's latest event
implies, never the stored status string itself.** A custom status (task 2)
mirrors onto one of Chatwoot's three native values -- picking "Lunch" sets
the real Chatwoot status to `busy` while the event log records "lunch".
Comparing `latest.status != fetched.availability_status` literally would see
`"lunch" != "busy"` on *every single tick* and append a spurious `busy`
event, permanently clobbering the agent's real status in the dashboard.
`_expected_native` maps the stored status back through the catalogue first,
so "no change" and "the stored custom status already mirrors to this native
value" collapse to the same case. The same mapping is what keeps a
deactivated/deleted agent (who stops appearing in `fetch_agents()` at all,
and is treated as having gone `offline`) from getting a fresh offline event
on every tick forever: once one `offline` event is appended, the next tick's
`_expected_native("offline")` is already `"offline"`, so nothing new is
written until the agent's status genuinely changes again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from chatbot.features.routing.custom_status import CustomStatus, build_custom_status_store
from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.presence_store import PresenceEvent, build_presence_event_store

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class _PresenceFetcherProto(Protocol):
    async def fetch_agents(self) -> list[AgentRecord]: ...


class _PresenceStoreProto(Protocol):
    async def append(self, event: PresenceEvent) -> None: ...

    async def latest(self, agent_id: int) -> PresenceEvent | None: ...


class _CustomStatusLookupProto(Protocol):
    async def get(self, key: str) -> CustomStatus | None: ...


class PresencePoller:
    """Diffs Chatwoot's native agent availability against the presence-event
    store and appends `source="poll"` events for whatever changed.

    All three collaborators are structural `Protocol`s (matching
    `CustomStatusStore`'s convention), not concrete-class dependencies, so
    tests can inject small purpose-built fakes without touching Firestore or
    a real Chatwoot -- see `test_presence_poller.py`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        fetcher: _PresenceFetcherProto | None = None,
        presence_store: _PresenceStoreProto | None = None,
        custom_status_store: _CustomStatusLookupProto | None = None,
    ) -> None:
        self._settings = settings
        self._fetcher: _PresenceFetcherProto = fetcher or PresenceFetcher(settings)
        self._presence_store: _PresenceStoreProto = presence_store or build_presence_event_store(
            settings
        )
        self._custom_status_store: _CustomStatusLookupProto = (
            custom_status_store or build_custom_status_store(settings)
        )
        # Agent ids ever observed via `fetch_agents()` in this poller's
        # lifetime. This is the only way to notice an agent dropping out of
        # the account entirely (deactivated/deleted) -- fetch_agents() simply
        # stops returning them, so there is nothing to diff against except a
        # remembered roster. Deliberately in-memory and never persisted: a
        # process restart re-learns the roster from the next successful
        # poll, so at worst a disappearance that happens entirely inside a
        # restart window is missed once -- an acceptable gap against the
        # alternative of an extra store round trip just to reconstruct a
        # roster this cheaply. It only ever grows (a returning agent is
        # simply re-added to the current set); that is what makes the
        # offline event fire once per disappearance rather than every tick,
        # see `_expected_native`.
        self._known_agent_ids: set[int] = set()

    async def poll(self, *, now: datetime | None = None) -> None:
        """Run one poll tick: fetch, diff, append events for what changed.

        Best-effort -- never raises. A failed fetch (network error, Chatwoot
        outage, or anything else) is logged and this tick does nothing
        further; the next scheduled tick starts over from a fresh fetch, so
        a single bad tick never blocks presence tracking from resuming.
        """
        try:
            moment = now or datetime.now(UTC)
            agents = await self._fetcher.fetch_agents()
            current_ids = {agent.id for agent in agents}

            disappeared_ids = self._known_agent_ids - current_ids
            self._known_agent_ids |= current_ids

            for agent in agents:
                await self._reconcile(agent.id, agent.availability_status, moment)
            for agent_id in disappeared_ids:
                await self._reconcile(agent_id, "offline", moment)
        except Exception as e:  # a bad tick must never crash the scheduler thread
            _log.error("presence_poll_failed", error=str(e))

    async def _reconcile(self, agent_id: int, fetched_status: str, moment: datetime) -> None:
        """Append one `source="poll"` event for `agent_id` iff `fetched_status`
        differs from the native value the store's latest event implies.

        No prior event at all means this is the first time the poller has
        ever seen this agent -- append an initial event rather than treating
        "no history" as "unchanged".
        """
        latest = await self._presence_store.latest(agent_id)
        if latest is None:
            await self._presence_store.append(
                PresenceEvent(
                    agent_id=agent_id,
                    status=fetched_status,
                    at=moment,
                    source="poll",
                    previous=None,
                )
            )
            return

        expected_native = await self._expected_native(latest.status)
        if expected_native == fetched_status:
            return

        await self._presence_store.append(
            PresenceEvent(
                agent_id=agent_id,
                status=fetched_status,
                at=moment,
                source="poll",
                previous=latest.status,
            )
        )

    async def _expected_native(self, stored_status: str) -> str:
        """The native Chatwoot value `stored_status` implies.

        `stored_status` is either a custom-status key (task 2's catalogue),
        in which case its `native` field is what Chatwoot should actually
        show -- or it is already a literal native value (an earlier poll
        tick wrote it directly, or it is simply not a catalogued key), in
        which case it *is* the expected native value. A catalogue miss
        (unknown key, or `get()` failing open to `None` on a store outage)
        falls back to the literal value too: degraded precision, never a
        crash and never a spurious status flip.
        """
        custom = await self._custom_status_store.get(stored_status)
        return custom.native if custom is not None else stored_status


def run_presence_poll_job(
    settings: Settings,
    *,
    poller: PresencePoller | None = None,
) -> None:
    """Run one presence poll tick from a synchronous scheduler context.

    `BackgroundScheduler` calls plain sync functions; `PresencePoller.poll`
    is async because the event store is. `asyncio.run` bridges that, the
    same solution `run_report_job` uses in the metrics scheduler. Wrapped in
    its own try/except (on top of `poll`'s own) so that even a failure in
    constructing the default poller can never crash the scheduler thread or
    stop the next tick from running.
    """
    try:
        asyncio.run((poller or PresencePoller(settings)).poll())
    except Exception as e:  # pragma: no cover - defensive, poll() itself never raises
        _log.error("presence_poll_job_failed", error=str(e))


def start_presence_poller(
    settings: Settings,
    *,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the in-app presence poller when enabled; else return `None`.

    Gated entirely on `settings.presence_tracking_enabled` (default
    `False`) -- flag off must mean no scheduler, no job, and no events, full
    stop.

    `settings.presence_poll_seconds` (default 60) is deliberate, not a
    rounding of convenience: the dashboard thresholds this feeds (task 4)
    are 10 minutes and 1 hour, so a minute of granularity is already well
    inside the noise floor of those decisions. Polling any tighter would
    multiply Chatwoot API calls -- one account-wide call per tick, forever,
    per tenant -- for zero decision-relevant precision.

    `scheduler`/`job` are injectable, matching `start_metrics_scheduler` /
    `start_report_scheduler`'s convention, purely so this is testable
    without a real `BackgroundScheduler` thread.
    """
    if not settings.presence_tracking_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_presence_poll_job(settings))
    sched.add_job(
        run,
        trigger="interval",
        seconds=settings.presence_poll_seconds,
        id="agent_presence_poll",
        replace_existing=True,
    )
    sched.start()
    _log.info("presence_poller_started", interval_seconds=settings.presence_poll_seconds)
    return sched
