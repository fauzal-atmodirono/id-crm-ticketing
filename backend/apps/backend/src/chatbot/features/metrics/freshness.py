"""The shared freshness contract: what a number is as-of, and where it came from.

P9 task 5. Three surfaces in this product are described as real-time and are
not: the executive dashboard (§2.2.3), the anomaly page (§4.79) and reporting
(§4.81). All three read BigQuery, which is fed by a Chatwoot->BQ batch sync on
`METRICS_SYNC_INTERVAL_HOURS` (default 6).

**The point of this module is that a difference between a dashboard figure and
the live CRM is EXPECTED.** "The dashboard says 41 and the CRM says 44" is a
credibility problem in a reconciliation meeting and a six-hour sync interval in
a footnote; it is the same fact either way, and the only thing that decides
which one it becomes is whether the page said so before anyone asked. This makes
the size of that difference visible instead of leaving it to be reported as a
bug.

Three rules the shape here exists to enforce:

**1. Freshness is a property of the surface's OWN data source.** There is no
single answer for the product. A helper that stamped one value everywhere would
label a live surface as batch or a batch surface as live, and either is worse
than nothing -- so every surface names its own source, and `SURFACES` below is
the inventory of which is which.

**2. `as_of` is never `now` unless the data really is from now.** A six-hour-old
figure stamped with the request time looks current, which is precisely the
misrepresentation this task exists to remove. `as_of` is the last completed
sync, and when that is not known it is `None` with
`as_of_status="unknown"` -- a blank on screen. An as-of stamp that is actually
`now` on stale data converts an honest uncertainty into a false assurance, so
`now` is never substituted for a missing measurement. (P5 onward: a blank is a
statement about instrumentation, a value is a claim.)

**3. "Unknown" and "continuous" are different, and neither is "measured".**
A live push has no single as-of -- it is current as events arrive -- and saying
"unknown" there would understate it. A browser-side 60-second poll has an as-of
the server genuinely cannot observe, and saying "now" there would overstate it.
Three statuses, so a page can render the right thing for each.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

# The `source` vocabulary the design fixes, and the one a consumer switches on.
#
# `batch_6h` names the CLASS of source with its default interval baked into the
# name; a tenant that sets METRICS_SYNC_INTERVAL_HOURS=12 still reports
# `batch_6h` because the token set is the contract, and the honest number is on
# `max_staleness_seconds` and spelled out in `basis`. Read the interval, not the
# token, when you need the actual bound.
SOURCE_LIVE_STREAM = "live_stream"
SOURCE_POLL_60S = "poll_60s"
SOURCE_BATCH = "batch_6h"

FreshnessSource = Literal["live_stream", "poll_60s", "batch_6h"]

# How `as_of` should be read. Deliberately three values -- see rule 3 above.
AS_OF_MEASURED = "measured"  # a real observed timestamp
AS_OF_UNKNOWN = "unknown"  # not determinable here; render a blank, never `now`
AS_OF_CONTINUOUS = "continuous"  # live push: no single as-of applies

_POLL_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class Freshness:
    """One surface's freshness statement.

    `stale` is only ever `True` on a MEASURED as-of that is older than
    `max_staleness_seconds`. An unknown as-of is not reported as stale: "we
    cannot tell" and "it is out of date" are different claims, and asserting the
    second from the first is the same substitution rule 2 forbids.
    """

    source: str
    as_of: datetime | None
    as_of_status: str
    basis: str
    max_staleness_seconds: int | None

    @property
    def stale(self) -> bool:
        if self.as_of is None or self.as_of_status != AS_OF_MEASURED:
            return False
        if self.max_staleness_seconds is None:
            return False
        now = datetime.now(UTC)
        as_of = self.as_of if self.as_of.tzinfo else self.as_of.replace(tzinfo=UTC)
        return (now - as_of).total_seconds() > self.max_staleness_seconds

    def as_payload(self) -> dict[str, Any]:
        return {
            "as_of_status": self.as_of_status,
            "basis": self.basis,
            "max_staleness_seconds": self.max_staleness_seconds,
            "stale": self.stale,
        }


# ---------------------------------------------------------------------------
# When the batch sync last finished
# ---------------------------------------------------------------------------
#
# Process-local, and honest about it. There is no persisted last-sync record in
# this system: `run_sync` WRITE_TRUNCATEs the conversations table and stamps a
# `synced_at` column on every row, but nothing reads it back. So after a restart,
# or on a replica that never ran the scheduler, the answer here is `None` and the
# page shows a blank rather than the request time. That is the correct behaviour
# for this contract, not a gap being papered over -- but the durable version is
# `SELECT MAX(synced_at) FROM conversations`, which is what should replace this
# once there is a reason to spend a query on it.
#
# A plain module global: a single rebinding of one name, which is atomic under
# the GIL. The scheduler writes it from one background thread and request
# handlers read it; there is nothing to tear.
_last_sync_completed_at: datetime | None = None


def record_sync_completed(when: datetime | None = None) -> None:
    """Called by `scheduler.run_sync_job` after a sync that actually succeeded.

    Only on success, deliberately. Recording a failed run would move `as_of`
    forward while the data underneath it did not, which is the freshness lie in
    its purest form: the number gets older and the stamp gets newer.
    """
    global _last_sync_completed_at  # noqa: PLW0603 -- see the note above this block
    _last_sync_completed_at = when or datetime.now(UTC)


def last_sync_completed_at() -> datetime | None:
    return _last_sync_completed_at


def reset_sync_clock() -> None:
    """Test seam. Nothing in the app calls this."""
    global _last_sync_completed_at  # noqa: PLW0603 -- see the note above this block
    _last_sync_completed_at = None


# ---------------------------------------------------------------------------
# The three constructors, one per class of source
# ---------------------------------------------------------------------------


def batch_freshness(settings: Settings, *, last_sync_at: datetime | None = None) -> Freshness:
    """Anything read out of BigQuery: the dashboard, the reports, the anomaly
    figures.

    `as_of` is the last completed sync -- NOT the request time. When it is not
    known (this process has not run a sync yet), the status is `unknown` and
    `as_of` is None.
    """
    interval_hours = max(int(getattr(settings, "metrics_sync_interval_hours", 6) or 0), 0)
    as_of = last_sync_at if last_sync_at is not None else last_sync_completed_at()
    if as_of is None:
        return Freshness(
            source=SOURCE_BATCH,
            as_of=None,
            as_of_status=AS_OF_UNKNOWN,
            basis=(
                f"BigQuery-backed, fed by the Chatwoot sync every {interval_hours}h. "
                f"The time of the last completed sync is not known to this process "
                f"(it records the sync it runs, and has not run one since starting), "
                f"so no as-of is shown. It is deliberately left blank rather than "
                f"filled in with the current time, which would make figures up to "
                f"{interval_hours}h old look current."
            ),
            max_staleness_seconds=interval_hours * 3600 if interval_hours else None,
        )
    return Freshness(
        source=SOURCE_BATCH,
        as_of=as_of,
        as_of_status=AS_OF_MEASURED,
        basis=(
            f"BigQuery-backed, fed by the Chatwoot sync every {interval_hours}h. "
            f"These figures are as-of the last completed sync, not as-of now: a "
            f"difference against the live CRM of up to one sync interval is expected "
            f"rather than an error."
        ),
        max_staleness_seconds=interval_hours * 3600 if interval_hours else None,
    )


def live_stream_freshness() -> Freshness:
    """Alerting on the Chatwoot ActionCable stream: pushed as it happens.

    `as_of` is None with status `continuous`, not `now`. The stream is
    browser-to-Chatwoot; this service never sees an event on it and so has no
    timestamp of its own to report. "Current as events arrive" is the true
    statement, and it is not the same statement as a measured timestamp.
    """
    return Freshness(
        source=SOURCE_LIVE_STREAM,
        as_of=None,
        as_of_status=AS_OF_CONTINUOUS,
        basis=(
            "Pushed live over the Chatwoot event stream, so there is no single "
            "as-of: the surface is current as events arrive. The stream runs "
            "between the browser and Chatwoot, so this service cannot report a "
            "last-event time of its own."
        ),
        max_staleness_seconds=0,
    )


def poll_freshness(interval_seconds: int = _POLL_INTERVAL_SECONDS) -> Freshness:
    """The existing 60-second poll -- the `my-tasks` app, and the documented
    fallback when the event stream is unavailable.

    Bounded but unobserved: the poll runs in the browser, so the server knows
    the bound (`interval_seconds`) and not the actual last fetch. Status is
    `unknown` for that reason, with the bound stated.
    """
    return Freshness(
        source=SOURCE_POLL_60S,
        as_of=None,
        as_of_status=AS_OF_UNKNOWN,
        basis=(
            f"Polled every {interval_seconds}s in the browser, so figures are at "
            f"most {interval_seconds}s old. The poll is client-side, so the exact "
            f"time of the last fetch is not known to this service and is left "
            f"blank rather than guessed."
        ),
        max_staleness_seconds=interval_seconds,
    )


# ---------------------------------------------------------------------------
# The inventory: which surface is which
# ---------------------------------------------------------------------------
#
# One list, in one place, so "is the anomaly page live?" has a single answer
# that a page and an endpoint cannot disagree about. Note `anomaly_hourly`:
# §3.5 calls it a real-time surface, and its NOTIFICATION genuinely is -- but
# its figures come out of the same batch warehouse as everything else, so it
# reports `batch_6h`. Stamping it `live_stream` because the page feels live is
# exactly the per-surface lie rule 1 exists to prevent.
SURFACE_DASHBOARD = "dashboard"
SURFACE_REPORTS = "reports"
SURFACE_ANOMALY_HOURLY = "anomaly_hourly"
SURFACE_ALERT_STREAM = "alert_stream"
SURFACE_MY_TASKS = "my_tasks"


def surface_freshness(settings: Settings) -> dict[str, Freshness]:
    """Every surface's own freshness, resolved against the live settings."""
    batch = batch_freshness(settings)
    # The alert surface is live only when the fork's stream subscription is
    # actually enabled. With `inbound_alerts_enabled` off, the alerting a tenant
    # has is still the my-tasks 60-second poll, and reporting it as a live stream
    # would be a claim about software that is not switched on.
    alerts = (
        live_stream_freshness()
        if getattr(settings, "inbound_alerts_enabled", False)
        else poll_freshness()
    )
    return {
        SURFACE_DASHBOARD: batch,
        SURFACE_REPORTS: batch,
        SURFACE_ANOMALY_HOURLY: batch,
        SURFACE_ALERT_STREAM: alerts,
        SURFACE_MY_TASKS: poll_freshness(),
    }


# ---------------------------------------------------------------------------
# Stamping a response
# ---------------------------------------------------------------------------


def stamp_freshness(
    payload: dict[str, Any], freshness: Freshness, *, enabled: bool
) -> dict[str, Any]:
    """Add `as_of`/`source`/`freshness` to a metrics response, or return it
    untouched.

    `enabled` is `DASHBOARD_FRESHNESS_ENABLED`, and off returns the SAME dict
    object, not a copy with the same contents. Every `/metrics/*` route
    serialises a bare dict with no response model, so any key added here appears
    in the payload the deployed SPA already parses; "off is byte-identical to
    today" is the invariant this whole package has held to, and returning the
    input unchanged is the only way to be sure of it.

    `as_of` serialises as an ISO-8601 string, or `null`. Never the request time
    -- see this module's rule 2.
    """
    if not enabled:
        return payload
    return {
        **payload,
        "as_of": freshness.as_of.isoformat() if freshness.as_of else None,
        "source": freshness.source,
        "freshness": freshness.as_payload(),
    }
