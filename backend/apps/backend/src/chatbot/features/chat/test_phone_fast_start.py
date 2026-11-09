"""Phone call start (2026-11-09): prefetch at /incoming, the PDPA notice over
the stream, the assistant greeting first, and the caller's language.

Live evidence: ~13-14 s from POST /voice/phone/incoming to the Live session
being connected -- the <Say> notice ran before Twilio opened the stream, then
the stream awaited the caller lookup + dealer list before opening Gemini Live,
and the assistant waited for the caller to speak first.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.phone.caller_context import CallerContext, sign_caller
from chatbot.features.chat.router import build_chat_router
from chatbot.features.demo import personas
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app

TOKEN = "test-twilio-token"
BASE = "https://example.test"
PHONE = "+60000000101"
NOTICE = "This call is recorded for quality purposes."


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "public_wss_base_url": "wss://tunnel.test",
        "twilio_webhook_base_url": BASE,
        "twilio_auth_token": TOKEN,
        "phone_caller_context_enabled": True,
        "phone_web_call_as_enabled": True,
        "phone_web_call_as_numbers": PHONE,
        "phone_transcript_live_enabled": True,
        "phone_recording_enabled": True,
        "phone_recording_announcement": NOTICE,
        "phone_fast_start_enabled": True,
        "nps_sample_rate": 0.0,
    }
    base.update(kw)
    return Settings(**base)


def _sig(url: str, params: dict[str, str]) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


class _Service:
    def __init__(self) -> None:
        self.lookups: list[tuple[str, str]] = []
        self.dealer_calls = 0

    async def lookup(self, phone: str, source: str) -> CallerContext:
        self.lookups.append((phone, source))
        return CallerContext.from_candidates(
            phone, source, [{"contact_id": 11, "name": "[DEMO] Ahmad"}]
        )

    async def dealer_slugs(self) -> list[str]:
        self.dealer_calls += 1
        return ["petaling_jaya"]

    async def save(self, **kw: Any) -> None:
        return None


class _Live:
    def __init__(self) -> None:
        self.greetings: list[str] = []
        self.text_hints: list[str] = []

    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(self, *a: Any) -> None: ...
    async def send_text_hint(self, text: str) -> None:
        self.text_hints.append(text)

    async def send_greeting(self, text: str) -> None:
        self.greetings.append(text)

    async def events(self) -> AsyncIterator[Any]:
        if False:
            yield None


class _Tts:
    def __init__(self, audio: bytes | None) -> None:
        self.audio = audio
        self.calls: list[tuple[str, str]] = []

    async def synthesize_mulaw8k(self, text: str, language_code: str = "en-US") -> bytes:
        self.calls.append((text, language_code))
        if self.audio is None:
            raise RuntimeError("tts down")
        return self.audio


def _app(settings: Settings, service: _Service, tts: _Tts, cap: dict[str, Any]) -> Any:
    orch = MagicMock()
    orch._settings = settings
    orch._knowledge_port = MagicMock()
    log = AsyncMock()
    log.ensure_conversation_ticket.return_value = "T-1"
    orch._conversation_log_port = log
    orch._tts_port = tts

    @asynccontextmanager
    async def factory(_s: Any, instruction: Any, _tools: Any) -> AsyncIterator[_Live]:
        cap["instruction"] = str(instruction)
        live = _Live()
        cap["live"] = live
        yield live

    app = create_app(settings)
    app.include_router(
        build_chat_router(orch, live_session_factory=factory, caller_context_service=service)  # type: ignore[arg-type]
    )
    return app


def _incoming(client: TestClient, form: dict[str, str]) -> str:
    res = client.post(
        "/voice/phone/incoming",
        data=form,
        headers={"X-Twilio-Signature": _sig(f"{BASE}/voice/phone/incoming", form)},
    )
    assert res.status_code == 200
    return res.text


def _params(xml: str) -> dict[str, str]:
    return dict(re.findall(r'<Parameter name="([^"]+)" value="([^"]*)"/>', xml))


def _start(params: dict[str, str]) -> dict[str, Any]:
    return {
        "event": "start",
        "start": {"streamSid": "S1", "callSid": "CA1", "customParameters": params},
    }


FORM = {"CallSid": "CA1", "From": PHONE}


def test_cold_notice_cache_keeps_the_say_and_warms_the_audio() -> None:
    tts = _Tts(b"\xff" * 16000)
    with TestClient(_app(_settings(), _Service(), tts, {})) as client:
        first = _incoming(client, FORM)
        assert f"<Say>{NOTICE}</Say>" in first  # PDPA: never silent
        assert "notice" not in _params(first)
        second = _incoming(client, {**FORM, "CallSid": "CA2"})
    assert "<Say>" not in second
    assert _params(second)["notice"] == "stream"
    assert tts.calls == [(NOTICE, "en-US")]


def test_flag_off_is_the_old_twiml() -> None:
    tts = _Tts(b"\xff" * 16000)
    with TestClient(_app(_settings(phone_fast_start_enabled=False), _Service(), tts, {})) as c:
        _incoming(c, FORM)
        xml = _incoming(c, FORM)
    assert f"<Say>{NOTICE}</Say>" in xml and "notice" not in _params(xml)
    assert tts.calls == []


def test_tts_failure_keeps_the_say() -> None:
    with TestClient(_app(_settings(), _Service(), _Tts(None), {})) as client:
        _incoming(client, FORM)
        xml = _incoming(client, FORM)
    assert f"<Say>{NOTICE}</Say>" in xml


def test_stream_plays_the_notice_first_uses_the_prefetch_and_greets_once() -> None:
    svc = _Service()
    cap: dict[str, Any] = {}
    tts = _Tts(b"\xff" * 8000)  # 1 s
    with TestClient(_app(_settings(), svc, tts, cap)) as client:
        _incoming(client, {**FORM, "CallSid": "CA0"})  # warms the notice
        xml = _incoming(client, FORM)
        params = _params(xml)
        with client.websocket_connect("/voice/phone/stream") as ws:
            ws.send_json(_start(params))
            frames = [ws.receive_json() for _ in range(4)]
    # 1 s of notice = 3 frames of <= 0.4 s, then the mark Twilio echoes
    assert [f["event"] for f in frames] == ["media", "media", "media", "mark"]
    assert frames[3]["mark"] == {"name": "pdpa_notice"}
    assert sum(len(base64.b64decode(f["media"]["payload"])) for f in frames[:3]) == 8000
    # one lookup per call, both started at /incoming (CA0, CA1): the stream
    # used CA1's prefetch instead of looking up again
    assert svc.lookups == [(PHONE, "caller_id"), (PHONE, "caller_id")]
    assert cap["live"].greetings and len(cap["live"].greetings) == 1
    assert cap["live"].text_hints == []


def test_stream_without_prefetch_still_looks_up() -> None:
    svc = _Service()
    cap: dict[str, Any] = {}
    params = {
        "caller": PHONE,
        "caller_source": "caller_id",
        "caller_sig": sign_caller(TOKEN, "CA1", "caller_id", PHONE),
    }
    with (
        TestClient(_app(_settings(), svc, _Tts(None), cap)) as client,
        client.websocket_connect("/voice/phone/stream") as ws,
    ):
        ws.send_json(_start(params))
    assert svc.lookups == [(PHONE, "caller_id")]
    assert "Am I speaking with Ahmad?" in cap["instruction"]


def test_web_call_as_persona_starts_in_its_language() -> None:
    cap: dict[str, Any] = {}
    form = {"CallSid": "CA1", "From": "client:proton-web-caller", "callAs": PHONE}
    persona = [{"number": PHONE, "language": "ms"}]
    orig = personas.load_personas
    personas.load_personas = lambda _path=None: persona  # type: ignore[assignment]
    try:
        with TestClient(_app(_settings(), _Service(), _Tts(None), cap)) as client:
            params = _params(_incoming(client, form))
            with client.websocket_connect("/voice/phone/stream") as ws:
                ws.send_json(_start(params))
    finally:
        personas.load_personas = orig  # type: ignore[assignment]
    assert "start the call in Bahasa Melayu" in cap["instruction"]


def test_instruction_language_and_rating_rules() -> None:
    cap: dict[str, Any] = {}
    with (
        TestClient(_app(_settings(), _Service(), _Tts(None), cap)) as client,
        client.websocket_connect("/voice/phone/stream") as ws,
    ):
        ws.send_json(_start({}))
    text = cap["instruction"]
    assert "switch immediately if they switch" not in text
    assert "Switch ONLY when the caller themselves clearly says a full sentence" in text
    assert "greet in English" in text
    assert "in the caller's language, never switching to English for it" in text
