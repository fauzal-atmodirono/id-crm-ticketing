"""P6 task 9 -- the workforce dashboard (requirement 4.73): a live view of
every agent's current status, elapsed time in it, today's time-per-status
breakdown, an availability percentage, assigned open cases, and (see below)
cases closed today.

Nothing before task 1's `PresenceEventStore` recorded presence history at
all, so this router is mostly a read over that store plus task 2's
`CustomStatusStore` catalogue (for turning a status key into a label/colour
and for `counts_as_unavailable`) and `PresenceFetcher` (the live Chatwoot
roster + open-case counts). `GET /admin/workforce` is gated by the
`workforce.view` permission (already registered in `authz/seed.py`'s
`PERMISSION_REGISTRY` by a prior wave -- not touched here).

Three load-bearing decisions, each because a naive version of this endpoint
would either lie or crash:

**1. "Availability percentage" is over the working day, not 24 hours.**
Dividing by a full calendar day would show every agent at roughly the same
~35% (an 8-hour shift over 24 hours) regardless of how well they actually
used their shift -- a number that looks like a metric but carries no signal.
This reuses `features.metrics.business_hours.working_minutes_between` (the
SAME row-shape parser `HandoffTargetResolver._within_business_hours` already
uses for the phone-handoff business-hours gate) as the one and only
"calendar" -- no second implementation of what a working day is. The inbox
is fetched via `features.metrics.sync.fetch_inbox_hours` (the same plain
`GET /inboxes/{id}` plumbing the BigQuery ETL uses) keyed on
`settings.chatwoot_inbox_id`, and **fails open** exactly like
`_within_business_hours` does: an unconfigured or unreadable inbox is
treated as "always open" (a bare calendar-minutes fallback --
`working_minutes_between`'s own documented behaviour for `{}`), never as a
reason to blank out or disable the whole dashboard.

Available time is computed per presence-event *segment* (not by taking the
per-status totals `time_in_status_since` already aggregates across the
whole day) specifically so each segment's overlap with working hours can be
measured on its own -- an agent who was "available" at 2am is not credited
with working-day availability just because the status label matches. Segment
classification: a segment is "available" unless its status is the literal
string `"offline"` or resolves (via `CustomStatusStore.get`) to a
`CustomStatus` with `counts_as_unavailable=True`. An unresolvable status key
(unknown key, or a catalogue outage -- `get()` returns `None` for both) is
treated as available, the same fail-open direction `custom_status.py`'s own
contract recommends ("no extra information" must never read as "absent").

**2. An agent with no events *ever* must show *unknown*, not zero -- but an
agent with events, just none since local midnight, is a different agent.**
Task 1's store deliberately returns `None` (never a zero-value duration) for
an agent with no presence history at all, because "0 min in Available" would
assert a transition that never happened. This router preserves that
distinction all the way to the response: `current_status.elapsed_minutes`
is `null` for such an agent, and `current_status.key` falls back to the
agent's LIVE native Chatwoot status (`AgentRecord.availability_status`,
always available from `PresenceFetcher.fetch_agents()` regardless of
presence history) so the row still renders something honest, just not an
elapsed duration.

`availability_percent_of_working_day` used to conflate this with a second,
narrower case -- an agent whose *last* transition was before today, so
`since(day_start)` comes back empty even though the agent's status is
perfectly well known (review finding I3: an agent "available" since
yesterday 08:00 rendered `time_in_status_today_minutes = {"available":
~600}` right beside `availability_percent_of_working_day: null` for the
same row). `_availability_percent` now accepts the agent's `latest` event as
a `carried` fallback: when there are no events *today*, but the agent has
been seen before (`carried is not None`), that carried status is credited
from `day_start` to `now` -- the exact segment `time_in_status_since` (task
1's store) already credits for the same reason. The blank stays reserved
for the one case where nothing is known at all: `since(day_start)` is empty
AND `carried is None`, i.e. the agent has no presence history whatsoever.
`time_in_status_today_minutes` / `availability_history` are still `{}` / `[]`
for that agent -- this fix does not touch that pair, only the percentage.

**3. "Cases closed today" is reported as `null`, not `0` -- see
`_CASES_CLOSED_TODAY_CAVEAT` below for why no helper exists that can honestly answer
this on a live ~30s poll.**

## The "Availability history" column -- NOT login/logout

Per this package's explicit "do not claim SSO login/logout" constraint:
`availability_history` is a list of transitions to/from the literal
`"offline"` presence-event status, nothing more. It is NOT a session
record -- an agent who closes their laptop without their Chatwoot status
ever changing stays shown as available until their next real transition.
Real login/logout tracking would need a Chatwoot-side session signal that
does not exist today. `_AVAILABILITY_HISTORY_DISCLAIMER` carries this
caveat in the response itself (and the fork's admin page titles the column
"Availability history", never "Login/logout" -- see
`deploy/chatwoot-fork/patches/0053-workforce-dashboard.patch`).

## Open case counts -- an empty tally is not a global zero

`PresenceFetcher.fetch_agent_open_counts()` is fail-open by its own contract
(see its docstring in `presence.py`): a Chatwoot pager failure on *any* page
returns `{}`, the exact same value a genuinely empty account (nobody has an
open conversation right now) would also produce -- both collapse to the same
empty dict at the source, and this router cannot tell them apart from the
dict alone (review finding I4). The old `open_counts.get(agent.id, 0)`
therefore rendered a Chatwoot outage as "the whole team is idle", the same
fabricated-zero lie this file exists to avoid for `cases_closed_today`.

The fix distinguishes three states, only two of which are ever a number:
the tally could not be established this poll (`open_counts` came back
empty) -> every agent's `open_case_count` is `null` and the response carries
`open_case_count_caveat`; the tally was established and this agent is absent
from it -> a real `0`; the tally was established and this agent is present
-> its real count. "Established" is inferred from non-emptiness (any agent
present with any count proves the fetch actually ran) rather than from an
exception, because `fetch_agent_open_counts()` never raises -- it already
fail-opens internally, so a router-level `try/except` around the call can
only ever see a normal return, never the failure itself.

## Freshness, not a fake "real-time" claim

Every other dashboard in this system is a 6-hour batch report. This one
reads the presence-event store directly on every request, so it genuinely
is current -- but "real-time" here means the caller polls this endpoint on
roughly a 30-second cadence, NOT a streamed/pushed feed. `generated_at` +
the `refresh` block say so explicitly rather than letting "real-time" be
read as something this endpoint does not do.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Protocol
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends

from chatbot.features.authz.deps import require_permission
from chatbot.features.metrics.business_hours import working_minutes_between
from chatbot.features.metrics.sync import fetch_inbox_hours
from chatbot.features.routing.custom_status import CustomStatus, build_custom_status_store
from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.presence_store import PresenceEvent, build_presence_event_store

if TYPE_CHECKING:
    from collections.abc import Callable

    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# A suggested cadence for whatever polls this endpoint (the fork's admin
# page). NOT a new Settings field -- config here is explicitly owned by a
# different wave; this is just the value baked into the response's own
# freshness disclosure (see the module docstring's "Freshness" section).
_RECOMMENDED_POLL_SECONDS = 30

_AVAILABILITY_HISTORY_DISCLAIMER = (
    "Derived from PresenceEvent transitions to/from the native `offline` "
    "status. This is NOT a login/logout session record: an agent who closes "
    "their laptop without their Chatwoot status changing stays shown as "
    "available until their next real transition. Genuine login/logout "
    "tracking would need a Chatwoot-side session signal that does not "
    "exist today."
)

_CASES_CLOSED_TODAY_CAVEAT = (
    "cases_closed_today is always null, never 0. No helper computes a "
    "date-filtered 'resolved today' count: features.metrics.sync."
    "fetch_conversations pages a tenant's ENTIRE conversation history "
    "(built for a nightly batch ETL job, not a live ~30s dashboard poll), "
    "and adding an unbounded full-history scan to every poll tick would "
    "not be an honest trade against the two other blocking full-account "
    "reads this endpoint already makes. A null is a statement about "
    "instrumentation; a 0 would be a claim about performance."
)

_OPEN_CASE_COUNT_CAVEAT = (
    "open_case_count is null for every agent this poll, not 0. "
    "fetch_agent_open_counts() came back empty, and that helper is "
    "deliberately fail-open (see presence.py: a partial undercount would be "
    "worse than no data), so an entirely empty tally is ambiguous between "
    "'the whole account genuinely has zero open conversations right now' "
    "and 'the Chatwoot conversations pager failed this poll' -- both "
    "collapse to the same {} at the source. Rather than assume the benign "
    "reading and show 0 for every agent (indistinguishable from a "
    "genuinely idle team on the very page built to avoid that fabrication), "
    "this poll reports unavailable. This does not apply once any agent has "
    "a nonzero count in the same poll -- at that point the fetch is known "
    "to have worked, and an agent absent from a non-empty tally has a real, "
    "honest 0."
)


class _AgentDirectory(Protocol):
    """The two `PresenceFetcher` methods this router depends on."""

    async def fetch_agents(self) -> list[AgentRecord]: ...

    async def fetch_agent_open_counts(self) -> dict[int, int]: ...


class _PresenceLog(Protocol):
    """The narrow slice of `PresenceEventStore`'s contract this router
    depends on -- kept structural so tests inject small in-memory fakes
    instead of Firestore, matching `presence_thresholds.py`'s convention."""

    async def latest(self, agent_id: int) -> PresenceEvent | None: ...

    async def since(self, agent_id: int, at: datetime) -> list[PresenceEvent]: ...

    async def elapsed_in_current_status(self, agent_id: int, now: datetime) -> timedelta | None: ...

    async def time_in_status_since(
        self, agent_id: int, since: datetime, now: datetime
    ) -> dict[str, timedelta]: ...


