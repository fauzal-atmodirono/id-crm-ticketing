from __future__ import annotations

from fastapi.testclient import TestClient

from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.main import bootstrap_application
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


def _client() -> TestClient:
    # One Settings for both, which is what `main.py` does: the router needs it
    # for the P9 freshness stamp. These tests assert which blocks are PRESENT,
    # never the exact key set, so they hold with the stamp on or off -- the exact
    # key set is pinned in test_dashboard_router_period.py (flag off) and
    # test_freshness_contract.py (both).
    settings = Settings()
    app = create_app(settings)
    app.include_router(build_metrics_query_router(MockMetricsQuery(), settings))
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


def test_bootstrap_app_serves_dashboard() -> None:
    client = TestClient(bootstrap_application())
    res = client.get("/metrics/dashboard")
    assert res.status_code == 200
    assert "volume" in res.json()
