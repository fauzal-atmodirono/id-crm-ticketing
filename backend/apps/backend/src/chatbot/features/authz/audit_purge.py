"""P13 -- Audit log retention purge job, and the daily schedule that runs it.

Purges audit-log records older than `AUDIT_LOG_RETENTION_DAYS` (default 2557
days = seven years + two leap days), and only when `AUDIT_PURGE_JOB_ENABLED` is
true. Both are declared `Settings` fields and are read as attributes here, not
via `getattr(settings, ..., default)`.

That distinction is the whole point of the P13 audit finding: while they were
`getattr` defaults, neither name existed in `Settings`, so no environment
variable configured or disabled this job -- and this module's own disable test
passed by setting an attribute pydantic had never declared. Reading them as
attributes means deleting either field breaks this job loudly instead of
silently reinstating a default nobody chose.

**What the wired job actually does today.** `start_audit_purge_job` is called
from `main.py`, so the schedule is real. The DELETE is not: the audit-log port
(`features/chat/adapters/audit_log.py`) has no delete method, and `AuditEntry`
carries no document id, so there is nothing to address a deletion to. A tick
therefore counts the rows past the window, logs the count, and deletes nothing,
reporting `status="dry_run"` -- never a purged count it did not perform. Wiring
the destructive half needs a delete on the port plus a stable row id; both are
recorded as owed. This is the deliberate direction of error: an audit trail that
is longer than the policy is a storage cost, an audit trail deleted by a job
nobody has watched work is unrecoverable evidence.

The first tick is one interval AFTER boot, never at boot -- a crash-looping
container must not be able to drive a deletion pass per restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

if TYPE_CHECKING:
    from chatbot.features.chat.ports import AuditLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

#: A candidate source is handed the cutoff and returns rows at or older than it.
AuditRowSource = Callable[[datetime], Awaitable[list[dict[str, Any]]]]

#: Daily. A job that deletes should not run more often than someone reads its
#: output -- the same reasoning `docs/runbooks/data-retention.md` §6 gives for
#: running the archive script monthly rather than nightly.
PURGE_INTERVAL_HOURS = 24

#: Per-tick read bound. A tick that reaches it logs `audit_purge_scan_limit_reached`
#: so a backlog is visible rather than silently truncated.
DEFAULT_SCAN_LIMIT = 5000


def _as_utc(value: Any) -> datetime | None:
    """Parse a row timestamp into an aware UTC datetime, or None.

    A naive timestamp used to raise `TypeError` on comparison with the aware
    cutoff and take the whole pass down with it; a store that returns naive
    datetimes is a configuration detail, not a reason to stop purging. Naive
    values are read as UTC, which is what every writer in this codebase stores.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def select_expired_audit_entries(
    audit_logs: list[dict[str, Any]], cutoff: datetime
) -> list[dict[str, Any]]:
    """Rows older than the cutoff. The single rule both the purge and the
    dry-run report use, so the count reported can never disagree with the set
    that would be deleted."""
    expired = []
    for entry in audit_logs:
        timestamp = _as_utc(entry.get("timestamp"))
        if timestamp is not None and timestamp < cutoff:
            expired.append(entry)
    return expired


async def run_audit_log_purge_job(
    settings: Settings,
    audit_logs: list[dict[str, Any]],
    delete_func: Any = None,
) -> dict[str, Any]:
    """Purge audit log records older than the retention period.

    With no `delete_func` this is a **dry run**: it reports how many rows are
    past the window and purges nothing. It deliberately does not report those
    rows as purged -- a count of deletions that did not happen is the kind of
    measurement that gets quoted in a compliance review.
    """
    if not settings.audit_purge_job_enabled:
        return {"status": "skipped", "purged_count": 0, "reason": "audit_purge_disabled"}

    retention_days = settings.audit_log_retention_days
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    expired = select_expired_audit_entries(audit_logs, cutoff)

    if delete_func is None:
        _log.info(
            "audit_purge_dry_run",
            eligible_count=len(expired),
            cutoff=cutoff.isoformat(),
            retention_days=retention_days,
        )
        return {
            "status": "dry_run",
            "eligible_count": len(expired),
            "purged_count": 0,
            "reason": "no_audit_deleter_configured",
        }

    purged_count = 0
    errors = 0
    undeletable = 0

    for entry in expired:
        entry_id = entry.get("id")
        if entry_id is None:
            # Nothing to address the delete to. Counted, never guessed at.
            undeletable += 1
            continue
        try:
            await delete_func(entry_id)
            purged_count += 1
        except Exception as exc:
            _log.error("audit_log_purge_failed", entry_id=entry_id, error=str(exc))
            errors += 1

    if undeletable:
        _log.warning(
            "audit_purge_rows_without_id_skipped",
            undeletable_count=undeletable,
            detail="the audit-log port exposes no document id for these rows",
        )

    _log.info(
        "audit_purge_completed",
        purged_count=purged_count,
        errors=errors,
        undeletable_count=undeletable,
        eligible_count=len(expired),
    )
    return {
        "status": "completed",
        "purged_count": purged_count,
        "errors": errors,
        "undeletable_count": undeletable,
        "eligible_count": len(expired),
    }


