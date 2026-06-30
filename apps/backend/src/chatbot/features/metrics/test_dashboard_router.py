from __future__ import annotations

from fastapi.testclient import TestClient

from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


def _client() -> TestClient:
    app = create_app(Settings())
    app.include_router(build_metrics_query_router(MockMetricsQuery()))
    return TestClient(app)


def test_dashboard_returns_all_eight_blocks() -> None:
    res = _client().get("/metrics/dashboard")
    assert res.status_code == 200
    body = res.json()
    for block in (
        "volume",
        "resolution",
        "csat",
        "nps",
        "speed",
        "fallback",
        "bounce",
        "quality",
    ):
        assert block in body
        assert isinstance(body[block], list)
    assert body["volume"][0]["channel"]
    assert body["nps"][0]["promoters"] >= 0


def test_dashboard_requires_no_auth_header() -> None:
    # POC: read endpoint is open (no X-API-Key). 200 with no headers.
    assert _client().get("/metrics/dashboard").status_code == 200
