"""Read-side adapter: SELECTs the 8 Bot-Metrics views into DashboardMetrics.

Range-aware queries (Package E / Task 2): `_block` takes an optional
`PeriodRange` plus the name of a DATE/TIMESTAMP column on the view to filter
on. `period=None` (the default, and every call site not listed below) emits
the exact same unfiltered `SELECT *` this adapter has always run -- that is
a deliberate regression guard on the whole existing dashboard, not an
oversight. A period is only threaded through for the views that actually
carry a date dimension: `v_volume_by_month_channel` (via the day-grained
`v_volume_daily` sibling, see `_volume_block_for_period`), `v_state_trend`
and `v_volume_by_type_division` (both widened in `bigquery_schema.py` with
an additive `month_start` column, same row grain as before). Every other
view aggregates across all time with no date column at all -- there is
nothing yet for a period to filter (see the Package E spec's G2/G3/G6 gaps)
-- so their fetch_* methods don't take a `period` argument."""

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
    CaseAgingMetrics,
    CaseAgingRow,
    CaseLifecycleRow,
    CategoryByVehicleModelRow,
    ComplaintTypeRow,
    CsatRow,
    DashboardMetrics,
    DealerEscalationMetrics,
    DealerEscalationRow,
    DealerSlowCaseRow,
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
    SlaBucketMetrics,
    SlaBucketRow,
    SpeedRow,
    StateTrendRow,
    TasksPerAgentRow,
    VolumeByTypeDivisionMetrics,
    VolumeByTypeDivisionRow,
    VolumeRow,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.period import PeriodRange
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_T = TypeVar("_T")

# BigQuery FORMAT_DATE elements, keyed by PeriodRange.granularity. Not a
# query parameter -- these are a fixed, code-controlled allowlist (falls
# back to "%Y-%m" for anything unrecognised), never interpolated user input;
# only the range bounds themselves go in as named parameters.
_BUCKET_FORMAT = {
    "day": "%Y-%m-%d",
    "week": "%G-W%V",  # ISO year + ISO week, matches period.bucket_key
    "month": "%Y-%m",
}