def build_audit_row_source(
    audit_log: AuditLogPort, *, scan_limit: int = DEFAULT_SCAN_LIMIT
) -> AuditRowSource:
    """Adapt the audit-log port into a candidate source for the purge.

    `list_filtered(to_ts=...)` is the only query the port offers that can select
    by age. Entries carry no document id, so every row this yields has
    ``id=None`` and is therefore undeletable by design -- which is exactly what
    the tick reports rather than papering over.
    """

    async def _source(cutoff: datetime) -> list[dict[str, Any]]:
        entries = await audit_log.list_filtered(to_ts=cutoff.isoformat(), limit=scan_limit)
        if len(entries) >= scan_limit:
            _log.warning(
                "audit_purge_scan_limit_reached",
                scan_limit=scan_limit,
                detail="more rows are past retention than one tick reads",
            )
        return [{"id": getattr(e, "id", None), "timestamp": e.at} for e in entries]

    return _source


async def run_audit_purge_pass(
    settings: Settings,
    *,
    source: AuditRowSource | None = None,
    delete_func: Any = None,
) -> dict[str, Any]:
    """One scheduled pass: fetch candidates, then purge (or dry-run) them."""
    if not settings.audit_purge_job_enabled:
        return {"status": "skipped", "purged_count": 0, "reason": "audit_purge_disabled"}

    if source is None:
        _log.warning(
            "audit_purge_not_executable",
            reason="no_audit_row_source_configured",
            detail="the schedule is running but has nothing to read rows from",
        )
        # Counts are None, not 0: nothing was measured. A 0 here would read as
        # "no rows are past retention", which is a claim this pass cannot make.
        return {
            "status": "not_executable",
            "reason": "no_audit_row_source_configured",
            "eligible_count": None,
            "purged_count": None,
        }

    cutoff = datetime.now(UTC) - timedelta(days=settings.audit_log_retention_days)
    rows = await source(cutoff)
    return await run_audit_log_purge_job(settings, rows, delete_func=delete_func)


def run_audit_purge_tick(
    settings: Settings,
    *,
    source: AuditRowSource | None = None,
    delete_func: Any = None,
) -> dict[str, Any]:
    """The sync callable APScheduler runs. Best-effort: never raises."""
    try:
        return asyncio.run(run_audit_purge_pass(settings, source=source, delete_func=delete_func))
    except Exception as exc:  # a failed scheduled run must never crash the app
        _log.error("audit_purge_job_failed", error=str(exc))
        return {"status": "failed", "purged_count": None, "error": str(exc)}


def start_audit_purge_job(
    settings: Settings,
    *,
    source: AuditRowSource | None = None,
    delete_func: Any = None,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the daily audit-purge tick when enabled; else return None.

    Same shape as `features/metrics/scheduler.py::start_metrics_scheduler`: no
    `BackgroundScheduler` is even constructed when the flag is off, so a tenant
    that has not opted in is byte-identical to before this existed. Unlike the
    metrics scheduler there is no `next_run_time=now`, deliberately -- see the
    module docstring.
    """
    if not settings.audit_purge_job_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_audit_purge_tick(settings, source=source, delete_func=delete_func))
    sched.add_job(
        run,
        trigger="interval",
        hours=PURGE_INTERVAL_HOURS,
        id="audit_log_purge",
        replace_existing=True,
    )
    sched.start()
    _log.info(
        "audit_purge_scheduler_started",
        interval_hours=PURGE_INTERVAL_HOURS,
        retention_days=settings.audit_log_retention_days,
        has_source=source is not None,
        deletes_enabled=delete_func is not None,
    )
    return sched
