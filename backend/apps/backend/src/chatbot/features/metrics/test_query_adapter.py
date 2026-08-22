from __future__ import annotations

import asyncio
import inspect
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
    EmptyMetricsQuery,
    LifecycleMetrics,
    MockMetricsQuery,
    SlaBucketMetrics,
    StateTrendRow,
    VolumeByTypeDivisionMetrics,
    VolumeByTypeDivisionRow,
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


def test_build_factory_returns_empty_for_noop() -> None:
    """"noop" is what a tenant with no warehouse runs, so it must not hand
    the reporting pages another tenant's canned fixtures. Canned rows moved
    behind an explicit `metrics_provider="mock"`."""
    port = build_metrics_query_port(Settings(metrics_provider="noop"))
    assert isinstance(port, EmptyMetricsQuery)


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


# --- Package E final fix (whole-branch review) ------------------------------


@pytest.mark.asyncio
async def test_period_skips_the_case_lifecycle_full_scan_entirely() -> None:
    """Finding I5. `v_case_lifecycle` is a row-per-case view with no
    aggregate grain and no day-grain sibling, so `_fetch_lifecycle_sync`
    used to run its unfiltered `SELECT *` regardless of period -- and
    `/metrics/lifecycle` fans out to two legs, so every week change cost
    two full scans plus serialising the whole all-time case list into a
    payload the Weekly Report page never reads from. With a period, the
    query must not be issued at all."""
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
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
            "v_state_trend_daily": [
                {"month": "2026-W29", "status": "resolved", "division": "Sales", "cases": 12}
            ],
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle(period=period)

    assert not any("v_case_lifecycle" in sql for sql in client.queries)
    assert result.cases == []
    # ...and the empty list is labelled honestly: nothing failed
    # ("unavailable" would be wrong) and no all-time rows are present
    # ("unfiltered" would be wrong). The view genuinely cannot be
    # period-filtered, which is what this status means.
    assert result.scopes["cases"] == BlockScope(
        status="unsupported_granularity", period=period, supported_granularity=None
    )
    # the block the page actually reads is unaffected
    assert result.state_trend[0].cases == 12


@pytest.mark.asyncio
async def test_no_period_still_full_scans_case_lifecycle_unchanged() -> None:
    """The other half of finding I5: the no-period path is the one patch
    0020/0034's Case Lifecycle report reads, and it must be byte-identical
    -- same query, same rows, same "unfiltered" scope."""
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
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
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle()

    assert "SELECT * FROM `proj.ds.v_case_lifecycle`" in client.queries
    assert len(result.cases) == 1
    assert result.scopes["cases"] == BlockScope(
        status="unfiltered", period=None, supported_granularity=None
    )


def test_query_block_no_longer_accepts_a_period_or_date_column() -> None:
    """Finding M1. `_query_block`'s `period`/`date_column` kwargs and their
    `WHERE <col> BETWEEN @start AND @end` branch were left behind by the
    abandoned month-grain filtering design, with zero callers and with the
    `_whole_calendar_months` guard that used to protect them deleted. A
    future task pointing that branch at a month-grain view reintroduces the
    round-2 4x over-count -- a window containing any 1st of a month returns
    that whole month -- and nothing would fail. There must be no way to ask
    `_query_block` for a DATE-filtered read.

    P4 narrowed this from "exactly these three parameters" to "no date
    parameters". `_query_block` now takes a `filters` argument for DIMENSION
    filters (agent/team/channel/dealer), which carry none of the over-counting
    risk: they add an equality predicate, not a range, and cannot pull in rows
    outside the requested window. The behavioural guard below -- that no
    emitted SQL ever filters a month-grain view by date -- is the one that
    actually protects against M1, and it is unchanged."""
    params = set(inspect.signature(BigQueryMetricsQuery._query_block).parameters)
    assert "period" not in params
    assert "date_column" not in params
    assert {"self", "view", "row_type"} <= params