class BigQueryMetricsQuery:
    """Reads the 8 dashboard views. Each SELECT is wrapped in asyncio.to_thread
    so it never blocks the event loop. A missing/empty view yields an empty
    block rather than raising — the dashboard degrades gracefully."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._prefix = f"{settings.bigquery_project_id}.{settings.bigquery_dataset}"
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    def _block(
        self,
        view: str,
        row_type: Callable[..., _T],
        *,
        period: PeriodRange | None = None,
        date_column: str | None = None,
    ) -> list[_T]:
        """SELECT a view into `row_type` rows.

        `period=None` (or a view with no `date_column`) runs the exact same
        `SELECT * FROM <view>` this adapter has always run -- byte-identical
        SQL, so today's callers see no change at all. A period *and* a
        `date_column` add a `WHERE <date_column> BETWEEN @start AND @end`
        predicate using BigQuery named parameters -- the range bounds are
        never string-formatted into the query text. If `date_column` names a
        column the live view doesn't have yet (e.g. a widened DDL that
        `ensure_views()` hasn't re-created on this deployment), BigQuery
        rejects the query and this degrades to an empty block, same as any
        other query failure -- never a 500.
        """
        try:
            if period is not None and date_column is not None:
                sql = (
                    f"SELECT * FROM `{self._prefix}.{view}` "  # noqa: S608
                    f"WHERE {date_column} BETWEEN @start AND @end"
                )
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("start", "DATE", period.start),
                        bigquery.ScalarQueryParameter("end", "DATE", period.end),
                    ]
                )
                job = self._client.query(sql, job_config=job_config)
            else:
                sql = f"SELECT * FROM `{self._prefix}.{view}`"  # noqa: S608
                job = self._client.query(sql)
            return [row_type(**dict(r)) for r in job.result()]
        except (
            Exception
        ) as e:  # one bad/drifted view degrades to an empty block, never 500s the page
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return []

    def _volume_block_for_period(self, period: PeriodRange) -> list[VolumeRow]:
        """Volume bucketed at `period.granularity`, over `v_volume_daily`.

        `v_volume_by_month_channel` is pre-aggregated to one row per
        (month, channel) -- fine for the unfiltered dashboard, but a week
        can't be recovered from a monthly total, and re-grouping it would
        change its row shape under the existing consumer (the fork's
        Overview chart keys `idx[row.month][row.channel] = row.volume`,
        which assumes exactly one row per month/channel and would silently
        keep only the last day's count if that view were widened to day
        grain instead). So this leaves that view untouched and instead
        reads the already-defined, previously-unused day-grained
        `v_volume_daily` sibling, filtered by named parameters and
        re-bucketed to week or month in SQL.
        """
        bucket_format = _BUCKET_FORMAT.get(period.granularity, "%Y-%m")
        sql = (
            f"SELECT FORMAT_DATE('{bucket_format}', day) AS month, "  # noqa: S608
            f"channel, SUM(volume) AS volume "
            f"FROM `{self._prefix}.v_volume_daily` "
            f"WHERE day BETWEEN @start AND @end "
            f"GROUP BY month, channel"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", period.start),
                bigquery.ScalarQueryParameter("end", "DATE", period.end),
            ]
        )
        try:
            job = self._client.query(sql, job_config=job_config)
            return [VolumeRow(**dict(r)) for r in job.result()]
        except Exception as e:  # same fail-open contract as _block
            _log.error("metrics_view_query_failed", view="v_volume_daily", error=str(e))
            return []

    def _fetch_sync(self, period: PeriodRange | None = None) -> DashboardMetrics:
        return DashboardMetrics(
            volume=(
                self._block("v_volume_by_month_channel", VolumeRow)
                if period is None
                else self._volume_block_for_period(period)
            ),
            resolution=self._block("v_resolution_split", ResolutionRow),
            csat=self._block("v_csat", CsatRow),
            nps=self._block("v_nps", NpsRow),
            speed=self._block("v_speed_of_response", SpeedRow),
            fallback=self._block("v_fallback_rate", FallbackRow),
            bounce=self._block("v_bounce_rate", BounceRow),
            quality=self._block("v_quality", QualityRow),
        )

    async def fetch_dashboard(self, period: PeriodRange | None = None) -> DashboardMetrics:
        return await asyncio.to_thread(self._fetch_sync, period)

    def _fetch_anomalies_sync(self) -> list[AnomalyRow]:
        return self._block("v_channel_anomaly", AnomalyRow)

    async def fetch_anomalies(self) -> list[AnomalyRow]:
        return await asyncio.to_thread(self._fetch_anomalies_sync)

    def _fetch_departments_sync(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=self._block("v_dept_pic_performance", DeptPicRow),
            reopen=self._block("v_reopen_rate", ReopenRow),
            category_by_vehicle_model=self._block(
                "v_category_by_vehicle_model", CategoryByVehicleModelRow
            ),
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

    def _fetch_lifecycle_sync(self, period: PeriodRange | None = None) -> LifecycleMetrics:
        return LifecycleMetrics(
            cases=self._block("v_case_lifecycle", CaseLifecycleRow),
            state_trend=self._block(
                "v_state_trend", StateTrendRow, period=period, date_column="month_start"
            ),
        )

    async def fetch_lifecycle(self, period: PeriodRange | None = None) -> LifecycleMetrics:
        return await asyncio.to_thread(self._fetch_lifecycle_sync, period)

    def _fetch_dealer_escalation_sync(self) -> DealerEscalationMetrics:
        return DealerEscalationMetrics(
            by_dealer=self._block("v_dealer_escalation", DealerEscalationRow),
            slowest_cases=self._block("v_dealer_escalation_slowest_cases", DealerSlowCaseRow),
        )

    async def fetch_dealer_escalation(self) -> DealerEscalationMetrics:
        return await asyncio.to_thread(self._fetch_dealer_escalation_sync)

    def _fetch_sla_buckets_sync(self) -> SlaBucketMetrics:
        return SlaBucketMetrics(buckets=self._block("v_resolution_sla_buckets", SlaBucketRow))

    async def fetch_sla_buckets(self) -> SlaBucketMetrics:
        return await asyncio.to_thread(self._fetch_sla_buckets_sync)

    def _fetch_case_aging_sync(self) -> CaseAgingMetrics:
        return CaseAgingMetrics(cases=self._block("v_case_aging", CaseAgingRow))

    async def fetch_case_aging(self) -> CaseAgingMetrics:
        return await asyncio.to_thread(self._fetch_case_aging_sync)

    def _fetch_volume_by_type_division_sync(
        self, period: PeriodRange | None = None
    ) -> VolumeByTypeDivisionMetrics:
        return VolumeByTypeDivisionMetrics(
            volume=self._block(
                "v_volume_by_type_division",
                VolumeByTypeDivisionRow,
                period=period,
                date_column="month_start",
            )
        )

    async def fetch_volume_by_type_division(
        self, period: PeriodRange | None = None
    ) -> VolumeByTypeDivisionMetrics:
        return await asyncio.to_thread(self._fetch_volume_by_type_division_sync, period)


def build_metrics_query_port(settings: Settings) -> MetricsQueryPort:
    """Pick the read-side implementation from settings (reuses metrics_provider)."""
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryMetricsQuery(settings)
        except Exception as e:  # never let init crash the app
            _log.error("metrics_query_init_failed_falling_back_to_mock", error=str(e))
            return MockMetricsQuery()
    return MockMetricsQuery()
