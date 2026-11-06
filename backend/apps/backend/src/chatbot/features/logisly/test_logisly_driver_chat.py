from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.logisly import trips
from chatbot.features.logisly.driver_chat import driver_view
from chatbot.features.logisly.router import build_logisly_router

CW = "http://cw:3000/api/v1/accounts/1"
H = {"X-API-Key": "k"}


def _settings(**over):
    base = {
        "proton_backend_key": "k",
        "logisly_demo_driver_phone": "+6281100001111",
        "logisly_driver_inbox_id": 4,
        "logisly_app_inbox_id": 5,
        "logisly_no_reply_call_seconds": 0,
        "chatwoot_api_url": "http://cw:3000",
        "chatwoot_account_id": 1,
        "chatwoot_api_token": "t",
        "twilio_webhook_base_url": "",
        "twilio_api_key_sid": "",
        "twilio_api_key_secret": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(trips, "TRIPS", trips.TripStore.seeded())
    monkeypatch.setattr("chatbot.features.logisly.router.TRIPS", trips.TRIPS)
    trips.ACTIVE_SESSIONS.clear()


def _client(**over):
    app = FastAPI()
    app.include_router(build_logisly_router(_settings(**over)))
    return TestClient(app)


def _mock_contact(convs):
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 14, "identifier": "logisly-demo-driver"}]}
        )
    )
    respx.get(f"{CW}/contacts/14/conversations").mock(
        return_value=httpx.Response(200, json={"payload": convs})
    )


def test_driver_view_hides_notes_and_activity():
    assert driver_view({"private": True, "message_type": 1}) is None
    assert driver_view({"private": False, "message_type": 2}) is None
    v = driver_view(
        {
            "id": 1,
            "message_type": 0,
            "content": "hi",
            "attachments": [{"file_type": "audio", "data_url": "http://x/a.wav", "thumb_url": ""}],
        }
    )
    assert v["from"] == "driver" and v["attachments"][0]["type"] == "audio"


@respx.mock
def test_messages_come_from_the_latest_open_app_conversation():
    _mock_contact(
        [
            {"id": 30, "inbox_id": 5, "status": "pending"},
            {"id": 31, "inbox_id": 4, "status": "open"},
            {"id": 29, "inbox_id": 5, "status": "resolved"},
        ]
    )
    respx.get(f"{CW}/conversations/30/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    {"id": 1, "message_type": 1, "content": "Halo Pak", "private": False},
                    {"id": 2, "message_type": 1, "content": "note", "private": True},
                ]
            },
        )
    )
    r = _client().get("/logisly/driver/messages", headers=H)
    assert r.json()["conversation_id"] == 30
    assert [m["text"] for m in r.json()["messages"]] == ["Halo Pak"]


@respx.mock
def test_driver_writing_first_opens_a_conversation_and_posts_incoming_with_file():
    _mock_contact([])
    created = respx.post(f"{CW}/conversations").mock(
        return_value=httpx.Response(200, json={"id": 40})
    )
    posted = respx.post(f"{CW}/conversations/40/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    r = _client().post(
        "/logisly/driver/messages",
        headers=H,
        data={"text": "ban pecah"},
        files={"file": ("pesan-suara.wav", b"RIFF....", "audio/wav")},
    )
    assert r.status_code == 200 and r.json()["conversation_id"] == 40
    assert b'"source_id":"logisly-demo-driver"' in created.calls.last.request.content
    body = posted.calls.last.request.content
    assert b'name="message_type"' in body and b"incoming" in body and b"pesan-suara.wav" in body


def test_empty_message_is_rejected():
    assert (
        _client().post("/logisly/driver/messages", headers=H, data={"text": " "}).status_code == 422
    )


@respx.mock
def test_alert_defaults_to_the_app_when_configured():
    _mock_contact([])
    create = respx.post(f"{CW}/conversations").mock(
        return_value=httpx.Response(200, json={"id": 41})
    )
    r = _client().post(
        "/logisly/anomaly", headers=H, json={"order_id": "LGS-24017", "kind": "gps_stop"}
    )
    assert r.json()["channel"] == "app"
    assert b'"inbox_id":5' in create.calls.last.request.content
    assert "crm-41" in trips.ACTIVE_SESSIONS