@pytest.mark.asyncio
async def test_no_emitted_sql_ever_filters_a_month_grain_view_by_date() -> None:
    """The behavioural half of finding M1: whatever the call shape, the
    only date predicate this adapter emits is the day-grain
    `WHERE day BETWEEN`. `month_start` must never appear in a predicate --
    that is the over-counting shape."""
    period = PeriodRange(date(2026, 6, 29), date(2026, 7, 5), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({})
    q = BigQueryMetricsQuery(settings, client=client)

    await q.fetch_dashboard(period=period)
    await q.fetch_lifecycle(period=period)
    await q.fetch_volume_by_type_division(period=period)
    await q.fetch_dashboard()
    await q.fetch_lifecycle()
    await q.fetch_volume_by_type_division()

    for sql in client.queries:
        assert "month_start" not in sql
        if "BETWEEN" in sql:
            assert "WHERE day BETWEEN @start AND @end" in sql


# --- Finding M6: a granularity-neutral grouping key on all three
# period-aware row types, not just VolumeRow. ---


@pytest.mark.asyncio
async def test_state_trend_period_rows_carry_a_bucket_sibling() -> None:
    """`month` holds "2026-W29" on the period path and a real "YYYY-MM" on
    the unfiltered path, and cannot be renamed (patch 0020 reads it as a
    month). `bucket` is the field a period-scoped consumer groups by
    without having to know which of the two it is looking at."""
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

    assert result.state_trend == [
        StateTrendRow(
            month="2026-W29",
            status="resolved",
            division="Sales",
            cases=12,
            month_start=None,
            bucket="2026-W29",
        )
    ]


@pytest.mark.asyncio
async def test_volume_by_type_division_period_rows_carry_a_bucket_sibling() -> None:
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_volume_by_type_division_daily": [
                {
                    "month": "2026-W29",
                    "channel": "WhatsApp",
                    "case_type": "Inquiry",
                    "division": "Sales",
                    "volume": 42,
                }
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_volume_by_type_division(period=period)

    assert result.volume == [
        VolumeByTypeDivisionRow(
            month="2026-W29",
            channel="WhatsApp",
            case_type="Inquiry",
            division="Sales",
            volume=42,
            month_start=None,
            bucket="2026-W29",
        )
    ]


@pytest.mark.asyncio
async def test_bucket_matches_month_at_every_granularity_on_all_three_blocks() -> None:
    """The frontend fix aggregates by `bucket`, so it must be populated
    identically to `month` whatever granularity was asked for -- day, week
    or month -- on all three period-aware blocks, not just VolumeRow."""
    for granularity, key in (
        ("day", "2026-07-20"),
        ("week", "2026-W29"),
        ("month", "2026-07"),
    ):
        period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), granularity)
        settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
        client = _FakeClient(
            {
                "v_volume_daily": [{"month": key, "channel": "web", "volume": 1}],
                "v_state_trend_daily": [
                    {"month": key, "status": "resolved", "division": "Sales", "cases": 2}
                ],
                "v_volume_by_type_division_daily": [
                    {
                        "month": key,
                        "channel": "web",
                        "case_type": "Inquiry",
                        "division": "Sales",
                        "volume": 3,
                    }
                ],
            }
        )
        q = BigQueryMetricsQuery(settings, client=client)

        dashboard = await q.fetch_dashboard(period=period)
        lifecycle = await q.fetch_lifecycle(period=period)
        by_type = await q.fetch_volume_by_type_division(period=period)

        for row in (dashboard.volume[0], lifecycle.state_trend[0], by_type.volume[0]):
            assert row.bucket == key == row.month, (granularity, row)


# --- Finding M2: the no-period payload pinned at ROW level, not just at
# the top-level block names. `bucket`/`month_start` are new declared
# fields, so every no-period row's key set changed; the pre-existing
# regression tests assert only the outer key set and could not see it. ---


@pytest.mark.asyncio
async def test_no_period_row_key_sets_are_pinned_exactly() -> None:
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_volume_by_month_channel": [{"month": "2026-06", "channel": "web", "volume": 140}],
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
                    "month_start": date(2026, 6, 1),
                    "status": "resolved",
                    "division": "Sales",
                    "cases": 45,
                }
            ],
            "v_volume_by_type_division": [
                {
                    "month": "2026-06",
                    "month_start": date(2026, 6, 1),
                    "channel": "web",
                    "case_type": "Complaint",
                    "division": "Sales",
                    "volume": 25,
                }
            ],
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    dashboard = asdict(await q.fetch_dashboard())
    lifecycle = asdict(await q.fetch_lifecycle())
    by_type = asdict(await q.fetch_volume_by_type_division())

    assert set(dashboard["volume"][0]) == {"month", "channel", "volume", "bucket"}
    assert dashboard["volume"][0]["bucket"] is None
    assert set(lifecycle["cases"][0]) == {
        "conversation_id",
        "channel",
        "division",
        "department",
        "dealer",
        "status",
        "created_at",
        "first_response_at",
        "resolved_at",
        "first_response_minutes",
        "resolution_minutes",
        "reopen_count",
    }
    assert set(lifecycle["state_trend"][0]) == {
        "month",
        "status",
        "division",
        "cases",
        "month_start",
        "bucket",
    }
    # month_start is genuinely populated on this path (it is a real column
    # on the month-grain view); bucket is the redundant sibling, null here.
    assert lifecycle["state_trend"][0]["month_start"] == date(2026, 6, 1)
    assert lifecycle["state_trend"][0]["bucket"] is None
    assert set(by_type["volume"][0]) == {
        "month",
        "month_start",
        "channel",
        "case_type",
        "division",
        "volume",
        "bucket",
    }
    assert by_type["volume"][0]["month_start"] == date(2026, 6, 1)
    assert by_type["volume"][0]["bucket"] is None


