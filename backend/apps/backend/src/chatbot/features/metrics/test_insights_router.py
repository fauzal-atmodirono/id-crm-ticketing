from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.query_port import MockMetricsQuery


def _client(key="secret"):
    class S:
        metrics_api_key = key

    app = FastAPI()
    app.include_router(build_metrics_insights_router(MockMetricsQuery(), S()))
    return TestClient(app)


def test_departments_requires_key():
    assert _client().get("/metrics/departments").status_code == 401


def test_departments_ok_with_key():
    r = _client().get("/metrics/departments", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert "dept_pic" in r.json()


def test_callcenter_and_lifecycle_ok():
    c = _client()
    assert c.get("/metrics/callcenter", headers={"x-api-key": "secret"}).status_code == 200
    assert c.get("/metrics/lifecycle", headers={"x-api-key": "secret"}).status_code == 200
