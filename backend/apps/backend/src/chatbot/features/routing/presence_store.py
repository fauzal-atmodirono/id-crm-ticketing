"""P6 task 1 -- the presence-event store.

Every named-status feature in P6 (durations, threshold alerts, the workforce
dashboard, After-Call-Work, fair-share assignment) reads this one primitive:
an append-only log of "agent X's status changed to Y" events. Nothing here
mutates a status in place -- an agent's current status is always *derived*,
by asking for the latest event, never stored as a field that something else
can quietly overwrite.

Two behaviours are load-bearing:

**No events is not zero elapsed time.** `latest()` and
`elapsed_in_current_status()` both return `None` for an agent who has never
had a presence event. A dashboard rendering that as "0 min in Available"
would be asserting a transition that never happened.

**Append-only, with one narrow, named exception.** There is no
`update`/`set_status` method -- once written, `status`/`at`/`previous` never
change, which is what makes the event log trustworthy as history. The single
exception is `stamp_alert`, which patches only the `alerts_sent` marker on
the latest event so a threshold alert (task 4) does not re-fire every poll
for the same continuous unavailable period. It cannot touch `status`, `at`,
or `previous`, and it takes the event the caller actually decided about
(`expected_event`) as an identity guard: if a new transition has landed
between the caller's decision and this call, the stamp is a no-op (logged),
never a mis-attributed write onto the new period.

**`latest()` and `stamp_alert()` are bounded, not full scans.** Both are on
the routing hot path -- fair-share selection (`RoutingService._is_routable`)
calls `latest()` once per candidate agent on every `pick_agent` call -- so
their cost cannot grow with how many presence events an agent has ever had.
Both use `order_by("at", DESCENDING).limit(...)` instead of streaming every
document for the agent and taking the max client-side. See
`PresenceEventStore`'s docstring for why `since()` and `_latest_at_or_before()`
are *not* bounded the same way (a composite index would be required, and
this store does not provision one blind), and for the fact that this bounds
the *read*, not the underlying collection's unbounded growth -- retention is
still nobody's job.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "presence_events"

_LATEST_QUERY_LIMIT = 5
"""How many of an agent's most-recent events `latest()`/`stamp_alert()`
fetch, newest first, before giving up. `1` would suffice if every document
were guaranteed parseable, but `_from_document` deliberately *skips* (logs,
does not raise) a document it can't read rather than taking the whole read
down -- see its own docstring. Fetching a small constant instead of exactly
one lets that skip still resolve to the true latest event without falling
back to streaming the agent's entire history. The important property is
that this is a fixed constant, not proportional to how many events the
agent has ever accumulated -- that is what makes the query O(1) instead of
O(history) as the account ages."""


@dataclass(frozen=True)
class PresenceEvent:
    """One immutable presence transition.

    `status` is a custom-status key (e.g. "lunch", "follow_up") or a native
    Chatwoot status ("online"/"busy"/"offline") -- the store does not
    validate against a status catalog, that belongs to the caller (task 3).
    `source` records who caused the transition: "agent" (self-service),
    "admin" (an operator forced it), "system" (e.g. auto-away), or "poll"
    (a periodic presence check observed it).

    `alerts_sent` exists only so `stamp_alert` has somewhere to record "the
    N-minute threshold alert already fired for this continuous period" --
    see the module docstring for why that is the one sanctioned mutation.
    """

    agent_id: int
    status: str
    at: datetime
    source: str
    previous: str | None
    alerts_sent: frozenset[str] = field(default_factory=frozenset)


def _to_document(event: PresenceEvent) -> dict[str, Any]:
    return {
        "agent_id": event.agent_id,
        "status": event.status,
        "at": event.at,
        "source": event.source,
        "previous": event.previous,
        "alerts_sent": sorted(event.alerts_sent),
    }


def _from_document(doc: Any) -> PresenceEvent | None:
    """Convert a Firestore document snapshot to a `PresenceEvent`.

    Unknown/malformed documents are skipped (logged, not raised) rather than
    taking the whole read down -- the same reasoning as the audit log: a
    document written by a newer build must not 500 an older reader.
    """
    data = doc.to_dict() or {}
    try:
        return PresenceEvent(
            agent_id=int(data["agent_id"]),
            status=str(data["status"]),
            at=data["at"],
            source=str(data.get("source", "system")),
            previous=data.get("previous"),
            alerts_sent=frozenset(data.get("alerts_sent") or []),
        )
    except (KeyError, TypeError, ValueError):
        _log.warning("presence_store_unreadable_document", doc=getattr(doc, "id", None))
        return None


def _same_period(a: PresenceEvent, b: PresenceEvent) -> bool:
    """Whether `a` and `b` identify the same continuous-status period --
    deliberately excluding `alerts_sent`, which `stamp_alert` itself may
    have already mutated between when a caller captured its
    `expected_event` and when it calls back in (the threshold sweeper reads
    `latest()` once and reuses that same object for both its warn and
    escalate checks, stamping the warn key first). Comparing every other
    field is what lets `stamp_alert` tell "this is still the period I
    decided about" apart from "a new transition landed underneath me".
    """
    return (
        a.agent_id == b.agent_id
        and a.status == b.status
        and a.at == b.at
        and a.previous == b.previous
        and a.source == b.source
    )


class PresenceEventStore:
    """Firestore-backed, append-only log of agent presence transitions.

    One document per event (auto-generated id) in the ``presence_events``
    collection. All I/O runs via ``asyncio.to_thread`` so the async FastAPI
    event loop is never blocked, and every read fails open (logged,
    empty/``None`` result) -- a Firestore hiccup must not break presence
    reporting.

    **Query shape differs by method, deliberately.**

    ``latest()`` and ``stamp_alert()`` are bounded server-side:
    ``where("agent_id", "==", agent_id).order_by("at",
    direction=DESCENDING).limit(_LATEST_QUERY_LIMIT)``. A single equality
    filter plus a single ``order_by`` on a *different* field is one of
    Firestore's documented exemptions from composite indexing -- it is
    served entirely by the automatic single-field indexes Firestore
    maintains for every field, so this bound needs nothing provisioned. This
    matters because ``latest()`` is on the routing hot path:
    ``RoutingService._is_routable`` calls it once per candidate agent on
    every ``pick_agent`` call, so its cost must not grow with how many
    presence transitions an agent has accumulated over the account's
    lifetime -- at ~20 transitions/agent/day, a year-old 20-agent account
    would otherwise mean ~140k documents streamed per routing decision.

    ``since()`` and ``_latest_at_or_before()`` are **not** bounded the same
    way and remain a full per-agent equality-filtered scan, sorted/filtered
    client-side, exactly as before this fix. Both need every event *in* (or
    at-or-before) a time range, which is a range filter on ``at`` combined
    with an equality filter on a *different* field (``agent_id``) --
    Firestore requires a **composite** index for that combination, and one
    cannot be provisioned from application code; a query that needs an
    index it doesn't have fails at runtime, not at review time, and a
    routing/reporting read failing silently is worse than the slow read it
    would replace. So this store stays within what a single-field-plus-order
    query supports for the two hot-path methods, and leaves the range-query
    methods as a known, documented follow-up rather than guessing at an
    index. They are called from the workforce dashboard's per-agent "today"
    tile and the threshold-alert sweeper -- both real, but neither is the
    per-routing-decision hot path this fix targets.

    **Bounding the read does not bound the collection.** There is still no
    retention/archival policy for ``presence_events`` and no task in this
    package owns adding one -- the collection grows forever regardless of
    how any individual method queries it. This fix stops routing decisions
    (and alert stamping) from paying for the full history; it does not stop
    the history from growing.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _collection(self) -> firestore.CollectionReference:
        return self._client().collection(_COLLECTION)

    async def append(self, event: PresenceEvent) -> None:
        """Append `event`. There is no update -- see the module docstring."""
        try:
            await asyncio.to_thread(self._collection().add, _to_document(event))
        except Exception as e:
            _log.error("presence_store_append_failed", agent_id=event.agent_id, error=str(e))

    async def since(self, agent_id: int, at: datetime) -> list[PresenceEvent]:
        """Events for `agent_id` at or after `at`, oldest first."""
        try:

            def _query() -> list[PresenceEvent]:
                docs = self._collection().where("agent_id", "==", agent_id).stream()
                events = [e for e in (_from_document(d) for d in docs) if e is not None]
                events = [e for e in events if e.at >= at]
                events.sort(key=lambda e: e.at)
                return events

            return await asyncio.to_thread(_query)
        except Exception as e:
            _log.error("presence_store_since_failed", agent_id=agent_id, error=str(e))
            return []

    async def latest(self, agent_id: int) -> PresenceEvent | None:
        """The most recent event for `agent_id`, or `None` if it has never had one.

        `None` here -- never a synthetic event -- is what lets
        `elapsed_in_current_status` tell "no history" apart from "zero
        seconds in status".

        Bounded server-side (`order_by("at", DESCENDING).limit(_LATEST_QUERY_LIMIT)`)
        rather than streaming every event for the agent and taking the max
        client-side -- see the class docstring's "Query shape" section for
        why this needs no composite index and why it matters (this is the
        read fair-share routing makes once per candidate agent on every
        `pick_agent` call).
        """
        try:

            def _query() -> PresenceEvent | None:
                docs = (
                    self._collection()
                    .where("agent_id", "==", agent_id)
                    .order_by("at", direction=firestore.Query.DESCENDING)
                    .limit(_LATEST_QUERY_LIMIT)
                    .stream()
                )
                for doc in docs:
                    event = _from_document(doc)
                    if event is not None:
                        return event
                return None

            return await asyncio.to_thread(_query)
        except Exception as e:
            _log.error("presence_store_latest_failed", agent_id=agent_id, error=str(e))
            return None

    async def elapsed_in_current_status(self, agent_id: int, now: datetime) -> timedelta | None:
        """How long `agent_id` has been in its current (latest) status as of `now`.

        Computed fresh from `latest()` every call -- nothing here is a stored
        duration that could drift from the event log. Returns `None`, not a
        zero `timedelta`, when the agent has no events: those are different
        claims, and a zero would assert a transition that never happened.
        """
        latest_event = await self.latest(agent_id)
        if latest_event is None:
            return None
        return now - latest_event.at

    async def _latest_at_or_before(self, agent_id: int, at: datetime) -> PresenceEvent | None:
        """The latest event for `agent_id` with `at` at or before the given
        instant, or `None` if there is none.

        Used only by `time_in_status_since` to find the status the agent was
        *already* in when a reporting window opens -- `since()` filters
        `at >= window_start`, which on its own would silently drop the event
        that established the carried-forward status and leave that segment
        uncounted. Fails open (logged, `None`) the same way every other read
        on this store does.
        """
        try:

            def _query() -> PresenceEvent | None:
                docs = self._collection().where("agent_id", "==", agent_id).stream()
                events = [e for e in (_from_document(d) for d in docs) if e is not None]
                events = [e for e in events if e.at <= at]
                if not events:
                    return None
                return max(events, key=lambda e: e.at)

            return await asyncio.to_thread(_query)
        except Exception as e:
            _log.error("presence_store_latest_at_or_before_failed", agent_id=agent_id, error=str(e))
            return None

    async def time_in_status_since(
        self, agent_id: int, since: datetime, now: datetime
    ) -> dict[str, timedelta]:
        """`{status: time spent}` for `agent_id` from `since` through `now`.

        Derived purely from the event list, nothing stored: each event opens
        a segment that runs until the next event's `at`, or, for the last
        event, until `now`. Consumed by the workforce dashboard (task 9) for
        "today's time per status". An agent with no events at all -- in or
        before the window -- yields `{}`, consistent with
        `latest`/`elapsed_in_current_status` never fabricating a duration.

        The window's *first* segment needs special handling: `since()` only
        returns events with `at >= since`, so the event that put the agent
        into whatever status it was already in when the window opened is not
        among them, and no segment would ever be opened for it -- an agent
        who has been `available` since 08:55 would report `{}`, not one hour
        forty-five, for a 09:00-10:45 window. `_latest_at_or_before` finds
        that carried-forward status (if any) and, when the in-window events
        don't already start exactly at `since`, credits it for the gap from
        `since` up to the first in-window event's `at` (or all the way to
        `now` if there are no in-window events at all).
        """
        events = await self.since(agent_id, since)
        totals: dict[str, timedelta] = {}

        if not events or events[0].at > since:
            carried = await self._latest_at_or_before(agent_id, since)
            if carried is not None:
                end = events[0].at if events else now
                duration = end - since
                if duration.total_seconds() > 0:
                    totals[carried.status] = totals.get(carried.status, timedelta()) + duration

        for i, event in enumerate(events):
            end = events[i + 1].at if i + 1 < len(events) else now
            duration = end - event.at
            if duration.total_seconds() <= 0:
                continue
            totals[event.status] = totals.get(event.status, timedelta()) + duration
        return totals

    async def stamp_alert(
        self, agent_id: int, alert_key: str, expected_event: PresenceEvent
    ) -> None:
        """Record that the `alert_key` threshold alert fired for the agent's
        current (latest) continuous-status period -- but only if that period
        is still `expected_event`.

        This is the one sanctioned exception to append-only -- see the module
        docstring. It patches *only* the `alerts_sent` field of the latest
        event's document and nothing else: it cannot rewrite `status`, `at`,
        or `previous`, because it never touches them. There is no general
        `update`/`set_status` method on this store; this method exists
        solely so task 4's threshold alert does not re-fire on every poll of
        the same unavailable period.

        `expected_event` closes a race the original implementation had: a
        caller decides an alert is due by reading `latest()`, sends the
        alert, then calls back in here to stamp it. If a new presence event
        (e.g. from the poller) lands in between, resolving "the latest
        event" *again* at write time would patch the **new** event instead
        of the one the alert was actually about -- silently losing the old
        period's stamp (so it re-fires) while wrongly pre-arming the new
        period's alert (so its own genuine alert never fires). The caller
        must pass the exact event it decided about; if the store's current
        latest event no longer matches it (by `_same_period` -- every field
        except `alerts_sent`, which this method itself is free to have
        already mutated), the stamp is skipped and logged rather than
        applied to the wrong period.

        Like `latest()`, this resolves "the latest event" via a bounded,
        server-side ``order_by("at", DESCENDING).limit(_LATEST_QUERY_LIMIT)``
        query rather than streaming and `max()`-ing the agent's entire
        history -- the threshold sweeper calls this once per alert per
        agent per poll, so it has the same growth problem `latest()` does if
        left unbounded.
        """
        try:

            def _run() -> None:
                docs = list(
                    self._collection()
                    .where("agent_id", "==", agent_id)
                    .order_by("at", direction=firestore.Query.DESCENDING)
                    .limit(_LATEST_QUERY_LIMIT)
                    .stream()
                )
                candidates: list[tuple[Any, PresenceEvent]] = [
                    (d, e) for d in docs if (e := _from_document(d)) is not None
                ]
                if not candidates:
                    return
                doc, latest_event = candidates[0]  # already ordered newest-first
                if not _same_period(latest_event, expected_event):
                    _log.info(
                        "presence_store_stamp_alert_skipped_stale_event",
                        agent_id=agent_id,
                        alert_key=alert_key,
                    )
                    return
                data = doc.to_dict() or {}
                alerts = set(data.get("alerts_sent") or [])
                alerts.add(alert_key)
                doc.reference.update({"alerts_sent": sorted(alerts)})

            await asyncio.to_thread(_run)
        except Exception as e:
            _log.error("presence_store_stamp_alert_failed", agent_id=agent_id, error=str(e))


def build_presence_event_store(settings: Settings) -> PresenceEventStore:
    return PresenceEventStore(settings)