class _StatusCatalogue(Protocol):
    """The one `CustomStatusStore` method this router depends on."""

    async def get(self, key: str) -> CustomStatus | None: ...


def _local_day_start(now: datetime, inbox: dict[str, Any]) -> datetime:
    """Local midnight for `now`, in the inbox's configured timezone (falls
    back to UTC when unset/invalid). This is the single "today" boundary
    used for the today-per-status tile, the availability-history list, AND
    the availability-percentage denominator, so all three agree on what
    "today" means. This is NOT a second implementation of
    `working_minutes_between`'s day-walking calendar -- just the same
    timezone-lookup convention it already uses, reduced to "what time is
    midnight right now".
    """
    tz_name = inbox.get("timezone") or "UTC"
    tz: tzinfo
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        _log.debug("workforce_unknown_timezone", timezone=tz_name)
        tz = UTC
    local_now = now.astimezone(tz)
    return datetime.combine(local_now.date(), time.min, tzinfo=tz)


def _is_available_status(status: str, custom: CustomStatus | None) -> bool:
    """A presence-event segment counts toward "available" time unless it is
    literally `offline`, or resolves to a catalogued status flagged
    `counts_as_unavailable`. An unresolvable key (unknown, or a catalogue
    outage -- `get()` returns `None` for both) fails open to "available",
    matching `custom_status.py`'s own "no extra information" contract.
    """
    if status == "offline":
        return False
    if custom is not None:
        return not custom.counts_as_unavailable
    return True


