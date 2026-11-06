import base64
import contextlib
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    OutputTranscript,
    ToolCall,
)
from chatbot.features.logisly import call, trips
from chatbot.features.logisly.router import build_logisly_router


class _FakeLive:
    def __init__(self, events=()):
        self.audio, self.hints, self.tool_responses = [], [], []
        self._events = list(events)

    async def send_audio(self, pcm):
        self.audio.append(pcm)

    async def send_text_hint(self, text):
        self.hints.append(text)

    async def send_tool_response(self, call_id, name, response):
        self.tool_responses.append((name, response))

    async def events(self):
        for e in self._events:
            yield e


class _FakeChannel:
    def __init__(self):
        self.calls = []

    async def add_private_note(self, cid, text):
        self.calls.append(("note", cid, text))

    async def add_labels(self, cid, labels):
        self.calls.append(("labels", cid, labels))

    async def set_priority(self, cid, p):
        self.calls.append(("priority", cid, p))

    async def set_status(self, cid, s):
        self.calls.append(("status", cid, s))


def _settings(**over):
    base = {
        "proton_backend_key": "k",
        "twilio_account_sid": "AC1",
        "twilio_auth_token": "tok",
        "twilio_api_key_sid": "SK1",
        "twilio_api_key_secret": "sec",
        "twilio_webhook_base_url": "https://demo.trycloudflare.com",
        "logisly_demo_driver_phone": "",
        "logisly_driver_inbox_id": 0,
        "logisly_app_inbox_id": 0,
        "logisly_no_reply_call_seconds": 0,
        "chatwoot_api_url": "http://cw:3000",
        "chatwoot_account_id": 1,
        "chatwoot_api_token": "t",
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(trips, "TRIPS", trips.TripStore.seeded())
    monkeypatch.setattr("chatbot.features.logisly.router.TRIPS", trips.TRIPS)
    trips.ACTIVE_SESSIONS.clear()
    trips.register_session("crm-22", "LGS-24017", "gps_stop", "Halo Pak, truk berhenti?")


def test_twiml_streams_to_public_wss_with_session_parameter():
    xml = call.stream_twiml(call.public_wss_url(_settings()), "crm-22")
    assert 'url="wss://demo.trycloudflare.com/logisly/call/stream"' in xml
    assert '<Parameter name="session" value="crm-22" />' in xml


def test_instruction_carries_the_trip_and_alert():
    text = call.call_instruction("crm-22")
    assert "PHONE CALL" in text and "LGS-24017" in text and "Truk berhenti lama" in text


@respx.mock
async def test_start_call_rings_the_driver_client():
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC1/Calls.json").mock(
        return_value=httpx.Response(201, json={"sid": "CA9"})
    )
    assert await call.start_call(_settings(), "crm-22") == "CA9"
    body = route.calls.last.request.content.decode()
    assert "client%3Alogisly-driver" in body and "crm-22" in body


async def test_bridge_speaks_first_relays_audio_and_records_trip_update():
    live = _FakeLive(
        [
            AudioOut(b"\x00\x00" * 240),
            OutputTranscript("Halo Pak Budi"),
            InputTranscript("ban pecah"),
            ToolCall(
                id="1",
                name="update_trip_status",
                args={"status": "delayed", "note": "Ban pecah, telat 1 jam"},
            ),
        ]
    )
    sent = []

    async def send(msg):
        sent.append(msg)

    ch = _FakeChannel()
    bridge = call.LogislyCallBridge(live, send, _settings(), "crm-22", ch)
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1"}})
    await bridge.handle_twilio(
        {"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}
    )
    await bridge.pump()
    await bridge.finalize()

    assert live.hints == [call.START_HINT] and live.audio
    assert sent[0]["event"] == "media" and sent[0]["streamSid"] == "MZ1"
    assert live.tool_responses == [("update_trip_status", {"status": "recorded"})]
    assert trips.TRIPS.get("LGS-24017")["status"] == "delayed"
    note = ch.calls[0][2]
    assert "Agen: Halo Pak Budi" in note and "Driver: ban pecah" in note and "LGS-24017" in note
    assert len(ch.calls) == 1  # no escalation


async def test_escalation_on_call_files_ticket_and_reopens():
    live = _FakeLive(
        [
            ToolCall(
                id="1",
                name="escalate_to_operations",
                args={"reason": "cargo_issue", "summary": "Muatan basah kena hujan."},
            )
        ]
    )
    ch = _FakeChannel()

    async def send(_msg):
        return None

    bridge = call.LogislyCallBridge(live, send, _settings(), "crm-22", ch)
    await bridge.pump()
    await bridge.finalize()
    kinds = [c[0] for c in ch.calls]
    assert kinds == ["note", "note", "labels", "priority", "status"]
    assert ch.calls[-1] == ("status", 22, "open")
    assert "Muatan basah" in ch.calls[1][2]


def test_driver_token_needs_key_and_allows_incoming():
    c = TestClient(_app())
    assert c.get("/logisly/driver/token").status_code == 401
    r = c.get("/logisly/driver/token", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.json()["identity"] == "logisly-driver"
    assert "Logisly Driver" in c.get("/logisly/driver").text


def test_call_requires_an_alert_first():
    trips.ACTIVE_SESSIONS.clear()
    r = TestClient(_app()).post(
        "/logisly/call", json={"order_id": "LGS-24017"}, headers={"X-API-Key": "k"}
    )
    assert r.status_code == 409


def test_stream_refuses_an_unknown_session():
    opened = []

    @contextlib.asynccontextmanager
    async def factory(*_a):
        opened.append(True)
        yield _FakeLive()

    c = TestClient(_app(factory))
    with c.websocket_connect("/logisly/call/stream") as ws:
        ws.send_json(
            {
                "event": "start",
                "start": {"streamSid": "MZ", "customParameters": {"session": "crm-404"}},
            }
        )
    assert opened == []


def _app(factory=None):
    app = FastAPI()
    kwargs = {"live_session_factory": factory} if factory else {}
    app.include_router(build_logisly_router(_settings(), channel=_FakeChannel(), **kwargs))
    return app
