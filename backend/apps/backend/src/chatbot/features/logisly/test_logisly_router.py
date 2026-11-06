from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.logisly import trips
from chatbot.features.logisly.router import ChatwootDriverChannel, build_logisly_router

CW = "http://cw:3000/api/v1/accounts/1"


def _settings(**over):
    base = dict(
        proton_backend_key="k",
        logisly_demo_driver_phone="+62 811-0000-1111",
        logisly_driver_inbox_id=4,
        chatwoot_api_url="http://cw:3000",
        chatwoot_account_id=1,
        chatwoot_api_token="t",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _client(settings):
    app = FastAPI()
    app.include_router(build_logisly_router(settings))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(trips, "TRIPS", trips.TripStore.seeded())
    monkeypatch.setattr("chatbot.features.logisly.router.TRIPS", trips.TRIPS)
    trips.ACTIVE_SESSIONS.clear()


def test_routes_need_the_api_key_but_console_does_not():
    c = _client(_settings())
    assert c.get("/logisly/trips").status_code == 401
    assert c.get("/logisly/trips", headers={"X-API-Key": "k"}).status_code == 200
    page = c.get("/logisly/console")
    assert page.status_code == 200 and "Tracking Simulator" in page.text and "k" != ""


@respx.mock
def test_anomaly_opens_conversation_and_registers_trip_context():
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [{"id": 13, "phone_number": "+6281100001111"}]})
    )
    respx.get(f"{CW}/contacts/13/conversations").mock(
        return_value=httpx.Response(200, json={"payload": [
            {"id": 21, "inbox_id": 4, "status": "open"},
            {"id": 5, "inbox_id": 1, "status": "open"},
        ]})
    )
    resolve = respx.post(f"{CW}/conversations/21/toggle_status").mock(return_value=httpx.Response(200, json={}))
    create = respx.post(f"{CW}/conversations").mock(return_value=httpx.Response(200, json={"id": 22}))

    r = _client(_settings()).post(
        "/logisly/anomaly", json={"order_id": "lgs-24017", "kind": "gps_stop"}, headers={"X-API-Key": "k"}
    )

    assert r.status_code == 200 and r.json()["conversation_id"] == 22
    assert resolve.call_count == 1  # only the driver-inbox conversation is closed
    sent = create.calls.last.request.content
    assert b'"source_id":"whatsapp:+6281100001111"' in sent and b"LGS-24017" in sent and b'"pending"' in sent
    ctx = trips.context_for_session("crm-22")
    assert "LGS-24017" in ctx and "Do not ask for the order number" in ctx
    assert trips.TRIPS.get("LGS-24017")["events"][-1]["status"] == "alert:gps_stop"
    assert trips.TRIPS.get("LGS-24017")["status"] == "on_trip"


def test_anomaly_validates_order_kind_and_configuration():
    c = _client(_settings())
    h = {"X-API-Key": "k"}
    assert c.post("/logisly/anomaly", json={"order_id": "LGS-0", "kind": "gps_stop"}, headers=h).status_code == 404
    assert c.post("/logisly/anomaly", json={"order_id": "LGS-24017", "kind": "x"}, headers=h).status_code == 422
    c2 = _client(_settings(logisly_demo_driver_phone=""))
    assert c2.post("/logisly/anomaly", json={"order_id": "LGS-24017", "kind": "no_pod"}, headers=h).status_code == 503


def test_driver_started_conversation_has_no_trip_context():
    assert trips.context_for_session("crm-999") == ""


@respx.mock
async def test_contact_is_created_when_phone_not_found():
    respx.get(f"{CW}/contacts/search").mock(return_value=httpx.Response(200, json={"payload": []}))
    respx.post(f"{CW}/contacts").mock(
        return_value=httpx.Response(200, json={"payload": {"contact": {"id": 77}}})
    )
    channel = ChatwootDriverChannel(_settings())
    assert await channel.find_or_create_contact("+6281100001111", "Pak Budi") == 77