# --- Finding I6: a fail-open fallback after a failed BigQuery client init
# must not be indistinguishable from a deliberate choice of mock data. ---


def test_deliberate_mock_provider_reports_unfiltered() -> None:
    port = build_metrics_query_port(Settings(metrics_provider="mock"))
    assert isinstance(port, MockMetricsQuery)
    metrics = asyncio.run(port.fetch_dashboard())
    assert all(scope.status == "unfiltered" for scope in metrics.scopes.values())


def test_fallback_after_failed_client_init_reports_unavailable_not_unfiltered(monkeypatch) -> None:
    """`MockMetricsQuery`'s canned rows (682 cases, "2026-06") render as a
    perfectly plausible all-time figure. When they are a *fallback* rather
    than a choice, nothing in the payload could say the numbers were
    invented -- the badge read "All time". Reporting "unavailable" is what
    lets a client-facing page render "temporarily unavailable" instead."""

    def _boom(_settings):
        raise RuntimeError("could not create bigquery client")

    monkeypatch.setattr("chatbot.features.metrics.query_adapter.BigQueryMetricsQuery", _boom)
    port = build_metrics_query_port(Settings(metrics_provider="bigquery"))

    assert isinstance(port, MockMetricsQuery)  # still fail-open, still no raise
    dashboard = asyncio.run(port.fetch_dashboard())
    lifecycle = asyncio.run(port.fetch_lifecycle())
    by_type = asyncio.run(port.fetch_volume_by_type_division())
    assert all(scope.status == "unavailable" for scope in dashboard.scopes.values())
    assert all(scope.status == "unavailable" for scope in lifecycle.scopes.values())
    assert all(scope.status == "unavailable" for scope in by_type.scopes.values())


def test_fallback_still_hides_scopes_from_the_no_period_json_shape() -> None:
    """The degraded scope must not become a new top-level key on the
    unfiltered payload -- that would break the byte-identical guarantee on
    exactly the deployments that are already misconfigured."""
    payload = asdict(asyncio.run(MockMetricsQuery(degraded=True).fetch_dashboard()))
    assert "scopes" not in payload


def test_fallback_after_failed_client_init_returns_empty_for_the_five_unscoped_methods(
    monkeypatch,
) -> None:
    """`fetch_departments`/`fetch_callcenter`/`fetch_dealer_escalation`/
    `fetch_sla_buckets`/`fetch_case_aging` have no scopes channel and can't
    gain one without changing the response shape the deployed SPA already
    reads. On the degraded fallback path they must return empty lists
    instead of the canned rows -- five Weekly Report sections rendering
    "no data" is the honest failure mode; five tables of someone else's
    fabricated figures under an "All time" badge is not."""

    def _boom(_settings):
        raise RuntimeError("could not create bigquery client")

    monkeypatch.setattr("chatbot.features.metrics.query_adapter.BigQueryMetricsQuery", _boom)
    port = build_metrics_query_port(Settings(metrics_provider="bigquery"))

    departments = asyncio.run(port.fetch_departments())
    callcenter = asyncio.run(port.fetch_callcenter())
    dealer_escalation = asyncio.run(port.fetch_dealer_escalation())
    sla_buckets = asyncio.run(port.fetch_sla_buckets())
    case_aging = asyncio.run(port.fetch_case_aging())

    assert departments == DepartmentsMetrics(dept_pic=[], reopen=[], category_by_vehicle_model=[])
    assert callcenter == CallCentreMetrics(
        sla=[],
        tasks_per_agent=[],
        first_response=[],
        resolution_time=[],
        complaint_types=[],
        peak_hours=[],
        nps_by_agent=[],
    )
    assert dealer_escalation == DealerEscalationMetrics(by_dealer=[], slowest_cases=[])
    assert sla_buckets == SlaBucketMetrics(buckets=[])
    assert case_aging == CaseAgingMetrics(cases=[])


