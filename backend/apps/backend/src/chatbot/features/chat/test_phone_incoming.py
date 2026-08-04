from unittest.mock import MagicMock

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
    s.phone_recording_enabled = True
    s.phone_recording_announcement = ""
    orch._settings = s
    res = _client(orch).post("/voice/phone/incoming")
    assert "<Say>" not in res.text
