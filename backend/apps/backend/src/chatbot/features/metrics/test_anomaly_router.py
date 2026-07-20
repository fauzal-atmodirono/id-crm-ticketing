from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.anomaly_router import build_metrics_anomaly_router
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.platform.config import Settings


def test_anomalies_endpoint_flags_the_spike() -> None:
    app = FastAPI()
    app.include_router(build_metrics_anomaly_router(MockMetricsQuery(), Settings()))
    r = TestClient(app).get("/metrics/anomalies")
    assert r.status_code == 200
    chans = [a["channel"] for a in r.json()["anomalies"]]
    assert "whatsapp" in chans and "web" not in chans
