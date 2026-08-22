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

All three period-aware blocks now use the same mechanism, and since the
`bucket` field landed on all three row types (finding M6) literally the
same method: each reads a day-grained sibling view (`v_volume_daily`,
`v_state_trend_daily`, `v_volume_by_type_division_daily` -- see
`_day_grain_block_for_period`) rather than filtering their month-grain
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
the three period-aware *value* blocks: `_day_grain_block_for_period` reads
a day-grain source, so every granularity (day/week/month) and every date
range is a valid predicate -- there's no shape a day-grain `WHERE day
BETWEEN @start AND @end` can get structurally wrong the way `month_start
BETWEEN` could. It is still reachable, and is the honest answer, for
`fetch_lifecycle`'s `cases` block: `v_case_lifecycle` is row-per-case with
no aggregate grain and no day-grain sibling to route through, so a period
request skips it entirely rather than full-scanning it (see
`_lifecycle_cases_block`, Package E final fix / finding I5). Task 3/4's UI
must keep its code path for this status; it is not hypothetical.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import structlog
from fastapi import HTTPException
from google.cloud import bigquery

from chatbot.features.metrics.query_port import (
    AfterHoursFirstResponseRow,
    AfterHoursMetrics,
    AfterHoursVolumeRow,
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
    EmptyMetricsQuery,
    FallbackRow,
    FirstResponseRow,
    HourlyAnomalyRow,
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
    TagVolumeMetrics,
    TagVolumeRow,
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


def _in_period(day: Any, period: PeriodRange) -> bool:
    """Is a row's `day` inside the requested window?

    Applied in Python for `v_case_aging` alone: it is row-per-case with no
    aggregate to re-bucket, so there is nothing for the day-grain SQL path to
    group. A row with no day is kept -- dropping it would silently shrink the
    aging list, and an aging report that hides cases is worse than one showing
    a few extra.
    """
    if day is None:
        return True
    try:
        return period.start <= day <= period.end
    except TypeError:
        return True


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
        filters: Any | None = None,
    ) -> tuple[list[_T], bool]:
        """SELECT a view into `row_type` rows, unfiltered. Returns `(rows, ok)`.

        `ok=False` only on a genuine query failure (drifted view, a column
        the live view doesn't have yet, a BigQuery outage) -- `rows == []`
        with `ok=True` is a legitimate empty result, not a failure. Callers
        that don't need the distinction use `_block`, which discards it.

        This emits the exact same `SELECT * FROM <view>` the adapter has
        always run -- byte-identical SQL, so today's callers see no change
        at all.

        It takes **no** `period`/`date_column` and emits no `WHERE`
        predicate, deliberately (Package E final fix, finding M1). It used
        to carry both, plus a `WHERE <date_column> BETWEEN @start AND @end`
        branch, from the abandoned month-grain filtering design. Once the
        period path moved to the day-grain siblings
        (`_day_grain_block_for_period`) the branch lost every caller, and
        the `_whole_calendar_months` guard that stopped it over-counting a
        month-grain view was deleted along with the rest of that design.
        Left in place it was a loaded gun: pointing it at a month-grain
        view (`v_state_trend`, `v_volume_by_type_division`) reintroduces
        the round-2 4x over-count -- a window containing any 1st of a month
        returns that whole month -- with no test failing. Any future
        period-aware block reads a day-grain source through
        `_day_grain_block_for_period`; there is no supported way to filter
        a month-grain view by date, which is the point.
        """
        try:
            sql = f"SELECT * FROM `{self._prefix}.{view}`"  # noqa: S608
            job_config = None
            if filters is not None:
                # `fragment` contains placeholders only; the values are bound.
                # An UnsupportedFilter raised here is a 400 and propagates --
                # a filter the view cannot honour must not degrade to an
                # unfiltered answer under a filtered header.
                fragment, params = filters.predicates_for(view)
                if fragment:
                    sql = f"{sql} WHERE {fragment}"
                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter(name, "STRING", value)
                            for name, value in params.items()
                        ]
                    )
            job = self._client.query(sql, job_config=job_config)
            return [row_type(**dict(r)) for r in job.result()], True
        except HTTPException:
            raise
        except (
            Exception
        ) as e:  # one bad/drifted view degrades to an empty block, never 500s the page
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return [], False

    def _block(
        self,
        view: str,
        row_type: Callable[..., _T],
        filters: Any | None = None,
    ) -> list[_T]:
        """Rows only, for callers that don't need the ok/failed distinction."""
        rows, _ok = self._query_block(view, row_type, filters)
        return rows

    def _day_grain_block_for_period(
        self,
        view: str,
        row_type: Callable[..., _T],
        period: PeriodRange,
        group_columns: tuple[str, ...],
        value_column: str,
        filters: Any | None = None,
    ) -> tuple[list[_T], bool]:
        """Bucket a day-grain view (columns: `day`, *`group_columns`,
        `value_column`) at `period.granularity`, filtered by named
        parameters. The single mechanism behind all three period-aware
        blocks: `v_volume_daily` (`group_columns=("channel",)`,
        `value_column="volume"`), `v_state_trend_daily`
        (`group_columns=("status", "division")`, `value_column="cases"`)
        and `v_volume_by_type_division_daily`
        (`group_columns=("channel", "case_type", "division")`,
        `value_column="volume"`). Any period shape/granularity is safe
        here -- the source is already day-grain, so there's no whole-month
        alignment requirement the month-grain originals would need.

        Every row gets `bucket` set to the same value as `month` (Package
        E final fix, finding M6). `month` cannot be renamed -- patch
        `0020`'s charts read it as a real "YYYY-MM" on the unfiltered path
        -- so on this path it holds whatever `_BUCKET_FORMAT` produced,
        which for a week period is an ISO key like "2026-W29". `bucket` is
        the granularity-neutral sibling a period-scoped consumer groups by
        without having to know which of those two it is looking at. It was
        previously set only on `VolumeRow`, which is why this method and a
        near-identical `_volume_block_for_period` existed side by side;
        with `bucket` on all three row types the two collapse into one and
        the three blocks are guaranteed to behave identically.
        """
        bucket_format = _BUCKET_FORMAT.get(period.granularity, "%Y-%m")
        group_sql = ", ".join(group_columns)
        # Filters narrow the same query the period already scopes -- one pass,
        # not a post-filter, so a filtered week costs no more than a plain one.
        filter_sql = ""
        job_config = self._range_job_config(period)
        if filters is not None:
            fragment, params = filters.predicates_for(view)
            if fragment:
                filter_sql = f" AND {fragment}"
                job_config.query_parameters = list(job_config.query_parameters) + [
                    bigquery.ScalarQueryParameter(name, "STRING", value)
                    for name, value in params.items()
                ]

        sql = (
            f"SELECT FORMAT_DATE('{bucket_format}', day) AS month, "  # noqa: S608
            f"{group_sql}, SUM({value_column}) AS {value_column} "
            f"FROM `{self._prefix}.{view}` "
            f"WHERE day BETWEEN @start AND @end{filter_sql} "
            f"GROUP BY month, {group_sql}"
        )
        try:
            job = self._client.query(sql, job_config=job_config)
            rows: list[_T] = []
            for r in job.result():
                row = dict(r)
                rows.append(row_type(bucket=row["month"], **row))
            return rows, True
        except Exception as e:  # same fail-open contract as _query_block
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return [], False

    def _dashboard_volume_block(
        self, period: PeriodRange | None
    ) -> tuple[list[VolumeRow], BlockScope]:
        if period is None:
            return self._block("v_volume_by_month_channel", VolumeRow), _UNFILTERED_SCOPE
        rows, ok = self._day_grain_block_for_period(
            "v_volume_daily", VolumeRow, period, ("channel",), "volume"
        )
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

    def _fetch_hourly_anomalies_sync(self) -> tuple[list[HourlyAnomalyRow], bool]:
        # `_query_block`, not `_block`: this view is flag-gated, so "the view is
        # not there yet" is a real and likely state on a tenant that has enabled
        # ANOMALY_HOURLY_ENABLED but not yet re-run `ensure_views`. `_block`
        # would discard the `ok` flag and hand the endpoint an empty list, which
        # renders as "no anomalies" -- a reassurance about an examination that
        # never happened.
        return self._query_block("v_channel_anomaly_hourly", HourlyAnomalyRow)

    async def fetch_hourly_anomalies(self) -> tuple[list[HourlyAnomalyRow], bool]:
        return await asyncio.to_thread(self._fetch_hourly_anomalies_sync)

    def _dated_block(self, view, row_type, period, group_columns, value_column, filters=None):
        """One block of a now-dated view, filtered when a period is given.

        P4: these views gained a `day` column precisely so this is possible.
        Before that they could only answer all-time, which is why the
        endpoints 400'd rather than serve an all-time number under a week
        header.
        """
        if period is None:
            return self._block(view, row_type, filters), _UNFILTERED_SCOPE
        rows, ok = self._day_grain_block_for_period(
            view, row_type, period, group_columns, value_column, filters
        )
        return rows, BlockScope(
            status="ok" if ok else "unavailable",
            period=period,
            supported_granularity=None,
        )

    def _fetch_departments_sync(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> DepartmentsMetrics:
        dept_rows, dept_scope = self._dated_block(
            "v_dept_pic_performance",
            DeptPicRow,
            period,
            ("department", "pic"),
            "cases",
            filters,
        )
        reopen_rows, reopen_scope = self._dated_block(
            "v_reopen_rate",
            ReopenRow,
            period,
            ("dealer", "department", "pic"),
            "reopened",
            filters,
        )
        # v_category_by_vehicle_model has no day column (it is a taxonomy
        # breakdown, not a time series), so it stays unfiltered and SAYS so
        # rather than being quietly served inside a period-labelled response.
        metrics = DepartmentsMetrics(
            dept_pic=dept_rows,
            reopen=reopen_rows,
            category_by_vehicle_model=self._block(
                "v_category_by_vehicle_model", CategoryByVehicleModelRow
            ),
        )
        metrics.attach_scopes(
            {
                "dept_pic": dept_scope,
                "reopen": reopen_scope,
                "category_by_vehicle_model": _UNFILTERED_SCOPE,
            }
        )
        return metrics

    async def fetch_departments(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> DepartmentsMetrics:
        return await asyncio.to_thread(self._fetch_departments_sync, period, filters)

    def _fetch_callcenter_sync(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> CallCentreMetrics:
        if period is not None:
            return self._callcenter_for_period(period, filters)
        return CallCentreMetrics(
            sla=self._block("v_sla_achievement", SlaAchievementRow),
            tasks_per_agent=self._block("v_tasks_per_agent", TasksPerAgentRow),
            first_response=self._block("v_first_response_by_channel", FirstResponseRow),
            resolution_time=self._block("v_resolution_time", ResolutionTimeRow),
            complaint_types=self._block("v_complaint_type_ranking", ComplaintTypeRow),
            peak_hours=self._block("v_peak_hours", PeakHourRow),
            nps_by_agent=self._block("v_nps_by_agent", NpsByAgentRow),
        )

    def _callcenter_for_period(
        self, period: PeriodRange, filters: Any | None = None
    ) -> CallCentreMetrics:
        """Every callcenter view gained a `day` column in P4, so all five
        blocks are genuinely period-scoped rather than all-time figures under
        a week header."""
        blocks = {
            "sla": ("v_sla_achievement", SlaAchievementRow, ("channel", "division"), "with_sla"),
            "tasks_per_agent": (
                "v_tasks_per_agent",
                TasksPerAgentRow,
                ("agent_id", "pic"),
                "cases",
            ),
            "first_response": (
                "v_first_response_by_channel",
                FirstResponseRow,
                ("channel",),
                "with_first_response",
            ),
            "resolution_time": (
                "v_resolution_time",
                ResolutionTimeRow,
                ("channel", "division"),
                "resolved",
            ),
            "nps_by_agent": (
                "v_nps_by_agent",
                NpsByAgentRow,
                ("agent_id", "channel"),
                "respondents",
            ),
        }
        rows: dict[str, Any] = {}
        scopes: dict[str, BlockScope] = {}
        for name, (view, row_type, group_columns, value_column) in blocks.items():
            rows[name], scopes[name] = self._dated_block(
                view, row_type, period, group_columns, value_column, filters
            )
        metrics = CallCentreMetrics(**rows)
        metrics.attach_scopes(scopes)
        return metrics

    async def fetch_callcenter(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> CallCentreMetrics:
        return await asyncio.to_thread(self._fetch_callcenter_sync, period, filters)

    def _lifecycle_cases_block(
        self, period: PeriodRange | None
    ) -> tuple[list[CaseLifecycleRow], BlockScope]:
        """`v_case_lifecycle` is a row-per-case view -- an unfiltered
        `SELECT *` over every case the tenant has ever had, with no
        aggregate grain and no period-filterable sibling.

        With a period supplied the query is **skipped entirely** (Package
        E final fix, finding I5): `insights_router.py`'s `/metrics/lifecycle`
        fans out to a current and a previous leg, so every week change on
        the Weekly Report page was triggering two full scans of this view
        and serialising the whole all-time case list into the JSON twice
        -- for a page that reads only `state_trend` from this endpoint and
        gets its per-case detail from live Chatwoot via patch `0044`'s
        `fetchAllCases`. That is exactly the cost the plan's "no
        full-table scan per page load" constraint targets.

        Skipped on **both** legs, not just `previous`: all-time case rows
        under a "17-23 July" header are the mislabelling `BlockScope`
        exists to prevent, and halving a cost that shouldn't be paid at
        all is not a fix. The scope reported is
        `"unsupported_granularity"` -- the accurate one. It is not
        `"unavailable"` (nothing failed) and not `"unfiltered"` (that
        would claim all-time rows are present when the list is empty);
        it says the view cannot be period-filtered, which is the truth,
        and it is the status Task 4's UI already has a code path for.

        The no-period path is untouched: same `SELECT *`, same
        `"unfiltered"` scope, byte-identical payload. That is the path
        patches `0020`/`0034`'s Case Lifecycle report reads, and it is the
        only consumer of this block anywhere in the repo.
        """
        if period is None:
            return self._block("v_case_lifecycle", CaseLifecycleRow), _UNFILTERED_SCOPE
        return [], BlockScope(
            status="unsupported_granularity", period=period, supported_granularity=None
        )

    def _fetch_lifecycle_sync(self, period: PeriodRange | None = None) -> LifecycleMetrics:
        cases_rows, cases_scope = self._lifecycle_cases_block(period)
        state_trend_rows, state_trend_scope = self._lifecycle_state_trend_block(period)
        metrics = LifecycleMetrics(cases=cases_rows, state_trend=state_trend_rows)
        metrics.attach_scopes({"cases": cases_scope, "state_trend": state_trend_scope})
        return metrics

    async def fetch_lifecycle(self, period: PeriodRange | None = None) -> LifecycleMetrics:
        return await asyncio.to_thread(self._fetch_lifecycle_sync, period)

    def _fetch_dealer_escalation_sync(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> DealerEscalationMetrics:
        # NOTE the period here filters on `dealer_escalated_at`, not
        # `created_at` -- see v_dealer_escalation in bigquery_schema.py. A case
        # created in May and escalated in June is a JUNE row.
        by_dealer, dealer_scope = self._dated_block(
            "v_dealer_escalation",
            DealerEscalationRow,
            period,
            ("dealer",),
            "cases_escalated",
            filters,
        )
        metrics = DealerEscalationMetrics(
            by_dealer=by_dealer,
            slowest_cases=self._block("v_dealer_escalation_slowest_cases", DealerSlowCaseRow),
        )
        metrics.attach_scopes(
            {
                "by_dealer": dealer_scope,
                # A worst-offenders list is a ranking, not a time series.
                "slowest_cases": _UNFILTERED_SCOPE,
            }
        )
        return metrics

    async def fetch_dealer_escalation(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> DealerEscalationMetrics:
        return await asyncio.to_thread(self._fetch_dealer_escalation_sync, period, filters)

    def _fetch_sla_buckets_sync(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> SlaBucketMetrics:
        rows, scope = self._dated_block(
            "v_resolution_sla_buckets",
            SlaBucketRow,
            period,
            ("case_type", "bucket_label"),
            "cases",
            filters,
        )
        metrics = SlaBucketMetrics(buckets=rows)
        metrics.attach_scopes({"buckets": scope})
        return metrics

    async def fetch_sla_buckets(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> SlaBucketMetrics:
        return await asyncio.to_thread(self._fetch_sla_buckets_sync, period, filters)

    def _fetch_case_aging_sync(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> CaseAgingMetrics:
        # Row-per-case, not an aggregate: filtered by date directly rather
        # than re-bucketed, since there is nothing to re-aggregate.
        rows = self._block("v_case_aging", CaseAgingRow, filters)
        scope = _UNFILTERED_SCOPE
        if period is not None:
            rows = [r for r in rows if _in_period(getattr(r, "day", None), period)]
            scope = BlockScope(status="ok", period=period, supported_granularity=None)
        metrics = CaseAgingMetrics(cases=rows)
        metrics.attach_scopes({"cases": scope})
        return metrics

    async def fetch_case_aging(
        self, period: PeriodRange | None = None, filters: Any | None = None
    ) -> CaseAgingMetrics:
        return await asyncio.to_thread(self._fetch_case_aging_sync, period, filters)

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

    def _fetch_after_hours_sync(self, period: PeriodRange | None = None) -> AfterHoursMetrics:
        """P1: after-hours arrival volume, and first-response speed split by it.

        The two blocks report different scopes on purpose. `v_volume_after_hours`
        carries a `day` column and is period-capable; the first-response split
        is month-grain only, so under a period it reports `unsupported_granularity`
        rather than quietly serving an all-time answer inside a period-labelled
        response -- the failure this module's docstring exists to prevent.
        """
        if period is None:
            volume_rows = self._block("v_volume_after_hours", AfterHoursVolumeRow)
            volume_scope = _UNFILTERED_SCOPE
        else:
            volume_rows, ok = self._day_grain_block_for_period(
                "v_volume_after_hours",
                AfterHoursVolumeRow,
                period,
                ("channel", "arrival_window"),
                "volume",
            )
            volume_scope = BlockScope(
                status="ok" if ok else "unavailable",
                period=period,
                supported_granularity=None,
            )

        first_response_rows = self._block(
            "v_first_response_by_hours_split", AfterHoursFirstResponseRow
        )
        first_response_scope = (
            _UNFILTERED_SCOPE
            if period is None
            else BlockScope(
                status="unsupported_granularity",
                period=period,
                supported_granularity="month",
            )
        )

        metrics = AfterHoursMetrics(volume=volume_rows, first_response=first_response_rows)
        metrics.attach_scopes({"volume": volume_scope, "first_response": first_response_scope})
        return metrics

    async def fetch_after_hours(self, period: PeriodRange | None = None) -> AfterHoursMetrics:
        return await asyncio.to_thread(self._fetch_after_hours_sync, period)

    def _fetch_by_tag_sync(
        self, period: PeriodRange | None = None, tag: str | None = None
    ) -> TagVolumeMetrics:
        rows, scope = self._dated_block(
            "v_volume_by_tag", TagVolumeRow, period, ("tag", "channel"), "cases"
        )
        if tag is not None:
            # Filtered in Python rather than SQL: the value comes from a query
            # string, and the block helper's parameter list is fixed. An empty
            # result is an empty list, never a 404 -- "no cases carried this
            # tag" is an answer, not a missing resource.
            rows = [r for r in rows if getattr(r, "tag", None) == tag]
        metrics = TagVolumeMetrics(by_tag=rows)
        metrics.attach_scopes({"by_tag": scope})
        return metrics

    async def fetch_by_tag(
        self, period: PeriodRange | None = None, tag: str | None = None
    ) -> TagVolumeMetrics:
        return await asyncio.to_thread(self._fetch_by_tag_sync, period, tag)


def build_metrics_query_port(settings: Settings) -> MetricsQueryPort:
    """Pick the read-side implementation from settings (reuses metrics_provider).

    Three outcomes, and the two non-BigQuery ones are NOT the same thing:

    - `"mock"` -- a deliberate choice of canned data (local dev, a demo).
      The rows are the intended answer and every block reports "unfiltered":
      all-time figures, honestly labelled as such.
    - anything else, i.e. the `"noop"` default -- a tenant with no
      warehouse. Empty blocks (`EmptyMetricsQuery`), because the canned rows
      are one tenant's fixtures ("Dealer KL", "Aftersales"/"Ali", "e.MAS 5")
      and serving them here is what made a freshly provisioned tenant's
      Reports pages show another customer's data as if it were its own.
      Canned rows must now be asked for by name.
    - the `except` branch -- a *failure*: the tenant asked for BigQuery and
      the client could not be built, so canned rows (682 cases, "2026-06")
      would render as a plausible-looking real figure on a client-facing
      page. `degraded=True` makes every block report `"unavailable"`, which
      a period-scoped consumer renders as "temporarily unavailable" rather
      than as data. Still fail-open -- a misconfigured warehouse must not
      raise or 500 the page -- just no longer fail-open into invented
      numbers. Deliberately NOT `EmptyMetricsQuery`: an empty report and a
      broken one look identical to a reader, and only one of them is worth
      paging someone about.
    """
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryMetricsQuery(settings)
        except Exception as e:  # never let init crash the app
            _log.error("metrics_query_init_failed_falling_back_to_mock", error=str(e))
            return MockMetricsQuery(degraded=True)
    if settings.metrics_provider == "mock":
        return MockMetricsQuery()
    return EmptyMetricsQuery()