async def _resolve_status_label(status_store: _StatusCatalogue, key: str) -> tuple[str, str | None]:
    """`(label, color)` for a status key. Falls back to a plain title-cased
    rendering of the raw key with no colour when it isn't a catalogued
    status (a raw native value like "online"/"busy"/"offline" written
    directly by the presence poller, or a catalogue outage) -- never
    invents catalogue data that doesn't exist.
    """
    custom = await status_store.get(key)
    if custom is not None:
        return custom.label, custom.color
    return key.replace("_", " ").title() or key, None


async def _availability_percent(
    events: list[PresenceEvent],
    *,
    now: datetime,
    inbox: dict[str, Any],
    status_store: _StatusCatalogue,
    day_start: datetime,
    carried: PresenceEvent | None,
) -> float | None:
    """Percentage of the working day elapsed so far spent in an "available"
    status (see `_is_available_status`). `None` -- never `0.0` -- ONLY when
    there is no presence history to compute from at all: no events since
    `day_start` AND no prior event to carry in (`carried is None`).

    `carried` is the agent's `latest()` event from the store, supplied by
    the caller. When `events` (today's, from `since(day_start)`) is empty,
    this function falls back to treating `carried` as a single segment
    running the entire elapsed working day (`day_start` to `now`) --
    `since`'s own contract guarantees that if `events` is empty, `carried`
    (when present at all) predates `day_start`, so this is exactly the
    status that was already in effect when today's window opened, which is
    what genuinely happened. This mirrors `time_in_status_since`'s own
    carried-forward fix (task 1) and closes review finding I3, where this
    function returned `null` right next to a nonzero
    `time_in_status_today_minutes` for the same agent -- a blank and a
    number that both claimed to describe the same day and disagreed. An
    agent with events today is unaffected: `carried` is ignored whenever
    `events` is non-empty.

    Each segment (one event to the next event's `at`, the last to `now`,
    clamped to start no earlier than `day_start` so a carried event's
    original -- possibly days-old -- timestamp never leaks working minutes
    from before today) is scored against `working_minutes_between`
    individually, THEN summed -- not the reverse -- so a segment outside
    working hours (e.g. an agent online at 2am) is never credited as
    working-day availability just because its status label says
    "available". Bounded by `min(pct, 100.0)` as a defensive clamp against
    integer-floor rounding across many small segments; the two sides are
    computed with the same calendar so they should already satisfy
    numerator <= denominator without it.
    """
    segments = events if events else ([carried] if carried is not None else [])
    if not segments:
        return None
    working_minutes_today = working_minutes_between(day_start, now, inbox)
    if working_minutes_today <= 0:
        return None
    available_minutes = 0
    for i, event in enumerate(segments):
        segment_start = max(event.at, day_start)
        segment_end = segments[i + 1].at if i + 1 < len(segments) else now
        if segment_end <= segment_start:
            continue
        custom = await status_store.get(event.status)
        if _is_available_status(event.status, custom):
            available_minutes += working_minutes_between(segment_start, segment_end, inbox)
    percent = (available_minutes / working_minutes_today) * 100
    return round(min(percent, 100.0), 1)


