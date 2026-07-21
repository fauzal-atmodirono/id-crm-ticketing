"""Read-side adapter: SELECTs the 8 Bot-Metrics views into DashboardMetrics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.query_port import (
    AnomalyRow,
    BounceRow,
    CallCentreMetrics,
    CaseLifecycleRow,
    ComplaintTypeRow,
    CsatRow,
    DashboardMetrics,
    DepartmentsMetrics,
    DeptPicRow,
    FallbackRow,
    FirstResponseRow,
    LifecycleMetrics,
    MockMetricsQuery,
    NpsByAgentRow,
    NpsRow,
    PeakHourRow,
    QualityRow,
    ReopenRow,
    ResolutionRow,
    ResolutionTimeRow,
    SlaAchievementRow,
    SpeedRow,
    StateTrendRow,
    TasksPerAgentRow,
    VolumeRow,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_T = TypeVar("_T")


class BigQueryMetricsQuery:
    """Reads the 8 dashboard views. Each SELECT is wrapped in asyncio.to_thread
    so it never blocks the event loop. A missing/empty view yields an empty
    block rather than raising — the dashboard degrades gracefully."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._prefix = f"{settings.bigquery_project_id}.{settings.bigquery_dataset}"
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    def _block(self, view: str, row_type: Callable[..., _T]) -> list[_T]:
        try:
            job = self._client.query(f"SELECT * FROM `{self._prefix}.{view}`")  # noqa: S608
            return [row_type(**dict(r)) for r in job.result()]
        except (
            Exception
        ) as e:  # one bad/drifted view degrades to an empty block, never 500s the page
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return []

    def _fetch_sync(self) -> DashboardMetrics:
        return DashboardMetrics(
            volume=self._block("v_volume_by_month_channel", VolumeRow),
            resolution=self._block("v_resolution_split", ResolutionRow),
            csat=self._block("v_csat", CsatRow),
            nps=self._block("v_nps", NpsRow),
            speed=self._block("v_speed_of_response", SpeedRow),
            fallback=self._block("v_fallback_rate", FallbackRow),
            bounce=self._block("v_bounce_rate", BounceRow),
            quality=self._block("v_quality", QualityRow),
        )

    async def fetch_dashboard(self) -> DashboardMetrics:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_anomalies_sync(self) -> list[AnomalyRow]:
        return self._block("v_channel_anomaly", AnomalyRow)

    async def fetch_anomalies(self) -> list[AnomalyRow]:
        return await asyncio.to_thread(self._fetch_anomalies_sync)

    def _fetch_departments_sync(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=self._block("v_dept_pic_performance", DeptPicRow),
            reopen=self._block("v_reopen_rate", ReopenRow),
        )

    async def fetch_departments(self) -> DepartmentsMetrics:
        return await asyncio.to_thread(self._fetch_departments_sync)

    def _fetch_callcenter_sync(self) -> CallCentreMetrics:
        return CallCentreMetrics(
            sla=self._block("v_sla_achievement", SlaAchievementRow),
            tasks_per_agent=self._block("v_tasks_per_agent", TasksPerAgentRow),
            first_response=self._block("v_first_response_by_channel", FirstResponseRow),
            resolution_time=self._block("v_resolution_time", ResolutionTimeRow),
            complaint_types=self._block("v_complaint_type_ranking", ComplaintTypeRow),
            peak_hours=self._block("v_peak_hours", PeakHourRow),
            nps_by_agent=self._block("v_nps_by_agent", NpsByAgentRow),
        )

    async def fetch_callcenter(self) -> CallCentreMetrics:
        return await asyncio.to_thread(self._fetch_callcenter_sync)

    def _fetch_lifecycle_sync(self) -> LifecycleMetrics:
        return LifecycleMetrics(
            cases=self._block("v_case_lifecycle", CaseLifecycleRow),
            state_trend=self._block("v_state_trend", StateTrendRow),
        )

    async def fetch_lifecycle(self) -> LifecycleMetrics:
        return await asyncio.to_thread(self._fetch_lifecycle_sync)


def build_metrics_query_port(settings: Settings) -> MetricsQueryPort:
    """Pick the read-side implementation from settings (reuses metrics_provider)."""
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryMetricsQuery(settings)
        except Exception as e:  # never let init crash the app
            _log.error("metrics_query_init_failed_falling_back_to_mock", error=str(e))
            return MockMetricsQuery()
    return MockMetricsQuery()
