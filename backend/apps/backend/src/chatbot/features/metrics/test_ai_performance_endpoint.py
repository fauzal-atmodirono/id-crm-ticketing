"""Wave 2 task 27 -- `/metrics/ai-performance` serves the eight AI reports
(RFP 2026_028 4.56) from one endpoint so the Bot report page makes one call.

Fixture rows stand in for BigQuery (decision D2: no live BigQuery in tests).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.period import PeriodRange
from chatbot.features.metrics.query_adapter import BigQueryMetricsQuery
from chatbot.features.metrics.query_port import (
    AI_PERFORMANCE_VIEWS,
    EmptyMetricsQuery,
    MockMetricsQuery,
)
from chatbot.platform.config import Settings


class _Job:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self._rows


class _Client:
    def __init__(self, by_view: dict[str, list[dict[str, Any]]], fail: str | None = None) -> None:
        self._by_view = by_view
        self._fail = fail
        self.queries: list[str] = []
        self.configs: list[Any] = []

    def query(self, sql: str, job_config: Any = None) -> _Job:
        self.queries.append(sql)
        self.configs.append(job_config)
        if self._fail and f".{self._fail}`" in sql:
            raise RuntimeError("boom")
        for view, rows in self._by_view.items():
            if f".{view}`" in sql:
                return _Job(rows)
        return _Job([])


def _adapter(client: _Client) -> BigQueryMetricsQuery:
    return BigQueryMetricsQuery(
        Settings(bigquery_project_id="proj", bigquery_dataset="ds"), client=client
    )


def test_the_eight_reports_are_the_eight_rfp_items() -> None:
    assert set(AI_PERFORMANCE_VIEWS) == {
        "resolution",  # 1
        "ai_vs_human",  # 2
        "csat",  # 3
        "escalation_reasons",  # 4
        "accuracy",  # 5
        "deflection",  # 6
        "root_cause",  # 7
        "kb_improvement",  # 8
    }


@pytest.mark.asyncio
async def test_ai_deflection_rate_view_rows_pass_through_with_definition() -> None:
    rows = [
        {
            "day": date(2026, 11, 2),
            "channel": "WhatsApp",
            "cases": 10,
            "deflected": 4,
            "human_involved": 6,
            "deflection_rate": 0.4,
            "deflection_definition": "Deflected means ...",
        }
    ]
    m = await _adapter(_Client({"v_ai_deflection": rows})).fetch_ai_performance()
    out = m.blocks["deflection"]
    assert out[0]["deflection_rate"] == 0.4 and out[0]["day"] == "2026-11-02"
    assert out[0]["deflection_definition"].startswith("Deflected")


@pytest.mark.asyncio
async def test_ai_root_cause_view_rows_pass_through_ranked_by_cases() -> None:
    rows = [
        {"day": date(2026, 11, 2), "category": "Service", "subcategory": "Brake", "cases": 3,
         "escalated_cases": 1, "week_complete": True, "prev_week_cases": 0, "wow_change": 3, "wow_growth_rate": None,
         "root_cause_basis": "b"},
        {"day": date(2026, 11, 2), "category": "Sales", "subcategory": "Price", "cases": 9,
         "escalated_cases": 2, "prev_week_cases": 6, "wow_change": 3, "wow_growth_rate": 0.5,
         "root_cause_basis": "b"},
    ]
    m = await _adapter(_Client({"v_ai_root_cause": rows})).fetch_ai_performance()
    out = m.blocks["root_cause"]
    assert [r["cases"] for r in out] == [9, 3], "largest issue first"
    assert out[1]["wow_growth_rate"] is None, "new issue has no growth rate"


@pytest.mark.asyncio
async def test_ai_kb_improvement_view_rows_pass_through() -> None:
    rows = [{"day": date(2026, 11, 3), "topic": "Warranty", "unanswered_cases": 7,
             "faq_suggestion": "Add", "kb_basis": "b"}]
    m = await _adapter(_Client({"v_ai_kb_improvement": rows})).fetch_ai_performance()
    assert m.blocks["kb_improvement"][0]["unanswered_cases"] == 7


@pytest.mark.asyncio
async def test_ai_accuracy_and_the_rest_each_read_their_own_view() -> None:
    client = _Client({})
    await _adapter(client).fetch_ai_performance()
    joined = " ".join(client.queries)
    for view in ("v_ai_resolution", "v_ai_vs_human", "v_csat_by_resolution",
                 "v_ai_escalation_reasons", "v_ai_accuracy", "v_ai_deflection",
                 "v_ai_root_cause", "v_ai_kb_improvement"):
        assert f".{view}`" in joined, view


@pytest.mark.asyncio
async def test_period_filters_on_day_with_bound_parameters() -> None:
    client = _Client({})
    period = PeriodRange(date(2026, 11, 2), date(2026, 11, 8), "week")
    m = await _adapter(client).fetch_ai_performance(period)
    root = [q for q in client.queries if "v_ai_root_cause" in q]
    others = [q for q in client.queries if "v_ai_root_cause" not in q]
    assert all("WHERE day BETWEEN @start AND @end" in q for q in others)
    # a week overlapping the range counts even when its Monday is before `from`
    assert "DATE_ADD(day, INTERVAL 6 DAY) >= @start AND day <= @end" in root[0]
    assert all(c is not None for c in client.configs)
    assert m.scopes["deflection"].status == "ok"


@pytest.mark.asyncio
async def test_one_failed_view_is_unavailable_not_a_500_and_not_empty_ok() -> None:
    m = await _adapter(_Client({}, fail="v_ai_accuracy")).fetch_ai_performance()
    assert m.blocks["accuracy"] == []
    assert m.scopes["accuracy"].status == "unavailable"
    assert m.scopes["deflection"].status == "unfiltered"


@pytest.mark.asyncio
async def test_empty_and_mock_ports_serve_the_shape() -> None:
    empty = await EmptyMetricsQuery().fetch_ai_performance()
    assert set(empty.blocks) == set(AI_PERFORMANCE_VIEWS)
    assert all(v == [] for v in empty.blocks.values())
    mock = await MockMetricsQuery().fetch_ai_performance()
    assert set(mock.blocks) == set(AI_PERFORMANCE_VIEWS)


def _client(port: Any) -> TestClient:
    class S:
        metrics_api_key = "secret"

    app = FastAPI()
    app.include_router(build_metrics_insights_router(port, S()))
    return TestClient(app)


def test_endpoint_requires_the_key() -> None:
    assert _client(MockMetricsQuery()).get("/metrics/ai-performance").status_code == 401


def test_endpoint_returns_blocks_and_scopes() -> None:
    r = _client(MockMetricsQuery()).get("/metrics/ai-performance", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["blocks"]) == set(AI_PERFORMANCE_VIEWS)
    assert "deflection" in body["scopes"]


def test_endpoint_rejects_a_bad_period_with_400() -> None:
    r = _client(MockMetricsQuery()).get(
        "/metrics/ai-performance",
        params={"from": "2026-11-09", "to": "2026-11-01", "granularity": "week"},
        headers={"x-api-key": "secret"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_period_bound_values_reach_the_query_and_scope_carries_it() -> None:
    client = _Client({"v_ai_accuracy": [{"day": date(2026, 11, 3), "ai_resolved": 2}]})
    period = PeriodRange(date(2026, 11, 1), date(2026, 11, 30), "day")
    m = await _adapter(client).fetch_ai_performance(period)
    params = {p.name: p.value for p in client.configs[0].query_parameters}
    assert params == {"start": date(2026, 11, 1), "end": date(2026, 11, 30)}
    assert m.scopes["accuracy"].period == period
    assert m.blocks["accuracy"][0]["day"] == "2026-11-03"


def test_endpoint_accepts_a_day_granularity_period() -> None:
    r = _client(MockMetricsQuery()).get(
        "/metrics/ai-performance",
        params={"from": "2026-11-01", "to": "2026-11-30", "granularity": "day"},
        headers={"x-api-key": "secret"},
    )
    assert r.status_code == 200