def _availability_history(events: list[PresenceEvent]) -> list[dict[str, str]]:
    """See the module docstring's "Availability history" section: transitions
    to/from `offline` ONLY, oldest first -- never a session record."""
    history: list[dict[str, str]] = []
    for event in events:
        if event.status == "offline":
            history.append({"at": event.at.isoformat(), "direction": "went_offline"})
        elif event.previous == "offline":
            history.append({"at": event.at.isoformat(), "direction": "came_online"})
    return history


async def _fetch_inbox_fail_open(
    fetch: Callable[[], dict[str, Any] | None], inbox_id: int
) -> dict[str, Any]:
    """Fail OPEN: an unconfigured or unreadable inbox must not disable the
    dashboard -- mirrors `HandoffTargetResolver._within_business_hours`'s own
    fail-open direction exactly. Returns `{}`, which
    `working_minutes_between` already treats as "no working-hours config,
    plain calendar minutes" -- so "fail open" here literally means "no
    special-casing needed downstream", not a second fallback path.
    """
    if not inbox_id:
        return {}
    try:
        inbox = await asyncio.to_thread(fetch)
    except Exception as e:
        _log.warning("workforce_inbox_hours_fetch_failed", error=str(e))
        return {}
    return inbox if isinstance(inbox, dict) else {}


async def _build_agent_row(
    agent: AgentRecord,
    *,
    now: datetime,
    day_start: datetime,
    inbox: dict[str, Any],
    presence_store: _PresenceLog,
    status_store: _StatusCatalogue,
    open_counts: dict[int, int],
    open_counts_available: bool,
) -> dict[str, Any]:
    latest = await presence_store.latest(agent.id)
    elapsed = await presence_store.elapsed_in_current_status(agent.id, now)
    # No presence history at all -- fall back to the LIVE native Chatwoot
    # status (always available from fetch_agents(), independent of presence
    # history) so the row still shows something real, just not an elapsed
    # duration (see the module docstring's point 2).
    current_key = latest.status if latest is not None else agent.availability_status
    current_label, current_color = await _resolve_status_label(status_store, current_key)

    events_today = await presence_store.since(agent.id, day_start)
    time_today = await presence_store.time_in_status_since(agent.id, day_start, now)
    time_today_minutes = {
        key: round(duration.total_seconds() / 60, 1) for key, duration in time_today.items()
    }
    availability_percent = await _availability_percent(
        events_today,
        now=now,
        inbox=inbox,
        status_store=status_store,
        day_start=day_start,
        carried=latest,
    )

    return {
        "agent_id": agent.id,
        "name": agent.name,
        "email": agent.email,
        "current_status": {
            "key": current_key,
            "label": current_label,
            "color": current_color,
            "elapsed_minutes": (
                round(elapsed.total_seconds() / 60, 1) if elapsed is not None else None
            ),
        },
        "time_in_status_today_minutes": time_today_minutes,
        "availability_percent_of_working_day": availability_percent,
        "availability_history": _availability_history(events_today),
        # null (never a fabricated 0) whenever this poll's tally is empty --
        # see _OPEN_CASE_COUNT_CAVEAT and the module docstring's "Open case
        # counts" section (review finding I4).
        "open_case_count": (open_counts.get(agent.id, 0) if open_counts_available else None),
        # Always null -- see _CASES_CLOSED_TODAY_CAVEAT.
        "cases_closed_today": None,
    }


