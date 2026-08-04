"""Read-side adapter: SELECTs the 8 Bot-Metrics views into DashboardMetrics.

Range-aware queries (Package E / Task 2, reopened after Task 4's review).
Three methods take an optional `PeriodRange`: `fetch_dashboard` (its
`volume` block only), `fetch_lifecycle` (its `state_trend` block only), and
`fetch_volume_by_type_division`. Every other block/method has no date
column to filter on at all -- there is nothing yet for a period to filter
(see the Package E spec's G2/G3/G6 gaps) -- so they don't take a `period`
argument and are always "unfiltered".

`period=None` is byte-identical to today: the exact same unfiltered
`SELECT * FROM <view>` this adapter has always run. That's a deliberate
regression guard on the whole existing dashboard, not an oversight.

Every block a fetch_* method returns carries a `BlockScope` (see
query_port.py) recording whether its rows are genuinely period-scoped
("ok"), came from a failed/not-yet-recreated view ("unavailable"), couldn't
honour the requested period shape at all ("unsupported_granularity"), or
were never filtered to begin with ("unfiltered"). This exists because rows
alone can't tell those four states apart, and `fetch_dashboard` in
particular returns a *mixed-scope* payload: only `volume` can ever be
period-scoped, so a client rendering all eight dashboard blocks under one
"17-23 July" header needs the per-block marker to avoid presenting an
all-time CSAT or bot-resolution split as if it were that week's.

All three period-aware blocks now use the same mechanism: each reads a
day-grained sibling view (`v_volume_daily`, `v_state_trend_daily`,
`v_volume_by_type_division_daily` -- see `_day_grain_block_for_period` and
`_volume_block_for_period`) rather than filtering their month-grain
originals (`v_volume_by_month_channel`, `v_state_trend`,
`v_volume_by_type_division`), which stay completely untouched and keep
serving the unfiltered path exactly as before. This was NOT the original
design: Task 2's first pass additively widened the two month-grain views
with a `month_start` DATE column and filtered `WHERE month_start BETWEEN
@start AND @end`, gated by a "is this exactly N whole calendar months"
check (`_whole_calendar_months`, since removed) to avoid over-counting a
window that straddled a month boundary. That worked for month-granularity
periods but made every week-granularity request against `state_trend`/
`volume_by_type_division` structurally `"unsupported_granularity"` --
which is exactly the two sections the Weekly Report page (Task 4) is
organised around. The plan's Task 2 Step 3 instruction to "widen the
month-keyed views... prefer widening over a parallel set of weekly views"
turned out to be infeasible for these: they're grouped at month grain, and
exposing week data means changing that grain, which changes the row shape
under `0020-reports-native-merge.patch`'s live consumers (see each day-grain
view's comment in `bigquery_schema.py` for the exact overwriting-assignment
risk). A day-grain sibling avoids the grain change entirely, same
precedent as `v_volume_daily` alongside `v_volume_by_month_channel` from
the original pass -- this is the documented exception to the plan's
prefer-widening rule, not a reversion of the earlier judgment call. The
`month_start` column stays on the month-grain views (harmless, additive,
still exported) even though nothing in this adapter filters on it anymore.

`unsupported_granularity` is consequently no longer reachable for any of
the three period-aware blocks: `_day_grain_block_for_period`/
`_volume_block_for_period` both read day-grain sources, so every
granularity (day/week/month) and every date range is a valid predicate --
there's no shape a day-grain `WHERE day BETWEEN @start AND @end` can get
structurally wrong the way `month_start BETWEEN` could. It remains part of
`BlockScope`'s enum for any future period-aware block whose only available
source is pre-aggregated (the same situation `state_trend`/
`volume_by_type_division` were in before this fix), and Task 3/4's UI
should keep a code path for it rather than assuming it can never occur.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.query_port import (
    AnomalyRow,
    BlockScope,
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

_UNFILTERED_SCOPE = BlockScope(status="unfiltered", period=None, supported_granularity=None)


class BigQueryMetricsQuery:
    """Reads the 8 dashboard views. Each SELECT is wrapped in asyncio.to_thread
    so it never blocks the event loop. A missing/empty view yields an empty
    block rather than raising — the dashboard degrades gracefully."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._prefix = f"{settings.bigquery_project_id}.{settings.bigquery_dataset}"
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    def _range_job_config(self, period: PeriodRange) -> bigquery.QueryJobConfig:
        return bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", period.start),
                bigquery.ScalarQueryParameter("end", "DATE", period.end),
            ]
        )

    def _query_block(
        self,
        view: str,
        row_type: Callable[..., _T],
        *,
        period: PeriodRange | None = None,
        date_column: str | None = None,
    ) -> tuple[list[_T], bool]:
        """SELECT a view into `row_type` rows. Returns `(rows, ok)`.

        `ok=False` only on a genuine query failure (drifted view, a column
        the live view doesn't have yet, a BigQuery outage) -- `rows == []`
        with `ok=True` is a legitimate empty result, not a failure. Callers
        that don't need the distinction use `_block`, which discards it.

        `period=None` (or a view with no `date_column`) runs the exact same
        `SELECT * FROM <view>` this adapter has always run -- byte-identical
        SQL, so today's callers see no change at all. A period *and* a
        `date_column` add a `WHERE <date_column> BETWEEN @start AND @end`
        predicate using BigQuery named parameters -- the range bounds are
        never string-formatted into the query text.
        """
        try:
            if period is not None and date_column is not None:
                sql = (
                    f"SELECT * FROM `{self._prefix}.{view}` "  # noqa: S608
                    f"WHERE {date_column} BETWEEN @start AND @end"
                )
                job = self._client.query(sql, job_config=self._range_job_config(period))
            else:
                sql = f"SELECT * FROM `{self._prefix}.{view}`"  # noqa: S608
                job = self._client.query(sql)
            return [row_type(**dict(r)) for r in job.result()], True
        except (
            Exception
        ) as e:  # one bad/drifted view degrades to an empty block, never 500s the page
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return [], False

    def _block(self, view: str, row_type: Callable[..., _T]) -> list[_T]:
        """Rows only, for the blocks with no period support at all (never
        take a `date_column`, so always the plain unfiltered SELECT *)."""
        rows, _ok = self._query_block(view, row_type)
        return rows

    def _day_grain_block_for_period(
        self,
        view: str,
        row_type: Callable[..., _T],
        period: PeriodRange,
        group_columns: tuple[str, ...],
        value_column: str,
    ) -> tuple[list[_T], bool]:
        """Bucket a day-grain view (columns: `day`, *`group_columns`,
        `value_column`) at `period.granularity`, filtered by named
        parameters. Same mechanism as `_volume_block_for_period`
        (dashboard's volume block), generalised to the other two
        day-grain siblings: `v_state_trend_daily`
        (`group_columns=("status", "division")`, `value_column="cases"`)
        and `v_volume_by_type_division_daily`
        (`group_columns=("channel", "case_type", "division")`,
        `value_column="volume"`). Any period shape/granularity is safe
        here -- the source is already day-grain, so there's no whole-month
        alignment requirement the month-grain originals would need.
        """
        bucket_format = _BUCKET_FORMAT.get(period.granularity, "%Y-%m")
        group_sql = ", ".join(group_columns)
        sql = (
            f"SELECT FORMAT_DATE('{bucket_format}', day) AS month, "  # noqa: S608
            f"{group_sql}, SUM({value_column}) AS {value_column} "
            f"FROM `{self._prefix}.{view}` "
            f"WHERE day BETWEEN @start AND @end "
            f"GROUP BY month, {group_sql}"
        )
        try:
            job = self._client.query(sql, job_config=self._range_job_config(period))
            return [row_type(**dict(r)) for r in job.result()], True
        except Exception as e:  # same fail-open contract as _query_block
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return [], False

    def _volume_block_for_period(self, period: PeriodRange) -> tuple[list[VolumeRow], bool]:
        """Volume bucketed at `period.granularity`, over `v_volume_daily`
        (day grain) -- see the module docstring for why this doesn't widen
        `v_volume_by_month_channel`. Any period shape/granularity is safe
        here: the source is already day-grain, so there's no whole-month
        alignment requirement a month-grain source would need. Kept
        separate from `_day_grain_block_for_period` because `VolumeRow`
        also needs the `bucket` sibling field set, which
        `StateTrendRow`/`VolumeByTypeDivisionRow` don't have.
        """
        bucket_format = _BUCKET_FORMAT.get(period.granularity, "%Y-%m")
        sql = (
            f"SELECT FORMAT_DATE('{bucket_format}', day) AS month, "  # noqa: S608
            f"channel, SUM(volume) AS volume "
            f"FROM `{self._prefix}.v_volume_daily` "
            f"WHERE day BETWEEN @start AND @end "
            f"GROUP BY month, channel"
        )
        try:
            job = self._client.query(sql, job_config=self._range_job_config(period))
            rows: list[VolumeRow] = []
            for r in job.result():
                row = dict(r)
                # `bucket` is a sibling of `month` -- see VolumeRow's
                # docstring for why `month` itself isn't renamed even
                # though it may hold a week key like "2026-W29" here.
                rows.append(VolumeRow(bucket=row["month"], **row))
            return rows, True
        except Exception as e:  # same fail-open contract as _query_block
            _log.error("metrics_view_query_failed", view="v_volume_daily", error=str(e))
            return [], False

    def _dashboard_volume_block(
        self, period: PeriodRange | None
    ) -> tuple[list[VolumeRow], BlockScope]:
        if period is None:
            return self._block("v_volume_by_month_channel", VolumeRow), _UNFILTERED_SCOPE
        rows, ok = self._volume_block_for_period(period)
        status = "ok" if ok else "unavailable"
        # No granularity restriction here (day-grain source), so
        # supported_granularity is never the reason for a non-"ok" status.
        return rows, BlockScope(status=status, period=period, supported_granularity=None)

    def _lifecycle_state_trend_block(
        self, period: PeriodRange | None
    ) -> tuple[list[StateTrendRow], BlockScope]:
        if period is None:
            return self._block("v_state_trend", StateTrendRow), _UNFILTERED_SCOPE
        rows, ok = self._day_grain_block_for_period(
            "v_state_trend_daily", StateTrendRow, period, ("status", "division"), "cases"
        )
        status = "ok" if ok else "unavailable"
        # No granularity restriction here either (day-grain source).
        return rows, BlockScope(status=status, period=period, supported_granularity=None)

    def _volume_by_type_division_block(
        self, period: PeriodRange | None
    ) -> tuple[list[VolumeByTypeDivisionRow], BlockScope]:
        if period is None:
            return (
                self._block("v_volume_by_type_division", VolumeByTypeDivisionRow),
                _UNFILTERED_SCOPE,
            )
        rows, ok = self._day_grain_block_for_period(
            "v_volume_by_type_division_daily",
            VolumeByTypeDivisionRow,
            period,
            ("channel", "case_type", "division"),
            "volume",
        )
        status = "ok" if ok else "unavailable"
        return rows, BlockScope(status=status, period=period, supported_granularity=None)

    def _fetch_sync(self, period: PeriodRange | None = None) -> DashboardMetrics:
        volume_rows, volume_scope = self._dashboard_volume_block(period)
        metrics = DashboardMetrics(
            volume=volume_rows,
            resolution=self._block("v_resolution_split", ResolutionRow),
            csat=self._block("v_csat", CsatRow),
            nps=self._block("v_nps", NpsRow),
            speed=self._block("v_speed_of_response", SpeedRow),
            fallback=self._block("v_fallback_rate", FallbackRow),
            bounce=self._block("v_bounce_rate", BounceRow),
            quality=self._block("v_quality", QualityRow),
        )
        # scopes is deliberately not a constructor kwarg -- see
        # DashboardMetrics.scopes's docstring in query_port.py.
        metrics.attach_scopes(
            {
                "volume": volume_scope,
                "resolution": _UNFILTERED_SCOPE,
                "csat": _UNFILTERED_SCOPE,
                "nps": _UNFILTERED_SCOPE,
                "speed": _UNFILTERED_SCOPE,
                "fallback": _UNFILTERED_SCOPE,
                "bounce": _UNFILTERED_SCOPE,
                "quality": _UNFILTERED_SCOPE,
            }
        )
        return metrics

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
        state_trend_rows, state_trend_scope = self._lifecycle_state_trend_block(period)
        metrics = LifecycleMetrics(
            cases=self._block("v_case_lifecycle", CaseLifecycleRow),
            state_trend=state_trend_rows,
        )
        metrics.attach_scopes({"cases": _UNFILTERED_SCOPE, "state_trend": state_trend_scope})
        return metrics

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
        rows, scope = self._volume_by_type_division_block(period)
        metrics = VolumeByTypeDivisionMetrics(volume=rows)
        metrics.attach_scopes({"volume": scope})
        return metrics

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
