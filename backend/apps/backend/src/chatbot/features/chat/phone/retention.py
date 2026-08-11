"""P11 Task 8 -- call-recording retention, and the daily schedule that runs it.

Enforces `PHONE_RECORDING_RETENTION_DAYS` (default 90) over call recordings,
gated on `PHONE_RETENTION_JOB_ENABLED` (default false).

**Read this before enabling it anywhere.** Deleting a customer's call recording
is irreversible, and a retention job that deletes the wrong thing is far worse
than one that has not run yet. So the destructive step requires explicit
configuration that this codebase does not yet contain, and nothing about turning
the flag on can produce a deletion by itself:

* `start_recording_retention_job` schedules a daily tick (from `main.py`), and
  the first tick is one interval AFTER boot, never at boot -- a crash-looping
  container must not be able to drive a deletion pass per restart.
* The tick needs a **candidate source** (something that lists recordings with
  their age) and a **deleter** (something that removes the audio at the
  provider). Neither exists yet: there is no Twilio recording-delete adapter,
  and `recording_router.py`'s registry is an in-process dict nothing in
  production writes. Both are injected parameters, so wiring them later is a
  main.py change, not a rewrite.
* With no deleter the pass is a **dry run**: it reports how many recordings are
  past the window and touches nothing. It deliberately does not mark them
  deleted. Marking a recording `is_deleted` while the audio still exists at the
  provider is the worst available outcome -- the retrieval endpoint would tell an
  agent "deleted under the retention policy" about audio that is still there, so
  the policy would look enforced precisely where it is not.

Consequently, and stated plainly: **the 90-day recording-retention policy is not
in force on any tenant today.** The schedule and the reporting are real; the
deletion is owed, and is recorded as owed in
`docs/analysis/2026-08-09-blocked-work-register.md`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

#: A candidate source is handed the cutoff and returns recordings at or older
#: than it. Each item needs at least `sid` and `created_at`.
RecordingSource = Callable[[datetime], Awaitable[list[dict[str, Any]]]]

#: Daily, for the same reason the archive script runs monthly: a job that deletes
#: should not run more often than someone reads its output.
RETENTION_INTERVAL_HOURS = 24


def _as_utc(value: Any) -> datetime | None:
    """Parse a recording timestamp into an aware UTC datetime, or None.

    A naive timestamp used to raise `TypeError` on comparison with the aware
    cutoff, which would abandon the whole pass part-way through -- with some
    recordings already deleted and no report of where it stopped. Naive values
    are read as UTC, which is what every writer here stores.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def select_expired_recordings(
    recordings: list[dict[str, Any]], cutoff: datetime
) -> list[dict[str, Any]]:
    """Recordings older than the cutoff and not already deleted.

    The single rule both the deletion and the dry-run report use, so the count
    reported can never disagree with the set that would actually be deleted.
    A recording with an unparseable or missing timestamp is NOT selected: we do
    not know its age, and "unknown age" must never resolve to "delete it".
    """
    expired = []
    for rec in recordings:
        if rec.get("is_deleted"):
            continue
        created_at = _as_utc(rec.get("created_at"))
        if created_at is not None and created_at < cutoff:
            expired.append(rec)
    return expired


