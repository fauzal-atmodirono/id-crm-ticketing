from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_incoming_returns_connect_stream_twiml() -> None:
    orch = MagicMock()
    s = get_settings()
    s.public_wss_base_url = "wss://tunnel.test"
    orch._settings = s
    res = _client(orch).post("/voice/phone/incoming")
    assert res.status_code == 200
    assert "application/xml" in res.headers["content-type"]
    assert 'url="wss://tunnel.test/voice/phone/stream"' in res.text
    assert "<Connect>" in res.text
    assert "<Say>" not in res.text  # no recording configured -> byte-identical


def test_incoming_says_announcement_before_connect_when_recording_enabled() -> None:
    """Package C Task 6 review fix: the PDPA notice is now spoken via TwiML
    <Say> BEFORE <Connect><Stream>, provably preceding the Media Stream
    "start" event that triggers CallControl.start_recording."""
    orch = MagicMock()
    s = get_settings()
    s.public_wss_base_url = "wss://tunnel.test"
    s.twilio_webhook_base_url = "https://example.ngrok.app"
    s.phone_recording_enabled = True
    s.phone_recording_announcement = "This call is recorded."
    orch._settings = s
    res = _client(orch).post("/voice/phone/incoming")
    assert res.status_code == 200
    say_idx = res.text.index("<Say>")
    connect_idx = res.text.index("<Connect>")
    assert say_idx < connect_idx
    assert "This call is recorded." in res.text


def test_incoming_omits_say_when_recording_enabled_without_announcement() -> None:
    """Matches _maybe_start_recording's own fail-closed refusal: no
    announcement configured means nothing is said (and, separately,
    recording itself never actually starts)."""
    orch = MagicMock()
    s = get_settings()
    s.public_wss_base_url = "wss://tunnel.test"
    s.twilio_webhook_base_url = "https://example.ngrok.app"
    s.phone_recording_enabled = True
    s.phone_recording_announcement = ""
    orch._settings = s
    res = _client(orch).post("/voice/phone/incoming")
    assert "<Say>" not in res.text


def test_incoming_omits_say_when_no_callback_base_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review minor fix: without twilio_webhook_base_url, _maybe_start_
    recording refuses to actually start recording at all (no callback URL
    to build) -- the caller must not be TOLD the call is recorded in that
    case, mirroring that exact gate."""
    orch = MagicMock()
    s = get_settings()
    s.public_wss_base_url = "wss://tunnel.test"
    s.phone_recording_enabled = True
    s.phone_recording_announcement = "This call is recorded."
    # Review fix (Minor, round 2): get_settings() is a cached singleton
    # shared across this file's tests (and, in a full suite run, every
    # other test that calls it). monkeypatch.setattr -- unlike the plain
    # `s.twilio_webhook_base_url = "..."` the other cases here use --
    # restores the ORIGINAL value automatically at teardown, so clearing
    # it here can't leak a blanked-out base url into whatever test runs
    # next (the order-dependent direction a plain assignment risks: the
    # other cases only ever SET a truthy value, which is comparatively
    # harmless since later tests overwrite it anyway).
    monkeypatch.setattr(s, "twilio_webhook_base_url", "")
    orch._settings = s
    res = _client(orch).post("/voice/phone/incoming")
    assert "<Say>" not in res.text
