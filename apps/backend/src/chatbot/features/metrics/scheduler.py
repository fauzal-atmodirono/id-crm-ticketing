"""In-app scheduler that periodically runs the Phase-1 Zendesk -> BigQuery sync."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from chatbot.features.metrics.sync import ensure_views, run_sync

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def start_metrics_scheduler(
    settings: Settings,
    *,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the in-app Zendesk->BQ sync scheduler when enabled; else return None."""
    if not settings.metrics_sync_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_sync_job(settings))
    sched.add_job(
        run,
        trigger="interval",
        hours=settings.metrics_sync_interval_hours,
        id="zendesk_metrics_sync",
        next_run_time=datetime.now(UTC),  # first run shortly after startup
        replace_existing=True,
    )
    sched.start()
    _log.info(
        "metrics_sync_scheduler_started",
        interval_hours=settings.metrics_sync_interval_hours,
    )
    return sched


def run_sync_job(
    settings: Settings,
    *,
    sync: Callable[[Settings], dict[str, int]] | None = None,
    ensure: Callable[[Settings], None] | None = None,
) -> dict[str, int]:
    """Run the Zendesk->BQ sync + refresh views. Best-effort: never raises."""
    try:
        result = (sync or run_sync)(settings)
        (ensure or ensure_views)(settings)
        _log.info("metrics_sync_job_done", **result)
        return result
    except Exception as e:  # a failed scheduled run must never crash the app
        _log.error("metrics_sync_job_failed", error=str(e))
        return {}
