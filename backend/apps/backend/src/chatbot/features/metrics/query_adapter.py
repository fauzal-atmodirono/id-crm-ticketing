"""Read-side adapter: SELECTs the 8 Bot-Metrics views into DashboardMetrics.

Range-aware queries (Package E / Task 2). Three methods take an optional
`PeriodRange`: `fetch_dashboard` (its `volume` block only), `fetch_lifecycle`
(its `state_trend` block only), and `fetch_volume_by_type_division`. Every
other block/method has no date column to filter on at all -- there is
nothing yet for a period to filter (see the Package E spec's G2/G3/G6 gaps)
-- so they don't take a `period` argument and are always "unfiltered".

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

Two different mechanisms back the three period-aware blocks, because they
sit on views with different grain:

- `fetch_dashboard`'s volume block reads the already-defined, previously
  unused day-grained `v_volume_daily` view (see `_volume_block_for_period`)
  rather than widening `v_volume_by_month_channel`. That view is
  pre-aggregated to one row per (month, channel) and the fork's Overview
  chart reads it as `idx[row.month][row.channel] = row.volume` -- an
  *overwriting* assignment. Widening it to day grain would make `SELECT *`
  return ~30 rows per month/channel instead of 1, and that line would
  silently keep only the last day's count as "the month's volume". Reading
  the day-grained sibling instead avoids touching that view at all, and
  supports genuine week-level bucketing that a month-grain view can't (a
  week can't be recovered from a value already collapsed to a monthly
  total).
- `fetch_lifecycle`'s state_trend block and `fetch_volume_by_type_division`
  read `v_state_trend`/`v_volume_by_type_division`, both additively widened
  in `bigquery_schema.py` with a `month_start` DATE column at their
  existing (month, ...) grain -- same row count as before, so their own
  existing readers are unaffected. Because `month_start` is always a
  month's 1st, `WHERE month_start BETWEEN @start AND @end` only returns
  correct results when the requested period is itself composed of whole
  calendar months: a partial-month window (e.g. 17-23 July) has no row's
  `month_start` inside it and would come back empty, but a window whose
  *start* happens to land on a month's 1st while its *end* doesn't (e.g.
  29 June - 5 July) would match and return that entire month's total,
  silently over-counting by roughly 4x. `_month_grain_block` guards this by
  checking the period is exactly N whole calendar months *before* running
  the query at all -- an unsupported shape returns `[]` with
  `status="unsupported_granularity"` rather than a wrong predicate.
"""

from __future__ import annotations

import asyncio
from calendar import monthrange
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


def _whole_calendar_months(period: PeriodRange) -> bool:
    """True iff `period` is exactly N whole calendar months (N >= 1),
    declared with granularity="month".

    The only shape+intent a `month_start`-grain view can honour without
    either dropping a partial month or over-counting one whose
    `month_start` (always a 1st) happens to land inside the window. This
    generalises `period._is_full_calendar_month` (which only recognises a
    *single* whole month) to spans of more than one -- e.g. a 6-month
    trend, whose start and end both land on a month boundary but which
    isn't "one month" -- while keeping that function's same
    shape-plus-declared-granularity gate, so a day/week-granularity
    request that happens to span whole months isn't silently answered with
    month-grain rows instead of the finer breakdown actually asked for.
    """
    if period.granularity != "month":
        return False
    if period.start.day != 1:
        return False
    last_day_of_end_month = monthrange(period.end.year, period.end.month)[1]
    return period.end.day == last_day_of_end_month


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

    def _month_grain_block(
        self, view: str, row_type: Callable[..., _T], date_column: str, period: PeriodRange | None
    ) -> tuple[list[_T], BlockScope]:
        """rows + scope for a view additively widened with a `month_start`
        column (v_state_trend, v_volume_by_type_division) -- see the module
        docstring for why only whole-calendar-month periods are safe here.
        """
        if period is None:
            return self._block(view, row_type), _UNFILTERED_SCOPE
        if not _whole_calendar_months(period):
            return [], BlockScope(
                status="unsupported_granularity", period=period, supported_granularity="month"
            )
        rows, ok = self._query_block(view, row_type, period=period, date_column=date_column)
        status = "ok" if ok else "unavailable"
        return rows, BlockScope(status=status, period=period, supported_granularity="month")

    def _volume_block_for_period(self, period: PeriodRange) -> tuple[list[VolumeRow], bool]:
        """Volume bucketed at `period.granularity`, over `v_volume_daily`
        (day grain) -- see the module docstring for why this doesn't widen
        `v_volume_by_month_channel`. Unlike `_month_grain_block`, any
        period shape is safe here: the source is already day-grain, so
        there's no whole-month alignment requirement.
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

    def _fetch_sync(self, period: PeriodRange | None = None) -> DashboardMetrics:
        volume_rows, volume_scope = self._dashboard_volume_block(period)
        return DashboardMetrics(
            volume=volume_rows,
            resolution=self._block("v_resolution_split", ResolutionRow),
            csat=self._block("v_csat", CsatRow),
            nps=self._block("v_nps", NpsRow),
            speed=self._block("v_speed_of_response", SpeedRow),
            fallback=self._block("v_fallback_rate", FallbackRow),
            bounce=self._block("v_bounce_rate", BounceRow),
            quality=self._block("v_quality", QualityRow),
            scopes={
                "volume": volume_scope,
                "resolution": _UNFILTERED_SCOPE,
                "csat": _UNFILTERED_SCOPE,
                "nps": _UNFILTERED_SCOPE,
                "speed": _UNFILTERED_SCOPE,
                "fallback": _UNFILTERED_SCOPE,
                "bounce": _UNFILTERED_SCOPE,
                "quality": _UNFILTERED_SCOPE,
            },
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
        state_trend_rows, state_trend_scope = self._month_grain_block(
            "v_state_trend", StateTrendRow, "month_start", period
        )
        return LifecycleMetrics(
            cases=self._block("v_case_lifecycle", CaseLifecycleRow),
            state_trend=state_trend_rows,
            scopes={"cases": _UNFILTERED_SCOPE, "state_trend": state_trend_scope},
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
        rows, scope = self._month_grain_block(
            "v_volume_by_type_division", VolumeByTypeDivisionRow, "month_start", period
        )
        return VolumeByTypeDivisionMetrics(volume=rows, scopes={"volume": scope})

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
