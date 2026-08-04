from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from chatbot.features.metrics.query_port import (
    CallCentreMetrics,
    DashboardMetrics,
    DepartmentsMetrics,
    LifecycleMetrics,
    MockMetricsQuery,
    VolumeByTypeDivisionMetrics,
)


@pytest.mark.asyncio
async def test_mock_returns_populated_dashboard() -> None:
    metrics = await MockMetricsQuery().fetch_dashboard()
    assert isinstance(metrics, DashboardMetrics)
    # every block is present and non-empty so the UI renders something
    assert metrics.volume and metrics.resolution and metrics.csat
    assert metrics.nps and metrics.speed and metrics.fallback
    assert metrics.bounce and metrics.quality
    # spot-check shapes
    assert metrics.volume[0].month and metrics.volume[0].channel
    assert isinstance(metrics.volume[0].volume, int)
    assert any(s.is_first_turn for s in metrics.speed)
    assert metrics.nps[0].promoters >= 0


def test_mock_departments_shape():
    m = asyncio.run(MockMetricsQuery().fetch_departments())
    assert isinstance(m, DepartmentsMetrics)
    assert m.dept_pic and m.dept_pic[0].department
    assert m.reopen and 0.0 <= (m.reopen[0].reopen_rate or 0) <= 1.0


def test_mock_callcenter_shape():
    m = asyncio.run(MockMetricsQuery().fetch_callcenter())
    assert isinstance(m, CallCentreMetrics)
    assert m.nps_by_agent and m.nps_by_agent[0].channel in ("Phone", "WhatsApp")
    assert m.peak_hours and 1 <= m.peak_hours[0].day_of_week <= 7


def test_mock_lifecycle_shape():
    m = asyncio.run(MockMetricsQuery().fetch_lifecycle())
    assert isinstance(m, LifecycleMetrics)
    assert m.cases and m.cases[0].conversation_id
    assert m.state_trend and m.state_trend[0].status


def test_mock_dashboard_marks_every_block_unfiltered():
    """MockMetricsQuery is also `build_metrics_query_port`'s fail-open
    fallback when the real BigQuery client can't init -- a period request
    that silently downgrades to this canned payload must say so via
    `scopes`, not just match the Protocol's return type (Task 2 review)."""
    m = asyncio.run(MockMetricsQuery().fetch_dashboard())
    assert set(m.scopes.keys()) == {
        "volume",
        "resolution",
        "csat",
        "nps",
        "speed",
        "fallback",
        "bounce",
        "quality",
    }
    assert all(scope.status == "unfiltered" for scope in m.scopes.values())


def test_mock_dashboard_asdict_has_no_scopes_key():
    """Same guard as the BigQuery adapter's equivalent test in
    test_query_adapter.py: `scopes` must not appear in the JSON shape
    dashboard_router.py actually serves, and the mock is a live fallback
    for that exact route, not just a test double."""
    payload = asdict(asyncio.run(MockMetricsQuery().fetch_dashboard()))
    assert "scopes" not in payload


def test_mock_lifecycle_asdict_has_no_scopes_key():
    payload = asdict(asyncio.run(MockMetricsQuery().fetch_lifecycle()))
    assert "scopes" not in payload


def test_mock_volume_by_type_division_marks_unfiltered_and_hides_scopes_from_asdict():
    m = asyncio.run(MockMetricsQuery().fetch_volume_by_type_division())
    assert isinstance(m, VolumeByTypeDivisionMetrics)
    assert m.scopes["volume"].status == "unfiltered"
    assert "scopes" not in asdict(m)
