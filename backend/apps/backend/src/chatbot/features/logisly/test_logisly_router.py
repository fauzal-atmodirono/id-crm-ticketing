from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.logisly import escalation, milestones, trips
from chatbot.features.logisly.channel import ChatwootDriverChannel
from chatbot.features.logisly.router import build_logisly_router

CW = "http://cw:3000/api/v1/accounts/1"


def _settings(**over):
    base = {
        "proton_backend_key": "k",
        "logisly_demo_driver_phone": "+62 811-0000-1111",
        "logisly_driver_inbox_id": 4,
        "logisly_app_inbox_id": 0,
        "logisly_no_reply_call_seconds": 0,
        "chatwoot_api_url": "http://cw:3000",
        "chatwoot_account_id": 1,
        "chatwoot_api_token": "t",
    }
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
    milestones.PENDING_TICKETS.clear()
    escalation._COMPLETE_NOTED.clear()


def test_routes_need_the_api_key_but_console_does_not():
    c = _client(_settings())
    assert c.get("/logisly/trips").status_code == 401
    assert c.get("/logisly/trips", headers={"X-API-Key": "k"}).status_code == 200
    page = c.get("/logisly/console")
    assert page.status_code == 200 and "Tracking Simulator" in page.text


@respx.mock
def test_anomaly_opens_conversation_and_registers_trip_context():
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 13, "phone_number": "+6281100001111"}]}
        )
    )
    respx.get(f"{CW}/contacts/13/conversations").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    {"id": 21, "inbox_id": 4, "status": "open"},
                    {"id": 5, "inbox_id": 1, "status": "open"},
                ]
            },
        )
    )
    resolve = respx.post(f"{CW}/conversations/21/toggle_status").mock(
        return_value=httpx.Response(200, json={})
    )
    create = respx.post(f"{CW}/conversations").mock(
        return_value=httpx.Response(200, json={"id": 22})
    )

    r = _client(_settings()).post(
        "/logisly/anomaly",
        json={"order_id": "lgs-24017", "kind": "gps_stop"},
        headers={"X-API-Key": "k"},
    )

    assert r.status_code == 200 and r.json()["conversation_id"] == 22
    assert resolve.call_count == 1  # only the driver-inbox conversation is closed
    sent = create.calls.last.request.content
    assert (
        b'"source_id":"whatsapp:+6281100001111"' in sent
        and b"LGS-24017" in sent
        and b'"pending"' in sent
    )
    assert b"di sekitar Pool Cikarang" in sent
    ctx = trips.context_for_session("crm-22")
    assert "LGS-24017" in ctx and "Do not ask for the order number" in ctx
    assert trips.TRIPS.get("LGS-24017")["events"][-1]["status"] == "alert:gps_stop"
    assert trips.TRIPS.get("LGS-24017")["status"] == "to_pickup"  # an alert keeps the status


def test_anomaly_validates_order_kind_and_configuration():
    c = _client(_settings())
    h = {"X-API-Key": "k"}
    assert (
        c.post(
            "/logisly/anomaly", json={"order_id": "LGS-0", "kind": "gps_stop"}, headers=h
        ).status_code
        == 404
    )
    assert (
        c.post(
            "/logisly/anomaly", json={"order_id": "LGS-24017", "kind": "x"}, headers=h
        ).status_code
        == 422
    )
    c2 = _client(_settings(logisly_demo_driver_phone=""))
    assert (
        c2.post(
            "/logisly/anomaly", json={"order_id": "LGS-24017", "kind": "no_pod"}, headers=h
        ).status_code
        == 503
    )


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


DAY = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)  # 10:00 WIB
NIGHT = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)  # 23:00 WIB
H = {"X-API-Key": "k"}


def _ms_client(at=DAY):
    app = FastAPI()
    app.include_router(build_logisly_router(_settings(), now=lambda: at))
    return TestClient(app)


def _stub_chatwoot():
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 13, "phone_number": "+6281100001111"}]}
        )
    )
    respx.get(f"{CW}/contacts/13/conversations").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    create = respx.post(f"{CW}/conversations").mock(
        return_value=httpx.Response(200, json={"id": 30})
    )
    note = respx.post(f"{CW}/conversations/30/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{CW}/conversations/30/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CW}/conversations/30/labels").mock(return_value=httpx.Response(200, json={}))
    respx.patch(f"{CW}/conversations/30").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{CW}/conversations/30/toggle_priority").mock(
        return_value=httpx.Response(200, json={})
    )
    return create, note


@respx.mock
def test_milestone_opens_conversation_with_only_open_items():
    create, note = _stub_chatwoot()
    r = _ms_client().post(
        "/logisly/milestone", json={"order_id": "LGS-24021", "milestone": "at_pickup"}, headers=H
    )
    body = r.json()
    assert r.status_code == 200 and body["sent"] and body["conversation_id"] == 30
    assert {"driver_app", "do_in_app"} <= set(body["not_ok"])
    sent = create.calls.last.request.content
    assert b"logisly.example/driver-app" in sent and b"foto unit" in sent
    assert any(
        b"team-alloc" in c.request.content for c in respx.calls if c.request.method == "POST"
    )
    assert any(b"private" in c.request.content for c in note.calls)
    assert trips.context_for_session("crm-30")


