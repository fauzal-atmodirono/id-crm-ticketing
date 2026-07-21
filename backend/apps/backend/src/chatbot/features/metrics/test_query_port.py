from __future__ import annotations

import asyncio

import pytest

from chatbot.features.metrics.query_port import (
    CallCentreMetrics,
    DashboardMetrics,
    DepartmentsMetrics,
    MockMetricsQuery,
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
