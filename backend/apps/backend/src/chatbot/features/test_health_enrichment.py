"""Unit tests for Enriched Health Check Endpoint (P13 Task 2)."""

from __future__ import annotations

import pytest

from chatbot.features.health_enrichment import get_enriched_health_status


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy()


async def test_enriched_health_returns_ok_overall_status(settings) -> None:
    res = await get_enriched_health_status(settings)
    assert res["status"] == "ok"
    assert "subsystems" in res
    assert "database" in res["subsystems"]
    assert "crm" in res["subsystems"]
    assert "voice" in res["subsystems"]
