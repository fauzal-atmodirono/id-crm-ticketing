from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

import pytest

from chatbot.features.metrics.period import PeriodRange
from chatbot.features.metrics.query_adapter import (
    BigQueryMetricsQuery,
    build_metrics_query_port,
)
from chatbot.features.metrics.query_port import (
    BlockScope,
    CallCentreMetrics,
    CaseAgingMetrics,
    DealerEscalationMetrics,
    DepartmentsMetrics,
    LifecycleMetrics,
    MockMetricsQuery,
    SlaBucketMetrics,
    VolumeByTypeDivisionMetrics,
    VolumeRow,
)
from chatbot.platform.config import Settings


class _FakeJob:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self._rows


class _FailingJob:
    def result(self) -> list[dict[str, Any]]:
        raise RuntimeError("bigquery unavailable")


class _FakeClient:
    """Returns canned rows based on the view name in the SQL."""

    def __init__(
        self,
        by_view: dict[str, list[dict[str, Any]]],
        *,
        fail_views: frozenset[str] = frozenset(),
    ) -> None:
        self._by_view = by_view
        self._fail_views = fail_views
        self.queries: list[str] = []
        self.job_configs: list[Any] = []

    def query(self, sql: str, job_config: Any = None) -> _FakeJob | _FailingJob:
        self.queries.append(sql)
        self.job_configs.append(job_config)
        for view in self._fail_views:
            if view in sql:
                return _FailingJob()
        for view, rows in self._by_view.items():
            if view in sql:
                return _FakeJob(rows)
        return _FakeJob([])