def build_workforce_router(
    settings: Settings,
    authz_repo: AuthzRepository | None,
    validator: TokenValidator | None,
    *,
    presence_fetcher: _AgentDirectory | None = None,
    presence_store: _PresenceLog | None = None,
    status_store: _StatusCatalogue | None = None,
    inbox_hours_fetcher: Callable[[], dict[str, Any] | None] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> APIRouter:
    """Build the `GET /admin/workforce` router, gated by `workforce.view`.

    Mirrors `build_pic_admin_router`/`build_customer360_router`'s factory
    shape: domain collaborators are optional, keyword-only, and default to
    the real `build_*`/`PresenceFetcher(settings)` constructions -- callers
    (a later wiring wave, and this file's own tests) can override any of
    them without touching Firestore or a real Chatwoot. `authz_repo`/
    `validator` follow `require_permission`'s own `| None` contract: when
    `settings.rbac_enabled` is off, both are unused and the endpoint falls
    back to the shared-secret `x-api-key` check instead.
    """
    router = APIRouter(prefix="/admin", tags=["workforce"])
    view_workforce = require_permission(
        "workforce.view", repo=authz_repo, validator=validator, settings=settings
    )

    fetcher: _AgentDirectory = presence_fetcher or PresenceFetcher(settings)
    presence: _PresenceLog = presence_store or build_presence_event_store(settings)
    statuses: _StatusCatalogue = status_store or build_custom_status_store(settings)
    fetch_hours: Callable[[], dict[str, Any] | None] = inbox_hours_fetcher or (
        lambda: fetch_inbox_hours(settings, settings.chatwoot_inbox_id)
    )
    clock: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))

    @router.get("/workforce", dependencies=[Depends(view_workforce)])
    async def workforce_dashboard() -> dict[str, Any]:
        now = clock()
        try:
            agents = await fetcher.fetch_agents()
        except Exception as e:
            _log.error("workforce_fetch_agents_failed", error=str(e))
            agents = []
        try:
            open_counts = await fetcher.fetch_agent_open_counts()
        except Exception as e:
            _log.error("workforce_fetch_open_counts_failed", error=str(e))
            open_counts = {}
        # fetch_agent_open_counts() is itself fail-open (see presence.py),
        # so it never actually raises here -- an empty dict is the ONLY
        # signal a failure ever produces, and it is indistinguishable from
        # "genuinely nobody has an open conversation right now". Treat
        # non-emptiness as the proof the fetch actually ran (see the module
        # docstring's "Open case counts" section / review finding I4).
        open_counts_available = bool(open_counts)

        inbox = await _fetch_inbox_fail_open(fetch_hours, settings.chatwoot_inbox_id)
        day_start = _local_day_start(now, inbox)

        rows = [
            await _build_agent_row(
                agent,
                now=now,
                day_start=day_start,
                inbox=inbox,
                presence_store=presence,
                status_store=statuses,
                open_counts=open_counts,
                open_counts_available=open_counts_available,
            )
            for agent in agents
        ]

        return {
            "generated_at": now.isoformat(),
            "refresh": {
                "mode": "poll",
                "recommended_interval_seconds": _RECOMMENDED_POLL_SECONDS,
                "note": (
                    "Reads the presence-event store directly on every request "
                    "-- genuinely current, unlike this system's 6-hour batch "
                    "reports. 'Real-time' means polling this endpoint roughly "
                    f"every {_RECOMMENDED_POLL_SECONDS}s; it is NOT a "
                    "streamed/pushed feed."
                ),
            },
            "availability_history_disclaimer": _AVAILABILITY_HISTORY_DISCLAIMER,
            "cases_closed_today_caveat": _CASES_CLOSED_TODAY_CAVEAT,
            "open_case_count_caveat": (None if open_counts_available else _OPEN_CASE_COUNT_CAVEAT),
            "agents": rows,
        }

    return router
