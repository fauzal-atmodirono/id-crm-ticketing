from __future__ import annotations

import pytest

from chatbot.features.metrics.query_port import (
    DashboardMetrics,
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