def test_deliberate_mock_provider_still_returns_canned_rows_for_the_five_unscoped_methods() -> None:
    """The degraded-only empty-list behaviour must not leak into the
    deliberate-mock path (`metrics_provider == "mock"`) -- those rows
    are still the intended dev/test answer, unchanged by this fix."""
    port = build_metrics_query_port(Settings(metrics_provider="mock"))

    departments = asyncio.run(port.fetch_departments())
    callcenter = asyncio.run(port.fetch_callcenter())
    dealer_escalation = asyncio.run(port.fetch_dealer_escalation())
    sla_buckets = asyncio.run(port.fetch_sla_buckets())
    case_aging = asyncio.run(port.fetch_case_aging())

    assert departments.dept_pic and departments.reopen and departments.category_by_vehicle_model
    assert callcenter.sla and callcenter.tasks_per_agent and callcenter.nps_by_agent
    assert dealer_escalation.by_dealer and dealer_escalation.slowest_cases
    assert sla_buckets.buckets
    assert case_aging.cases


# --- A tenant with no warehouse must render blank reports, not another
# tenant's canned demo rows. `metrics_provider` defaults to "noop", and that
# used to hand every reporting page `MockMetricsQuery`'s Proton-flavoured
# fixtures ("Dealer KL", "Aftersales"/"Ali", "e.MAS 5"), which read as real
# figures on a freshly provisioned tenant. Canned rows now require an
# explicit `metrics_provider="mock"`. ---


def test_default_provider_returns_empty_not_canned_rows() -> None:
    port = build_metrics_query_port(Settings())
    assert isinstance(port, EmptyMetricsQuery)

    assert asyncio.run(port.fetch_departments()) == DepartmentsMetrics(
        dept_pic=[], reopen=[], category_by_vehicle_model=[]
    )
    assert asyncio.run(port.fetch_dealer_escalation()) == DealerEscalationMetrics(
        by_dealer=[], slowest_cases=[]
    )
    assert asyncio.run(port.fetch_sla_buckets()) == SlaBucketMetrics(buckets=[])
    assert asyncio.run(port.fetch_case_aging()) == CaseAgingMetrics(cases=[])
    assert asyncio.run(port.fetch_callcenter()) == CallCentreMetrics(
        sla=[],
        tasks_per_agent=[],
        first_response=[],
        resolution_time=[],
        complaint_types=[],
        peak_hours=[],
        nps_by_agent=[],
    )
    assert asyncio.run(port.fetch_anomalies()) == []
    assert asyncio.run(port.fetch_hourly_anomalies()) == ([], False)

    dashboard = asyncio.run(port.fetch_dashboard())
    assert (
        dashboard.volume
        == dashboard.resolution
        == dashboard.csat
        == dashboard.nps
        == dashboard.speed
        == dashboard.fallback
        == dashboard.bounce
        == dashboard.quality
        == []
    )
    lifecycle = asyncio.run(port.fetch_lifecycle())
    assert lifecycle.cases == [] and lifecycle.state_trend == []
    assert asyncio.run(port.fetch_volume_by_type_division()).volume == []
    assert asyncio.run(port.fetch_by_tag()).by_tag == []
    after_hours = asyncio.run(port.fetch_after_hours())
    assert after_hours.volume == [] and after_hours.first_response == []


def test_empty_port_reports_unfiltered_not_unavailable() -> None:
    """Nothing is broken on a warehouse-less tenant, so the badge must not
    say "temporarily unavailable" (the degraded fallback's meaning). The
    blocks are honestly empty and honestly all-time."""
    metrics = asyncio.run(build_metrics_query_port(Settings()).fetch_dashboard())
    assert all(scope.status == "unfiltered" for scope in metrics.scopes.values())


def test_empty_port_hides_scopes_from_the_json_shape() -> None:
    """Same byte-identical-payload guard the mock and BigQuery adapters carry."""
    payload = asdict(asyncio.run(build_metrics_query_port(Settings()).fetch_dashboard()))
    assert "scopes" not in payload


def test_canned_rows_now_require_explicit_mock_provider() -> None:
    port = build_metrics_query_port(Settings(metrics_provider="mock"))
    assert isinstance(port, MockMetricsQuery)
    assert asyncio.run(port.fetch_departments()).dept_pic
