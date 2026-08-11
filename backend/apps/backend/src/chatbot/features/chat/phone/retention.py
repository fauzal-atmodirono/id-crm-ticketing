"""P11 Task 8 -- Automated phone recording retention runner.

Enforces PHONE_RECORDING_RETENTION_DAYS purging of call recordings older than the retention window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


async def run_retention_purge_job(
    settings: Settings,
    recordings: list[dict[str, Any]],
    delete_func: Any = None,
) -> dict[str, Any]:
    """Execute retention purge job for recordings older than PHONE_RECORDING_RETENTION_DAYS."""
    if not settings.phone_retention_job_enabled:
        return {"status": "skipped", "purged_count": 0, "reason": "retention_job_disabled"}

    retention_days = getattr(settings, "phone_recording_retention_days", 90)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    purged_count = 0
    errors = 0

    for rec in recordings:
        created_at = rec.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        if created_at and created_at < cutoff and not rec.get("is_deleted"):
            if delete_func is not None:
                try:
                    await delete_func(rec["sid"])
                    rec["is_deleted"] = True
                    rec["recording_url"] = None
                    purged_count += 1
                except Exception as exc:
                    _log.error("retention_delete_failed", sid=rec.get("sid"), error=str(exc))
                    errors += 1
            else:
                rec["is_deleted"] = True
                rec["recording_url"] = None
                purged_count += 1

    _log.info("retention_job_completed", purged_count=purged_count, errors=errors)
    return {"status": "completed", "purged_count": purged_count, "errors": errors}
