from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from chatbot.features.metrics.period import PeriodRange
from chatbot.features.metrics.query_adapter import (
    BigQueryMetricsQuery,
    build_metrics_query_port,
)
from chatbot.features.metrics.query_port import (
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


# --- Task 2: range-aware queries -------------------------------------------


@pytest.mark.asyncio
async def test_period_none_emits_byte_identical_sql_to_today() -> None:
    """Regression guard: omitting a period must not change the query text
    for a view that *does* support period filtering (v_state_trend) — this
    is the exact SQL the adapter has always run."""
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({"v_state_trend": []})
    q = BigQueryMetricsQuery(settings, client=client)

    await q.fetch_lifecycle()  # period omitted entirely

    state_trend_queries = [sql for sql in client.queries if "v_state_trend" in sql]
    assert state_trend_queries == ["SELECT * FROM `proj.ds.v_state_trend`"]
    # no WHERE, no job_config -- unfiltered call site is untouched
    assert client.job_configs[client.queries.index(state_trend_queries[0])] is None


@pytest.mark.asyncio
async def test_period_none_leaves_dashboard_volume_query_untouched() -> None:
    """Same regression guard for fetch_dashboard: no period -> the adapter
    still queries v_volume_by_month_channel, not v_volume_daily."""
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({"v_volume_by_month_channel": []})
    q = BigQueryMetricsQuery(settings, client=client)

    await q.fetch_dashboard()

    assert any(sql == "SELECT * FROM `proj.ds.v_volume_by_month_channel`" for sql in client.queries)
    assert not any("v_volume_daily" in sql for sql in client.queries)


@pytest.mark.asyncio
async def test_period_adds_where_clause_with_named_parameters_not_literals() -> None:
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_state_trend": [
                {"month": "2026-07", "status": "resolved", "division": "Sales", "cases": 12}
            ]
        }
    )
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle(period=period)

    state_trend_idx = next(i for i, sql in enumerate(client.queries) if "v_state_trend" in sql)
    emitted_sql = client.queries[state_trend_idx]
    job_config = client.job_configs[state_trend_idx]

    assert "WHERE month_start BETWEEN @start AND @end" in emitted_sql
    # the dates themselves must never be string-formatted into the query text
    assert "2026-07-17" not in emitted_sql
    assert "2026-07-23" not in emitted_sql

    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["start"] == date(2026, 7, 17)
    assert params["end"] == date(2026, 7, 23)
    assert len(result.state_trend) == 1
    assert result.state_trend[0].cases == 12


@pytest.mark.asyncio
async def test_period_query_failure_degrades_to_empty_block_not_500() -> None:
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    # Simulates a widened DDL whose deployed view hasn't been re-created by
    # ensure_views() yet -- the WHERE month_start predicate fails because the
    # live view doesn't have that column, same as any other query failure.
    client = _FakeClient({}, fail_views=frozenset({"v_state_trend"}))
    q = BigQueryMetricsQuery(settings, client=client)

    result = await q.fetch_lifecycle(period=period)  # must not raise

    assert result.state_trend == []


@pytest.mark.asyncio
async def test_volume_by_type_division_period_uses_named_parameters() -> None:
    period = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient(
        {
            "v_volume_by_type_division": [
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

    idx = next(i for i, sql in enumerate(client.queries) if "v_volume_by_type_division" in sql)
    emitted_sql = client.queries[idx]
    assert "WHERE month_start BETWEEN @start AND @end" in emitted_sql
    assert "2026-06-01" not in emitted_sql
    assert "2026-06-30" not in emitted_sql
    params = {p.name: p.value for p in client.job_configs[idx].query_parameters}
    assert params["start"] == date(2026, 6, 1)
    assert params["end"] == date(2026, 6, 30)
    assert len(result.volume) == 1


@pytest.mark.asyncio
async def test_dashboard_volume_with_period_queries_volume_daily_with_parameters() -> None:
    """A period on fetch_dashboard buckets volume from v_volume_daily (day
    grain) instead of v_volume_by_month_channel (month grain) -- a week
    can't be recovered from a pre-aggregated monthly total."""
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
    assert "WHERE day BETWEEN @start AND @end" in emitted_sql
    assert "2026-07-17" not in emitted_sql
    assert "2026-07-23" not in emitted_sql
    params = {p.name: p.value for p in client.job_configs[idx].query_parameters}
    assert params["start"] == date(2026, 7, 17)
    assert params["end"] == date(2026, 7, 23)
    assert metrics.volume == [VolumeRow(month="2026-W29", channel="web", volume=42)]


@pytest.mark.asyncio
async def test_dashboard_period_does_not_leak_into_unfiltered_blocks() -> None:
    """A period on fetch_dashboard only affects the volume block -- the
    other 7 blocks have no date column at all and must stay unfiltered."""
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

    # every non-volume block still ran the plain, unfiltered SELECT *
    for view in (
        "v_resolution_split",
        "v_csat",
        "v_nps",
        "v_speed_of_response",
        "v_fallback_rate",
        "v_bounce_rate",
        "v_quality",
    ):
        idx = next(i for i, sql in enumerate(client.queries) if view in sql)
        assert client.queries[idx] == f"SELECT * FROM `proj.ds.{view}`"  # noqa: S608
        assert client.job_configs[idx] is None
    assert metrics.csat[0].avg_score == 4.2


@pytest.mark.asyncio
async def test_dashboard_volume_period_query_failure_degrades_to_empty_block() -> None:
    period = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    client = _FakeClient({}, fail_views=frozenset({"v_volume_daily"}))
    q = BigQueryMetricsQuery(settings, client=client)

    metrics = await q.fetch_dashboard(period=period)  # must not raise

    assert metrics.volume == []
