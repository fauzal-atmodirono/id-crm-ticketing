from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.export_router import build_metrics_export_router
from chatbot.features.metrics.query_port import MockMetricsQuery


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_metrics_export_router(MockMetricsQuery()))
    return TestClient(app)


def test_export_xlsx() -> None:
    r = _client().get("/metrics/export?format=xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]


def test_export_pdf() -> None:
    r = _client().get("/metrics/export?format=pdf")
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


def test_export_bad_format_is_400() -> None:
    assert _client().get("/metrics/export?format=csv").status_code == 400
