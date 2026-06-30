from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.metrics.query_adapter import (
    BigQueryMetricsQuery,
    build_metrics_query_port,
)
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.platform.config import Settings


class _FakeJob:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeClient:
    """Returns canned rows based on the view name in the SQL."""

    def __init__(self, by_view: dict[str, list[dict[str, Any]]]) -> None:
        self._by_view = by_view
        self.queries: list[str] = []

    def query(self, sql: str) -> _FakeJob:
        self.queries.append(sql)
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
