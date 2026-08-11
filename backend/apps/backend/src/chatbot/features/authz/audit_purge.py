"""P13 Task 1 -- Audit log retention purge job.

Purges audit log records older than AUDIT_LOG_RETENTION_DAYS (default 365 days).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


async def run_audit_log_purge_job(
    settings: Settings,
    audit_logs: list[dict[str, Any]],
    delete_func: Any = None,
) -> dict[str, Any]:
    """Purge audit log records older than retention period."""
    if not getattr(settings, "audit_purge_job_enabled", True):
        return {"status": "skipped", "purged_count": 0, "reason": "audit_purge_disabled"}

    retention_days = getattr(settings, "audit_log_retention_days", 365)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    purged_count = 0
    errors = 0

    for entry in audit_logs:
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        if timestamp and timestamp < cutoff:
            if delete_func is not None:
                try:
                    await delete_func(entry["id"])
                    purged_count += 1
                except Exception as exc:
                    _log.error("audit_log_purge_failed", entry_id=entry.get("id"), error=str(exc))
                    errors += 1
            else:
                purged_count += 1

    _log.info("audit_purge_completed", purged_count=purged_count, errors=errors)
    return {"status": "completed", "purged_count": purged_count, "errors": errors}