async def run_retention_purge_job(
    settings: Settings,
    recordings: list[dict[str, Any]],
    delete_func: Any = None,
) -> dict[str, Any]:
    """Purge recordings older than `PHONE_RECORDING_RETENTION_DAYS`.

    With no `delete_func` this is a **dry run**: it reports how many recordings
    are past the window, mutates nothing, and deletes nothing. See the module
    docstring for why marking them deleted without a deleter is the one outcome
    to avoid.
    """
    if not settings.phone_retention_job_enabled:
        return {"status": "skipped", "purged_count": 0, "reason": "retention_job_disabled"}

    retention_days = settings.phone_recording_retention_days
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    expired = select_expired_recordings(recordings, cutoff)

    if delete_func is None:
        _log.info(
            "recording_retention_dry_run",
            eligible_count=len(expired),
            cutoff=cutoff.isoformat(),
            retention_days=retention_days,
        )
        return {
            "status": "dry_run",
            "eligible_count": len(expired),
            "purged_count": 0,
            "reason": "no_recording_deleter_configured",
        }

    purged_count = 0
    errors = 0

    for rec in expired:
        try:
            await delete_func(rec["sid"])
        except Exception as exc:
            # Left untouched on failure: the audio is still there, so the stored
            # attributes must keep saying so.
            _log.error("retention_delete_failed", sid=rec.get("sid"), error=str(exc))
            errors += 1
            continue
        # Only after the provider confirmed the delete. This is what keeps
        # "deleted under the retention policy" a true statement.
        rec["is_deleted"] = True
        rec["recording_url"] = None
        purged_count += 1

    _log.info(
        "retention_job_completed",
        purged_count=purged_count,
        errors=errors,
        eligible_count=len(expired),
    )
    return {
        "status": "completed",
        "purged_count": purged_count,
        "errors": errors,
        "eligible_count": len(expired),
    }


async def run_retention_pass(
    settings: Settings,
    *,
    source: RecordingSource | None = None,
    delete_func: Any = None,
) -> dict[str, Any]:
    """One scheduled pass: fetch candidates, then purge (or dry-run) them."""
    if not settings.phone_retention_job_enabled:
        return {"status": "skipped", "purged_count": 0, "reason": "retention_job_disabled"}

    if source is None:
        _log.warning(
            "recording_retention_not_executable",
            reason="no_recording_source_configured",
            detail=(
                "the schedule is running but nothing lists recordings due for "
                "purge, so PHONE_RECORDING_RETENTION_DAYS enforces nothing"
            ),
        )
        # Counts are None, not 0: nothing was measured. A 0 would read as "no
        # recordings are past retention", which this pass cannot know.
        return {
            "status": "not_executable",
            "reason": "no_recording_source_configured",
            "eligible_count": None,
            "purged_count": None,
        }

    cutoff = datetime.now(UTC) - timedelta(days=settings.phone_recording_retention_days)
    recordings = await source(cutoff)
    return await run_retention_purge_job(settings, recordings, delete_func=delete_func)


def run_retention_tick(
    settings: Settings,
    *,
    source: RecordingSource | None = None,
    delete_func: Any = None,
) -> dict[str, Any]:
    """The sync callable APScheduler runs. Best-effort: never raises."""
    try:
        return asyncio.run(run_retention_pass(settings, source=source, delete_func=delete_func))
    except Exception as exc:  # a failed scheduled run must never crash the app
        _log.error("recording_retention_job_failed", error=str(exc))
        return {"status": "failed", "purged_count": None, "error": str(exc)}


def start_recording_retention_job(
    settings: Settings,
    *,
    source: RecordingSource | None = None,
    delete_func: Any = None,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the daily recording-retention tick when enabled; else return None.

    Same shape as `features/metrics/scheduler.py::start_metrics_scheduler`: with
    the flag off no `BackgroundScheduler` is even constructed, so a tenant that
    has not opted in is byte-identical to before this existed.
    """
    if not settings.phone_retention_job_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_retention_tick(settings, source=source, delete_func=delete_func))
    sched.add_job(
        run,
        trigger="interval",
        hours=RETENTION_INTERVAL_HOURS,
        id="phone_recording_retention",
        replace_existing=True,
    )
    sched.start()
    if delete_func is None:
        _log.warning(
            "recording_retention_deletes_not_configured",
            detail=(
                "PHONE_RETENTION_JOB_ENABLED is on and the tick is scheduled, but "
                "no recording deleter is wired, so nothing will be deleted and no "
                "recording will be marked deleted. The declared retention policy "
                "is not in force."
            ),
        )
    _log.info(
        "recording_retention_scheduler_started",
        interval_hours=RETENTION_INTERVAL_HOURS,
        retention_days=settings.phone_recording_retention_days,
        has_source=source is not None,
        deletes_enabled=delete_func is not None,
    )
    return sched
