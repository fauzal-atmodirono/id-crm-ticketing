"""In-app scheduler that periodically runs the Phase-1 Zendesk -> BigQuery sync."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from chatbot.features.metrics.anomaly import flag_anomalies
from chatbot.features.metrics.export import render_pdf, render_xlsx
from chatbot.features.metrics.sync import ensure_views, run_sync

if TYPE_CHECKING:
    from chatbot.features.metrics.email_port import EmailReportPort
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

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


def run_report_job(
    settings: Settings,
    query_port: MetricsQueryPort,
    email_port: EmailReportPort,
) -> None:
    """Render + email the metrics report and any anomaly alert. Never raises."""
    try:
        recipients = settings.report_recipient_list()
        metrics = asyncio.run(query_port.fetch_dashboard())
        if recipients:
            email_port.send_report(
                recipients,
                "Bot Metrics Report",
                "Attached: the latest bot metrics (xlsx + pdf).",
                [
                    ("bot-metrics.xlsx", render_xlsx(metrics), _XLSX_MIME),
                    ("bot-metrics.pdf", render_pdf(metrics), "application/pdf"),
                ],
            )
        rows = asyncio.run(query_port.fetch_anomalies())
        flagged = flag_anomalies(rows, settings.anomaly_zscore_k, settings.anomaly_min_baseline)
        if flagged and recipients:
            lines = "\n".join(
                f"- {a.channel}: {a.current_volume} (baseline {a.baseline_mean:.0f}, z={a.z_score:.1f})"
                for a in flagged
            )
            email_port.send_report(
                recipients, "⚠️ Channel anomaly detected", f"Anomalies:\n{lines}", []
            )
    except Exception as e:  # a scheduled report must never crash the app
        _log.error("metrics_report_job_failed", error=str(e))


def start_report_scheduler(
    settings: Settings,
    query_port: MetricsQueryPort,
    email_port: EmailReportPort,
    *,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the report scheduler when enabled; else return None."""
    if not settings.report_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_report_job(settings, query_port, email_port))
    sched.add_job(
        run,
        trigger="interval",
        hours=settings.report_interval_hours,
        id="metrics_report",
        replace_existing=True,
    )
    sched.start()
    _log.info("metrics_report_scheduler_started", interval_hours=settings.report_interval_hours)
    return sched