@pytest.mark.asyncio
async def test_adapter_maps_rows_into_dataclasses() -> None:
    client = _FakeClient(
        {
            "v_volume_by_month_channel": [{"month": "2026-06", "channel": "web", "volume": 140}],
            "v_resolution_split": [
                {
                    "channel": "web",
                    "closed_by_bot": 90,
                    "transfer_to_agent": 30,
                    "total": 120,
                    "closed_by_bot_pct": 0.75,
                    "transfer_to_agent_pct": 0.25,
                }
            ],
            "v_csat": [
                {"channel": "web", "respondents": 40, "avg_score": 4.3, "satisfied_rate": 0.85}
            ],
            "v_nps": [
                {
                    "channel": "web",
                    "respondents": 35,
                    "promoters": 20,
                    "passives": 10,
                    "detractors": 5,
                    "nps": 42.86,
                }
            ],
            "v_speed_of_response": [
                {
                    "channel": "web",
                    "is_first_turn": True,
                    "p99_latency_ms": 1800,
                    "avg_latency_ms": 950.0,
                    "turns": 130,
                }
            ],
            "v_fallback_rate": [{"channel": "web", "fallback_rate": 0.08, "turns": 540}],
            "v_bounce_rate": [
                {"channel": "web", "bounced": 18, "total_sessions": 120, "bounce_rate": 0.15}
            ],
            "v_quality": [
                {"channel": "web", "labels": 20, "avg_accuracy": 88.5, "avg_quality": 91.0}
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    adapter = BigQueryMetricsQuery(settings, client=client)

    metrics = await adapter.fetch_dashboard()

    assert metrics.volume[0].volume == 140
    assert metrics.resolution[0].closed_by_bot_pct == 0.75
    assert metrics.csat[0].avg_score == 4.3
    assert metrics.nps[0].promoters == 20
    assert metrics.speed[0].is_first_turn is True
    assert metrics.fallback[0].fallback_rate == 0.08
    assert metrics.bounce[0].bounce_rate == 0.15
    assert metrics.quality[0].avg_accuracy == 88.5
    # one query per view (8 total)
    assert len(client.queries) == 8


@pytest.mark.asyncio
async def test_adapter_handles_null_aggregates() -> None:
    client = _FakeClient(
        {
            "v_csat": [
                {"channel": "web", "respondents": 0, "avg_score": None, "satisfied_rate": None}
            ]
        }
    )
    adapter = BigQueryMetricsQuery(Settings(), client=client)
    metrics = await adapter.fetch_dashboard()
    assert metrics.csat[0].avg_score is None
    assert metrics.volume == []  # absent view -> empty block, no crash


def test_build_factory_returns_mock_for_noop() -> None:
    port = build_metrics_query_port(Settings(metrics_provider="noop"))
    assert isinstance(port, MockMetricsQuery)


@pytest.mark.asyncio
async def test_drifted_view_degrades_to_empty_block_without_raising() -> None:
    """A view whose columns drift (unexpected extra key) must degrade to []
    rather than propagating TypeError and 500-ing the whole dashboard."""
    client = _FakeClient(
        {
            # drifted: extra key "unexpected_col" causes TypeError in row_type(**r)
            "v_volume_by_month_channel": [
                {
                    "month": "2026-06",
                    "channel": "web",
                    "volume": 99,
                    "unexpected_col": "oops",
                }
            ],
            # valid view — must still be populated
            "v_csat": [
                {"channel": "web", "respondents": 10, "avg_score": 4.5, "satisfied_rate": 0.9}
            ],
        }
    )
    adapter = BigQueryMetricsQuery(Settings(), client=client)

    # Must not raise
    metrics = await adapter.fetch_dashboard()

    assert metrics.volume == [], "drifted view must degrade to empty block"
    assert len(metrics.csat) == 1, "valid view must still be populated"
    assert metrics.csat[0].avg_score == 4.5
    # All 8 queries still issued
    assert len(client.queries) == 8


@pytest.mark.asyncio
async def test_departments_reads_expected_views() -> None:
    client = _FakeClient(
        {
            "v_dept_pic_performance": [
                {
                    "department": "Aftersales",
                    "pic": "Ali",
                    "cases": 40,
                    "avg_first_response_min": 12.0,
                    "avg_resolution_min": 240.0,
                    "resolution_rate": 0.9,
                }
            ],
            "v_reopen_rate": [
                {
                    "dealer": "Dealer KL",
                    "department": "Aftersales",
                    "pic": "Ali",
                    "cases": 40,
                    "reopened": 4,
                    "reopen_rate": 0.1,
                }
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    q = BigQueryMetricsQuery(settings, client=client)
    result = await q.fetch_departments()
    assert any("v_dept_pic_performance" in sql for sql in client.queries)
    assert any("v_reopen_rate" in sql for sql in client.queries)
    assert isinstance(result, DepartmentsMetrics)
    assert len(result.dept_pic) == 1
    assert len(result.reopen) == 1


@pytest.mark.asyncio
async def test_callcenter_reads_expected_views() -> None:
    client = _FakeClient(
        {
            "v_sla_achievement": [
                {
                    "channel": "Phone",
                    "division": "Sales",
                    "with_sla": 100,
                    "met": 95,
                    "sla_achievement_rate": 0.95,
                }
            ],
            "v_tasks_per_agent": [
                {
                    "agent_id": "ALI001",
                    "pic": "Ali",
                    "cases": 50,
                    "avg_first_response_min": 8.5,
                    "avg_resolution_min": 180.0,
                    "resolved_cases": 48,
                }
            ],
            "v_first_response_by_channel": [
                {
                    "channel": "Phone",
                    "avg_first_response_min": 8.5,
                    "p50_first_response_min": 5,
                    "p90_first_response_min": 20,
                    "with_first_response": 95,
                }
            ],
            "v_resolution_time": [
                {
                    "channel": "Phone",
                    "division": "Sales",
                    "avg_min": 180.0,
                    "p50_min": 150,
                    "p90_min": 300,
                }
            ],
            "v_complaint_type_ranking": [
                {
                    "category": "Billing",
                    "subcategory": "Late Invoice",
                    "division": "Finance",
                    "cases": 25,
                    "share_pct": 0.45,
                }
            ],
            "v_peak_hours": [
                {
                    "day_of_week": 2,
                    "hour_of_day": 14,
                    "channel": "whatsapp",
                    "volume": 55,
                }
            ],
            "v_nps_by_agent": [
                {
                    "agent_id": "ALI001",
                    "channel": "Phone",
                    "respondents": 30,
                    "nps": 45.0,
                }
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    q = BigQueryMetricsQuery(settings, client=client)
    result = await q.fetch_callcenter()
    assert any("v_sla_achievement" in sql for sql in client.queries)
    assert any("v_tasks_per_agent" in sql for sql in client.queries)
    assert any("v_first_response_by_channel" in sql for sql in client.queries)
    assert any("v_resolution_time" in sql for sql in client.queries)
    assert any("v_complaint_type_ranking" in sql for sql in client.queries)
    assert any("v_peak_hours" in sql for sql in client.queries)
    assert any("v_nps_by_agent" in sql for sql in client.queries)
    assert isinstance(result, CallCentreMetrics)
    assert len(result.sla) == 1
    assert len(result.nps_by_agent) == 1


@pytest.mark.asyncio
async def test_lifecycle_reads_expected_views() -> None:
    client = _FakeClient(
        {
            "v_case_lifecycle": [
                {
                    "conversation_id": "CONV001",
                    "channel": "whatsapp",
                    "division": "Sales",
                    "department": "Aftersales",
                    "dealer": "Dealer KL",
                    "status": "resolved",
                    "created_at": None,
                    "first_response_at": None,
                    "resolved_at": None,
                    "first_response_minutes": 15,
                    "resolution_minutes": 240,
                    "reopen_count": 0,
                }
            ],
            "v_state_trend": [
                {
                    "month": "2026-06",
                    "status": "resolved",
                    "division": "Sales",
                    "cases": 45,
                }
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    q = BigQueryMetricsQuery(settings, client=client)
    result = await q.fetch_lifecycle()
    assert any("v_case_lifecycle" in sql for sql in client.queries)
    assert any("v_state_trend" in sql for sql in client.queries)
    assert isinstance(result, LifecycleMetrics)
    assert len(result.cases) == 1
    assert len(result.state_trend) == 1


@pytest.mark.asyncio
async def test_dealer_escalation_reads_expected_views() -> None:
    # Note: "v_dealer_escalation_slowest_cases" must come first in this dict —
    # _FakeClient.query matches views by substring, and "v_dealer_escalation"
    # is itself a substring of "v_dealer_escalation_slowest_cases", so the
    # more specific key needs first-match priority.
    client = _FakeClient(
        {
            "v_dealer_escalation_slowest_cases": [
                {
                    "conversation_id": "CONV001",
                    "dealer": "Dealer KL",
                    "turnaround_days": 9.0,
                }
            ],
            "v_dealer_escalation": [
                {
                    "dealer": "Dealer KL",
                    "cases_escalated": 12,
                    "avg_turnaround_days": 2.5,
                    "p50_turnaround_days": 2.0,
                    "p90_turnaround_days": 5.0,
                }
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    q = BigQueryMetricsQuery(settings, client=client)
    result = await q.fetch_dealer_escalation()
    assert any("v_dealer_escalation" in sql for sql in client.queries)
    assert any("v_dealer_escalation_slowest_cases" in sql for sql in client.queries)
    assert isinstance(result, DealerEscalationMetrics)
    assert len(result.by_dealer) == 1
    assert len(result.slowest_cases) == 1
    assert result.by_dealer[0].cases_escalated == 12
    assert result.slowest_cases[0].turnaround_days == 9.0


@pytest.mark.asyncio
async def test_sla_buckets_reads_expected_views() -> None:
    client = _FakeClient(
        {
            "v_resolution_sla_buckets": [
                {"case_type": "Complaint", "bucket_label": "< 1 day", "cases": 30}
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    q = BigQueryMetricsQuery(settings, client=client)
    result = await q.fetch_sla_buckets()
    assert any("v_resolution_sla_buckets" in sql for sql in client.queries)
    assert isinstance(result, SlaBucketMetrics)
    assert len(result.buckets) == 1
    assert result.buckets[0].cases == 30


@pytest.mark.asyncio
async def test_case_aging_reads_expected_views() -> None:
    client = _FakeClient(
        {
            "v_case_aging": [
                {
                    "conversation_id": "CONV002",
                    "case_type": "Complaint",
                    "division": "Sales",
                    "dealer": "Dealer KL",
                    "pic": "Ali",
                    "status": "open",
                    "created_at": None,
                    "age_days": 3.5,
                    "bucket_label": "1-7 days",
                }
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    q = BigQueryMetricsQuery(settings, client=client)
    result = await q.fetch_case_aging()
    assert any("v_case_aging" in sql for sql in client.queries)
    assert isinstance(result, CaseAgingMetrics)
    assert len(result.cases) == 1
    assert result.cases[0].age_days == 3.5


@pytest.mark.asyncio
async def test_volume_by_type_division_reads_expected_views() -> None:
    client = _FakeClient(
        {
            "v_volume_by_type_division": [
                {
                    "month": "2026-06",
                    "channel": "web",
                    "case_type": "Complaint",
                    "division": "Sales",
                    "volume": 25,
                }
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    q = BigQueryMetricsQuery(settings, client=client)
    result = await q.fetch_volume_by_type_division()
    assert any("v_volume_by_type_division" in sql for sql in client.queries)
    assert isinstance(result, VolumeByTypeDivisionMetrics)
    assert len(result.volume) == 1
    assert result.volume[0].volume == 25


# --- Task 2: range-aware queries -------------------------------------------


@pytest.mark.asyncio
async def test_period_none_emits_byte_identical_sql_to_today() -> None:
    """Regression guard: omitting a period must not change the query text
    for a view that *does* support period filtering (v_state_trend) — this
    is the exact SQL the adapter has always run."""
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({"v_state_trend": []})
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle()  # period omitted entirely

    state_trend_queries = [sql for sql in client.queries if "v_state_trend" in sql]
    assert state_trend_queries == ["SELECT * FROM `proj.ds.v_state_trend`"]
    # no WHERE, no job_config -- unfiltered call site is untouched
    assert client.job_configs[client.queries.index(state_trend_queries[0])] is None
    assert result.scopes["state_trend"] == BlockScope(
        status="unfiltered", period=None, supported_granularity=None
    )
    assert result.scopes["cases"] == BlockScope(
        status="unfiltered", period=None, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_period_none_leaves_dashboard_volume_query_untouched() -> None:
    """Same regression guard for fetch_dashboard: no period -> the adapter
    still queries v_volume_by_month_channel, not v_volume_daily."""
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({"v_volume_by_month_channel": []})
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard()

    assert any(sql == "SELECT * FROM `proj.ds.v_volume_by_month_channel`" for sql in client.queries)
    assert not any("v_volume_daily" in sql for sql in client.queries)
    assert metrics.scopes["volume"] == BlockScope(
        status="unfiltered", period=None, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_state_trend_week_period_queries_day_grain_view_with_named_parameters() -> None:
    """Task 2 reopened after Task 4's review: 17-23 July is the exact
    partial-month shape the first pass's month_start-filtered query
    structurally could never answer (see test_bigquery_schema.py /
    query_adapter.py's module docstring for the full history) -- it's also
    exactly what the Weekly Report page always requests. This must now
    come back status="ok" with real rows, routed through the day-grain
    v_state_trend_daily sibling instead of the month-grain v_state_trend."""
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_state_trend_daily": [
                {"month": "2026-W29", "status": "resolved", "division": "Sales", "cases": 12}
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle(period=period)

    idx = next(i for i, sql in enumerate(client.queries) if "v_state_trend_daily" in sql)
    emitted_sql = client.queries[idx]
    job_config = client.job_configs[idx]

    # `v_state_trend_daily` (not the month-grain `v_state_trend`) -- the
    # closing backtick disambiguates, since the latter is otherwise a
    # substring of the former's fully-qualified name.
    assert "`proj.ds.v_state_trend`" not in emitted_sql
    assert "`proj.ds.v_state_trend_daily`" in emitted_sql
    assert "FORMAT_DATE('%G-W%V', day)" in emitted_sql
    assert "WHERE day BETWEEN @start AND @end" in emitted_sql
    # the dates themselves must never be string-formatted into the query text
    assert "2026-07-17" not in emitted_sql
    assert "2026-07-23" not in emitted_sql

    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["start"] == date(2026, 7, 17)
    assert params["end"] == date(2026, 7, 23)
    assert len(result.state_trend) == 1
    assert result.state_trend[0].cases == 12
    assert result.scopes["state_trend"] == BlockScope(
        status="ok", period=period, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_state_trend_period_query_failure_degrades_to_empty_block_not_500() -> None:
    """Deployment hazard: ensure_views() creates v_state_trend_daily on the
    next sync, so on a deployment that hasn't synced since this view was
    added, the query fails (view doesn't exist yet) -- same fail-open
    contract as any other query failure, never a 500."""
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({}, fail_views=frozenset({"v_state_trend_daily"}))
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle(period=period)  # must not raise

    assert result.state_trend == []
    assert result.scopes["state_trend"] == BlockScope(
        status="unavailable", period=period, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_month_straddling_period_queries_day_grain_with_correct_range() -> None:
    """The exact window that broke the first pass's month_start filter:
    29 June - 5 July has July's 1st inside it, so `month_start BETWEEN
    @start AND @end` would have matched and returned July's entire
    month-grain total for a 7-day ask (~4x over-count) -- or, after fix
    round 2, been rejected outright as "unsupported_granularity" (correct,
    but meant this window could never be answered at all). Routed through
    the day-grain view, the WHERE predicate itself is precise: it can only
    ever select the 7 requested days, so there's no shape of window left
    that can silently return more than what was asked for. (A canned-row
    fake client can't simulate BigQuery evaluating the predicate -- like
    every other test in this file, this pins the SQL/params construction,
    not live aggregation.)"""
    period = PeriodRange(date(2026, 6, 29), date(2026, 7, 5), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_volume_by_type_division_daily": [
                {
                    "month": "2026-W27",
                    "channel": "web",
                    "case_type": "Inquiry",
                    "division": "Sales",
                    "volume": 9,
                }
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_volume_by_type_division(period=period)

    idx = next(
        i for i, sql in enumerate(client.queries) if "v_volume_by_type_division_daily" in sql
    )
    emitted_sql = client.queries[idx]
    assert "WHERE day BETWEEN @start AND @end" in emitted_sql
    params = {p.name: p.value for p in client.job_configs[idx].query_parameters}
    assert params["start"] == date(2026, 6, 29)
    assert params["end"] == date(2026, 7, 5)
    assert len(result.volume) == 1
    assert result.scopes["volume"] == BlockScope(
        status="ok", period=period, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_multi_month_whole_range_is_supported() -> None:
    """A 6-month trend (Jan-Jun) -- what a monthly-report trend chart
    needs -- is supported the same way as any other range now: the
    day-grain view is filtered and bucketed to month granularity, not
    routed through any month-alignment special case."""
    period = PeriodRange(date(2026, 1, 1), date(2026, 6, 30), "month")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_volume_by_type_division_daily": [
                {
                    "month": "2026-03",
                    "channel": "web",
                    "case_type": "Inquiry",
                    "division": "Sales",
                    "volume": 60,
                }
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_volume_by_type_division(period=period)

    idx = next(
        i for i, sql in enumerate(client.queries) if "v_volume_by_type_division_daily" in sql
    )
    assert "FORMAT_DATE('%Y-%m', day)" in client.queries[idx]
    assert len(result.volume) == 1
    assert result.scopes["volume"] == BlockScope(
        status="ok", period=period, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_state_trend_day_granularity_uses_day_format() -> None:
    """A day-granularity request now gets a genuine daily breakdown (one
    row per day) via the day-grain view, rather than being rejected --
    the finer breakdown this shape actually asks for is exactly what
    v_state_trend_daily can give, unlike the month-grain original."""
    period = PeriodRange(date(2026, 6, 15), date(2026, 6, 15), "day")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_state_trend_daily": [
                {"month": "2026-06-15", "status": "resolved", "division": "Sales", "cases": 3}
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle(period=period)

    idx = next(i for i, sql in enumerate(client.queries) if "v_state_trend_daily" in sql)
    assert "FORMAT_DATE('%Y-%m-%d', day)" in client.queries[idx]
    assert len(result.state_trend) == 1
    assert result.scopes["state_trend"].status == "ok"


@pytest.mark.asyncio
async def test_volume_by_type_division_period_uses_named_parameters() -> None:
    period = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_volume_by_type_division_daily": [
                {
                    "month": "2026-06",
                    "channel": "web",
                    "case_type": "Inquiry",
                    "division": "Sales",
                    "volume": 40,
                }
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_volume_by_type_division(period=period)

    idx = next(
        i for i, sql in enumerate(client.queries) if "v_volume_by_type_division_daily" in sql
    )
    emitted_sql = client.queries[idx]
    assert "WHERE day BETWEEN @start AND @end" in emitted_sql
    assert "2026-06-01" not in emitted_sql
    assert "2026-06-30" not in emitted_sql
    params = {p.name: p.value for p in client.job_configs[idx].query_parameters}
    assert params["start"] == date(2026, 6, 1)
    assert params["end"] == date(2026, 6, 30)
    assert len(result.volume) == 1
    assert result.scopes["volume"] == BlockScope(
        status="ok", period=period, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_dashboard_volume_with_period_queries_volume_daily_with_parameters() -> None:
    """A period on fetch_dashboard buckets volume from v_volume_daily (day
    grain) instead of v_volume_by_month_channel (month grain) -- a week
    can't be recovered from a pre-aggregated monthly total. Unlike the
    month_start-grain views, any period shape is safe here -- a partial
    week is exactly what a day-grain source can answer correctly."""
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {"v_volume_daily": [{"month": "2026-W29", "channel": "web", "volume": 42}]}
    )
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard(period=period)

    idx = next(i for i, sql in enumerate(client.queries) if "v_volume_daily" in sql)
    emitted_sql = client.queries[idx]
    assert "v_volume_by_month_channel" not in emitted_sql
    assert "FORMAT_DATE('%G-W%V', day)" in emitted_sql
    assert "WHERE day BETWEEN @start AND @end" in emitted_sql
    assert "2026-07-17" not in emitted_sql
    assert "2026-07-23" not in emitted_sql
    params = {p.name: p.value for p in client.job_configs[idx].query_parameters}
    assert params["start"] == date(2026, 7, 17)
    assert params["end"] == date(2026, 7, 23)
    assert metrics.volume == [
        VolumeRow(month="2026-W29", channel="web", volume=42, bucket="2026-W29")
    ]
    assert metrics.scopes["volume"] == BlockScope(
        status="ok", period=period, supported_granularity=None
    )


@pytest.mark.asyncio
async def test_dashboard_volume_period_month_granularity_uses_month_format() -> None:
    period = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {"v_volume_daily": [{"month": "2026-06", "channel": "web", "volume": 100}]}
    )
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard(period=period)

    idx = next(i for i, sql in enumerate(client.queries) if "v_volume_daily" in sql)
    assert "FORMAT_DATE('%Y-%m', day)" in client.queries[idx]
    assert metrics.volume == [
        VolumeRow(month="2026-06", channel="web", volume=100, bucket="2026-06")
    ]


@pytest.mark.asyncio
async def test_dashboard_volume_period_day_granularity_uses_day_format() -> None:
    period = PeriodRange(date(2026, 7, 20), date(2026, 7, 20), "day")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {"v_volume_daily": [{"month": "2026-07-20", "channel": "web", "volume": 7}]}
    )
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard(period=period)

    idx = next(i for i, sql in enumerate(client.queries) if "v_volume_daily" in sql)
    assert "FORMAT_DATE('%Y-%m-%d', day)" in client.queries[idx]
    assert metrics.volume == [
        VolumeRow(month="2026-07-20", channel="web", volume=7, bucket="2026-07-20")
    ]


@pytest.mark.asyncio
async def test_dashboard_period_does_not_leak_into_unfiltered_blocks() -> None:
    """Critical-1 regression guard: a period on fetch_dashboard only
    affects the volume block -- the other 7 blocks have no date column at
    all, must stay unfiltered, and must say so via BlockScope so a client
    rendering all 8 under one period header doesn't present all-time
    figures as if they were that period's."""
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_volume_daily": [{"month": "2026-W29", "channel": "web", "volume": 5}],
            "v_csat": [
                {"channel": "web", "respondents": 10, "avg_score": 4.2, "satisfied_rate": 0.9}
            ],
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard(period=period)

    unfiltered_blocks = (
        "resolution",
        "csat",
        "nps",
        "speed",
        "fallback",
        "bounce",
        "quality",
    )
    view_by_block = {
        "resolution": "v_resolution_split",
        "csat": "v_csat",
        "nps": "v_nps",
        "speed": "v_speed_of_response",
        "fallback": "v_fallback_rate",
        "bounce": "v_bounce_rate",
        "quality": "v_quality",
    }
    for block in unfiltered_blocks:
        view = view_by_block[block]
        idx = next(i for i, sql in enumerate(client.queries) if view in sql)
        # every non-volume block still ran the plain, unfiltered SELECT *
        assert client.queries[idx] == f"SELECT * FROM `proj.ds.{view}`"  # noqa: S608
        assert client.job_configs[idx] is None
        # ... and is explicitly marked unfiltered, not silently mixed into
        # the period-scoped payload
        assert metrics.scopes[block] == BlockScope(
            status="unfiltered", period=None, supported_granularity=None
        )
    assert metrics.csat[0].avg_score == 4.2
    # volume, in contrast, really was scoped to the requested period
    assert metrics.scopes["volume"].status == "ok"
    assert metrics.scopes["volume"].period == period


@pytest.mark.asyncio
async def test_dashboard_volume_period_query_failure_degrades_to_empty_block() -> None:
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({}, fail_views=frozenset({"v_volume_daily"}))
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard(period=period)  # must not raise

    assert metrics.volume == []
    assert metrics.scopes["volume"] == BlockScope(
        status="unavailable", period=period, supported_granularity=None
    )


# --- Task 2 review fix round 2: scopes must not leak into the no-period
# JSON shape, at the level dashboard_router.py / insights_router.py
# actually serialise at (a bare `asdict(await port.fetch_*())`, no
# response-model filtering). Asserted here rather than in
# test_dashboard_router.py / test_insights_router.py because those two
# files can't be collected in this environment without a live
# GEMINI_API_KEY -- a test that never runs is not a real guard.


@pytest.mark.asyncio
async def test_dashboard_unfiltered_asdict_has_no_scopes_key() -> None:
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({})
    q = BigQueryMetricsQuery(settings, client=client)

    payload = asdict(await q.fetch_dashboard())  # exactly what dashboard_router.py does

    assert set(payload.keys()) == {
        "volume",
        "resolution",
        "csat",
        "nps",
        "speed",
        "fallback",
        "bounce",
        "quality",
    }
    assert "scopes" not in payload


@pytest.mark.asyncio
async def test_lifecycle_unfiltered_asdict_has_no_scopes_key() -> None:
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({})
    q = BigQueryMetricsQuery(settings, client=client)

    payload = asdict(await q.fetch_lifecycle())  # exactly what insights_router.py's
    # /metrics/lifecycle route does

    assert set(payload.keys()) == {"cases", "state_trend"}
    assert "scopes" not in payload


@pytest.mark.asyncio
async def test_volume_by_type_division_unfiltered_asdict_has_no_scopes_key() -> None:
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({})
    q = BigQueryMetricsQuery(settings, client=client)

    payload = asdict(await q.fetch_volume_by_type_division())  # exactly what
    # insights_router.py's /metrics/volume-by-type route does

    assert set(payload.keys()) == {"volume"}
    assert "scopes" not in payload


@pytest.mark.asyncio
async def test_scopes_is_still_reachable_as_a_plain_attribute() -> None:
    """Not being a dataclass field must not mean not being usable -- Task
    3/4 (or a future export path) still needs `metrics.scopes` to work
    like any other attribute, just invisible to asdict()/fields()."""
    period = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({})
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard(period=period)

    assert metrics.scopes["volume"].status == "ok"
    assert metrics.scopes["csat"].status == "unfiltered"
