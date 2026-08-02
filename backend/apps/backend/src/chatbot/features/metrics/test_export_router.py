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


def test_dealer_escalation_csv_export() -> None:
    response = _client().get("/metrics/dealer-escalation/export?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"


def test_sla_buckets_csv_export() -> None:
    response = _client().get("/metrics/sla-buckets/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"


def test_case_aging_csv_export() -> None:
    response = _client().get("/metrics/case-aging/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]


def test_volume_by_type_csv_export() -> None:
    response = _client().get("/metrics/volume-by-type/export")
    assert response.status_code == 200


def test_departments_csv_export() -> None:
    response = _client().get("/metrics/departments/export")
    assert response.status_code == 200
