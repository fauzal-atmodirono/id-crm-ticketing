"""/voice/phone/incoming + /voice/phone/stream with caller context
(2026-11-09 brief items 1-3, 8-10). Twilio and Chatwoot are faked; the Live
session is an injected factory."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.phone.caller_context import CallerContext, sign_caller
from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app

TOKEN = "test-twilio-token"
BASE = "https://example.test"
PHONE = "+60000000101"


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "public_wss_base_url": "wss://tunnel.test",
        "twilio_webhook_base_url": BASE,
        "twilio_auth_token": TOKEN,
        "phone_caller_context_enabled": True,
        "nps_sample_rate": 0.0,
    }
    base.update(kw)
    return Settings(**base)


def _twilio_sig(url: str, params: dict[str, str]) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


class _Service:
    def __init__(self, ctx: CallerContext | None = None) -> None:
        self.ctx = ctx
        self.lookups: list[tuple[str, str]] = []

    async def lookup(self, phone: str, source: str) -> CallerContext:
        self.lookups.append((phone, source))
        return self.ctx or CallerContext(phone=phone, source=source)

    async def dealer_slugs(self) -> list[str]:
        return ["petaling_jaya"]

    async def save(self, **kw: Any) -> None:
        return None


class _FakeLive:
    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None: ...
    async def send_text_hint(self, text: str) -> None: ...

    async def events(self) -> AsyncIterator[Any]:
        if False:
            yield None


def _client(
    settings: Settings, service: _Service | None, captured: dict[str, Any] | None = None
) -> TestClient:
    orch = MagicMock()
    orch._settings = settings
    orch._knowledge_port = MagicMock()
    orch._conversation_log_port = MagicMock()

    @asynccontextmanager
    async def factory(_s: Any, instruction: Any, tools: Any) -> AsyncIterator[_FakeLive]:
        if captured is not None:
            captured["instruction"] = str(instruction)
            captured["tools"] = [fd.name for t in tools for fd in (t.function_declarations or [])]
        yield _FakeLive()

    app = create_app(settings)
    app.include_router(
        build_chat_router(
            orch,
            live_session_factory=factory,
            caller_context_service=service,  # type: ignore[arg-type]
        )
    )
    return TestClient(app)


def _params(xml: str) -> dict[str, str]:
    return dict(re.findall(r'<Parameter name="([^"]+)" value="([^"]*)"/>', xml))


def _incoming(client: TestClient, form: dict[str, str], *, sign: bool = True) -> str:
    headers = {
        "X-Twilio-Signature": _twilio_sig(f"{BASE}/voice/phone/incoming", form) if sign else "bad"
    }
    res = client.post("/voice/phone/incoming", data=form, headers=headers)
    assert res.status_code == 200
    return res.text


# --- incoming ------------------------------------------------------------------


def test_incoming_passes_a_signed_caller_parameter() -> None:
    xml = _incoming(_client(_settings(), _Service()), {"CallSid": "CA1", "From": PHONE})
    p = _params(xml)
    assert p["caller"] == PHONE and p["caller_source"] == "caller_id"
    assert p["caller_sig"] == sign_caller(TOKEN, "CA1", "caller_id", PHONE)
    assert xml.index("<Parameter") > xml.index("<Stream")


def test_incoming_with_bad_twilio_signature_passes_no_caller() -> None:
    xml = _incoming(_client(_settings(), _Service()), {"CallSid": "CA1", "From": PHONE}, sign=False)
    assert "<Parameter" not in xml
    assert "<Connect>" in xml  # the call is still answered


def test_incoming_flag_off_is_byte_identical() -> None:
    s = _settings(phone_caller_context_enabled=False)
    xml = _incoming(_client(s, None), {"CallSid": "CA1", "From": PHONE})
    assert xml == (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
        '<Stream url="wss://tunnel.test/voice/phone/stream" /></Connect></Response>'
    )


def test_incoming_browser_call_as_only_when_enabled() -> None:
    form = {"CallSid": "CA1", "From": "client:proton-web-caller", "callAs": "0000000101"}
    allow = {"phone_web_call_as_numbers": "+60000000101,+60000000102"}
    off = _incoming(_client(_settings(**allow), _Service()), form)
    assert "<Parameter" not in off
    on = _incoming(_client(_settings(phone_web_call_as_enabled=True, **allow), _Service()), form)
    p = _params(on)
    assert p["caller"] == PHONE and p["caller_source"] == "web_call_as"


def test_incoming_call_as_is_limited_to_the_allowlist() -> None:
    """Otherwise the console is an oracle: type any number, hear its owner's
    first name and car. Empty list = deny all."""
    form = {"CallSid": "CA1", "From": "client:proton-web-caller", "callAs": "0123456789"}
    listed = _settings(phone_web_call_as_enabled=True, phone_web_call_as_numbers="+60000000101")
    assert "<Parameter" not in _incoming(_client(listed, _Service()), form)
    empty = _settings(phone_web_call_as_enabled=True)
    form_ok = {**form, "callAs": "+60000000101"}
    assert "<Parameter" not in _incoming(_client(empty, _Service()), form_ok)


def test_incoming_pstn_call_ignores_call_as() -> None:
    form = {"CallSid": "CA1", "From": "+60000000999", "callAs": "0000000101"}
    p = _params(_incoming(_client(_settings(phone_web_call_as_enabled=True), _Service()), form))
    assert p["caller"] == "+60000000999" and p["caller_source"] == "caller_id"


def test_incoming_withheld_number_passes_no_caller() -> None:
    xml = _incoming(_client(_settings(), _Service()), {"CallSid": "CA1", "From": "anonymous"})
    assert "<Parameter" not in xml


# --- stream ----------------------------------------------------------------------


def _start(params: dict[str, str] | None) -> dict[str, Any]:
    start: dict[str, Any] = {"streamSid": "S1", "callSid": "CA1"}
    if params is not None:
        start["customParameters"] = params
    return {"event": "start", "start": start}


def _stream(
    settings: Settings, service: _Service | None, params: dict[str, str] | None
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    client = _client(settings, service, captured)
    with client.websocket_connect("/voice/phone/stream") as ws:
        ws.send_json(_start(params))
    return captured


def test_stream_looks_up_a_verified_caller_before_the_greeting() -> None:
    ctx = CallerContext.from_candidates(
        PHONE,
        "caller_id",
        [
            {
                "contact_id": 11,
                "name": "Ahmad bin Ali",
                "vehicles": [{"plate": "DMO1101", "model": "Proton X50"}],
            }
        ],
    )
    svc = _Service(ctx)
    params = {
        "caller": PHONE,
        "caller_source": "caller_id",
        "caller_sig": sign_caller(TOKEN, "CA1", "caller_id", PHONE),
    }
    cap = _stream(_settings(), svc, params)
    assert svc.lookups == [(PHONE, "caller_id")]
    assert "Am I speaking with Ahmad?" in cap["instruction"]
    assert PHONE not in cap["instruction"] and "DMO1101" not in cap["instruction"]
    assert "save_caller_details" in cap["tools"] and "lookup_caller" not in cap["tools"]


def test_stream_ignores_a_forged_caller_and_asks_for_the_number() -> None:
    svc = _Service()
    params = {"caller": PHONE, "caller_source": "caller_id", "caller_sig": "forged"}
    cap = _stream(_settings(), svc, params)
    assert svc.lookups == []
    assert "lookup_caller" in cap["tools"]
    assert "mobile number" in cap["instruction"]


def test_stream_flag_off_offers_no_caller_tools_and_no_flow_paragraph() -> None:
    cap = _stream(_settings(phone_caller_context_enabled=False), None, None)
    assert "save_caller_details" not in cap["tools"] and "lookup_caller" not in cap["tools"]
    assert "CALL FLOW" not in cap["instruction"]