@respx.mock
def test_same_milestone_twice_sends_once():
    create, _ = _stub_chatwoot()
    c = _ms_client()
    j = {"order_id": "LGS-24021", "milestone": "at_pickup"}
    assert c.post("/logisly/milestone", json=j, headers=H).json()["sent"] is True
    again = c.post("/logisly/milestone", json=j, headers=H).json()
    assert again["sent"] is False and again["deferred"] is False
    assert create.call_count == 1


@respx.mock
def test_quiet_hours_defer_unless_forced():
    create, _ = _stub_chatwoot()
    c = _ms_client(NIGHT)
    j = {"order_id": "LGS-24021", "milestone": "at_pickup"}
    r = c.post("/logisly/milestone", json=j, headers=H).json()
    assert r["deferred"] is True and r["sent"] is False and create.call_count == 0
    assert trips.TRIPS.get("LGS-24021")["milestone"] == "at_pickup"
    forced = _ms_client(NIGHT).post(
        "/logisly/milestone",
        json={"order_id": "LGS-24033", "milestone": "at_drop", "force": True},
        headers=H,
    )
    assert forced.json()["sent"] is True


@respx.mock
def test_nothing_open_sends_nothing():
    create, _ = _stub_chatwoot()
    trip = trips.TRIPS.get("LGS-24017")
    for item in milestones.CHECKLISTS["to_pickup"]:
        milestones.record_item(trip, "to_pickup", item.id, True, "", "manual", None)
    r = _ms_client().post(
        "/logisly/milestone", json={"order_id": "LGS-24017", "milestone": "to_pickup"}, headers=H
    )
    assert r.status_code == 200 and r.json()["sent"] is False and create.call_count == 0


def test_milestone_validates_order_and_code():
    c = _ms_client()
    assert (
        c.post(
            "/logisly/milestone", json={"order_id": "LGS-0", "milestone": "at_drop"}, headers=H
        ).status_code
        == 404
    )
    assert (
        c.post(
            "/logisly/milestone", json={"order_id": "LGS-24017", "milestone": "x"}, headers=H
        ).status_code
        == 422
    )
    assert c.get("/logisly/trips", headers=H).json()["milestones"][0] == [
        "assigned",
        "Pre check driver",
    ]


def test_console_has_milestone_controls():
    page = _client(_settings()).get("/logisly/console").text
    assert "/logisly/milestone" in page and "Next milestone" in page


def test_trips_carry_a_checklist_view():
    data = _client(_settings()).get("/logisly/trips", headers={"X-API-Key": "k"}).json()
    trip = data["trips"][0]
    assert trip["checklist"], "current milestone should have checklist rows"
    assert set(trip["checklist"][0]) == {"id", "label", "source", "status", "value"}


@respx.mock
def test_deferred_milestone_is_resent_with_force_and_files_adopted_ticket():
    create, _ = _stub_chatwoot()
    c = _ms_client(NIGHT)
    j = {"order_id": "LGS-24021", "milestone": "at_pickup"}
    assert c.post("/logisly/milestone", json=j, headers=H).json()["deferred"] is True
    again = c.post("/logisly/milestone", json={**j, "force": True}, headers=H).json()
    assert again["sent"] is True and again["conversation_id"] == 30
    assert create.call_count == 1
    assert any(
        b"team-alloc" in c.request.content for c in respx.calls if c.request.method == "POST"
    )
    assert milestones.PENDING_TICKETS == []
    assert "pending_notify" not in trips.TRIPS.get("LGS-24021")
    events = [e["status"] for e in trips.TRIPS.get("LGS-24021")["events"]]
    assert events.count("milestone:at_pickup") == 1  # not applied twice


@respx.mock
def test_failed_send_is_retried_by_a_plain_repost():
    create, _ = _stub_chatwoot()
    create.side_effect = [httpx.ConnectError("down"), httpx.Response(200, json={"id": 30})]
    c = _ms_client()
    j = {"order_id": "LGS-24021", "milestone": "at_pickup"}
    assert c.post("/logisly/milestone", json=j, headers=H).status_code == 502
    assert c.post("/logisly/milestone", json=j, headers=H).json()["sent"] is True
    assert c.post("/logisly/milestone", json=j, headers=H).json()["sent"] is False
    assert create.call_count == 2


@respx.mock
def test_deferred_resend_without_force_stays_deferred_at_night():
    create, _ = _stub_chatwoot()
    c = _ms_client(NIGHT)
    j = {"order_id": "LGS-24021", "milestone": "at_pickup"}
    c.post("/logisly/milestone", json=j, headers=H)
    again = c.post("/logisly/milestone", json=j, headers=H).json()
    assert again["deferred"] is True and create.call_count == 0


def test_console_keeps_current_milestone_and_renders_422_details():
    page = _client(_settings()).get("/logisly/console").text
    assert 'data-cur="' in page  # selects remember the milestone they were rendered with
    assert "activeElement" in page  # the 4s refresh pauses while a select is open
    assert "errText" in page and ".msg" in page  # 422 detail arrays render their messages
