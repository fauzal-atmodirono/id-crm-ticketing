"""Read-side adapter: SELECTs the 8 Bot-Metrics views into DashboardMetrics."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.query_port import (
    BounceRow,
    CsatRow,
    DashboardMetrics,
    FallbackRow,
    MockMetricsQuery,
    NpsRow,
    QualityRow,
    ResolutionRow,
    SpeedRow,
    VolumeRow,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class BigQueryMetricsQuery:
    """Reads the 8 dashboard views. Each SELECT is wrapped in asyncio.to_thread
    so it never blocks the event loop. A missing/empty view yields an empty
    block rather than raising — the dashboard degrades gracefully."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._prefix = f"{settings.bigquery_project_id}.{settings.bigquery_dataset}"
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    def _rows(self, view: str) -> list[dict[str, Any]]:
        try:
            job = self._client.query(f"SELECT * FROM `{self._prefix}.{view}`")  # noqa: S608
            return [dict(r) for r in job.result()]
        except Exception as e:  # a single bad view must not blank the whole page
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return []

    def _fetch_sync(self) -> DashboardMetrics:
        return DashboardMetrics(
            volume=[VolumeRow(**r) for r in self._rows("v_volume_by_month_channel")],
            resolution=[ResolutionRow(**r) for r in self._rows("v_resolution_split")],
            csat=[CsatRow(**r) for r in self._rows("v_csat")],
            nps=[NpsRow(**r) for r in self._rows("v_nps")],
            speed=[SpeedRow(**r) for r in self._rows("v_speed_of_response")],
            fallback=[FallbackRow(**r) for r in self._rows("v_fallback_rate")],
            bounce=[BounceRow(**r) for r in self._rows("v_bounce_rate")],
            quality=[QualityRow(**r) for r in self._rows("v_quality")],
        )

    async def fetch_dashboard(self) -> DashboardMetrics:
        return await asyncio.to_thread(self._fetch_sync)


def build_metrics_query_port(settings: Settings) -> MetricsQueryPort:
    """Pick the read-side implementation from settings (reuses metrics_provider)."""
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryMetricsQuery(settings)
        except Exception as e:  # never let init crash the app
            _log.error("metrics_query_init_failed_falling_back_to_mock", error=str(e))
            return MockMetricsQuery()
    return MockMetricsQuery()
