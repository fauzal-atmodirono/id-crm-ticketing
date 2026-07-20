"""Integration smoke tests: boot the full app and assert the new routes exist."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chatbot.main import bootstrap_application


def _client() -> TestClient:
    return TestClient(bootstrap_application())


def test_export_xlsx_route_is_registered_and_returns_xlsx() -> None:
    r = _client().get("/metrics/export?format=xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"


def test_anomalies_route_is_registered_and_returns_anomalies_key() -> None:
    r = _client().get("/metrics/anomalies")
    assert r.status_code == 200
    assert "anomalies" in r.json()
