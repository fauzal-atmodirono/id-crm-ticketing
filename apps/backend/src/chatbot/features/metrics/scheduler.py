"""In-app scheduler that periodically runs the Phase-1 Zendesk -> BigQuery sync."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

from chatbot.features.metrics.sync import ensure_views, run_sync

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


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
