import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository
from chatbot.features.rsa.rsa_router import build_rsa_router
from chatbot.platform.config import get_settings


@pytest.fixture
def client():
    settings = get_settings().model_copy(update={"faq_admin_api_key": "test-key"})
    repo = InMemoryRsaRepository()
    app = FastAPI()
    app.include_router(build_rsa_router(repo, settings))
    return TestClient(app)


def _headers():
    return {"x-api-key": "test-key"}


def test_create_requires_api_key(client):
    response = client.post("/rsa/incidents", json={
        "incident_date": "2026-07-01", "vehicle_no": "VPP8636", "cause": "Flat Tyre",
    })
    assert response.status_code == 401


def test_create_list_get_incident(client):
    create_res = client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "VPP8636", "cause": "Flat Tyre"},
        headers=_headers(),
    )
    assert create_res.status_code == 200
    incident_id = create_res.json()["id"]

    list_res = client.get("/rsa/incidents", headers=_headers())
    assert list_res.status_code == 200
    assert len(list_res.json()["incidents"]) == 1

    get_res = client.get(f"/rsa/incidents/{incident_id}", headers=_headers())
    assert get_res.status_code == 200
    assert get_res.json()["vehicle_no"] == "VPP8636"


def test_get_missing_incident_404(client):
    response = client.get("/rsa/incidents/does-not-exist", headers=_headers())
    assert response.status_code == 404


def test_update_and_delete_incident(client):
    incident_id = client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "VPP8636", "cause": "Flat Tyre"},
        headers=_headers(),
    ).json()["id"]

    patch_res = client.patch(
        f"/rsa/incidents/{incident_id}", json={"remarks": "resolved"}, headers=_headers()
    )
    assert patch_res.status_code == 200

    delete_res = client.delete(f"/rsa/incidents/{incident_id}", headers=_headers())
    assert delete_res.status_code == 200
    assert client.get(f"/rsa/incidents/{incident_id}", headers=_headers()).status_code == 404


def test_aggregate_endpoint(client):
    client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "A1", "cause": "Flat Tyre"},
        headers=_headers(),
    )
    response = client.get("/rsa/incidents/aggregate", headers=_headers())
    assert response.status_code == 200
    assert response.json()["by_cause"] == [{"cause": "Flat Tyre", "count": 1}]


def test_csv_export(client):
    client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "A1", "cause": "Flat Tyre"},
        headers=_headers(),
    )
    response = client.get("/rsa/incidents/export?format=csv", headers=_headers())
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
