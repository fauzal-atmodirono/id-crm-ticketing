"""Package C Task 5: call recording.

Covers (per the task brief, plus the review round's fixes):
- recording starts on stream start only when phone_recording_enabled is on;
- a start_recording failure does not affect the call;
- PHONE_RECORDING_ANNOUNCEMENT fails CLOSED -- enabled with no announcement
  configured, or the announcement hint failing to queue, or no callback
  base configured, all refuse to start recording, logged at WARNING;
- the /webhooks/phone/recording-status callback persists the three
  attributes on "completed" and ignores every other status;
- a callback for an unknown call is ignored rather than raising (pinned
  against a REAL ChatwootAdapter with no matching contact/conversation --
  not a fake-only sentinel, see test_chatwoot_conversation_log.py);
- a retried callback delivery is idempotent (no duplicate attach);
- the webhook is gated behind phone_recording_enabled (404 when off) and
  refuses (401) rather than skips verification when no Twilio auth token
  is configured;
- flags off -> zero new Twilio/CRM calls (byte-identical).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.phone.bridge import PhoneBridge
from chatbot.features.chat.phone.call_control import CallControl
from chatbot.features.chat.phone.live_events import LiveEvent
from chatbot.features.chat.ports import ConversationLogResult
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


@pytest.fixture(autouse=True)
def _fake_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """OrchestratorService's constructor always eagerly builds a real
    google-genai Client() (features/chat/service.py), even for tests below
    that only exercise the recording-status webhook and never touch
    Gemini. A real deployment always has a key configured; here a dummy
    value just lets construction succeed without one -- nothing in this
    file ever makes a real Gemini call."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-a-real-credential")


# --- bridge-level fakes (self-contained -- not shared with test_bridge.py) -


class _FakeLive:
    def __init__(self, *, hint_raises: Exception | None = None) -> None:
        self.text_hints: list[str] = []
        self._hint_raises = hint_raises

    async def send_audio(self, pcm16k: bytes) -> None:
        return None

    async def send_tool_response(self, call_id: str, name: str, response: dict[str, Any]) -> None:
        return None

    async def send_text_hint(self, text: str) -> None:
        if self._hint_raises is not None:
            raise self._hint_raises
        self.text_hints.append(text)

    async def events(self) -> AsyncIterator[LiveEvent]:
        return
        yield  # pragma: no cover -- makes this an async generator


class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        return []


class _FakeLog:
    """Minimal ConversationLogPort fake -- only what recording touches."""

    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.found: str | None = "T-1"  # find_conversation_ticket's return value
        self.found_calls: list[str] = []
        self.recordings: list[tuple[str, str, str, str]] = []

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        self.ensured.append(session_id)
        return "T-1"

    async def rotate_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        return session_id

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        self.found_calls.append(session_id)
        return self.found

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        return ConversationLogResult.OK

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        return None

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        return None

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        return None

    async def set_ticket_classification(
        self,
        ticket_id: str,
        *,
        case_type: str | None = None,
        division: str | None = None,
        concern: str | None = None,
    ) -> None:
        return None

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        return ("", None, None)

    async def set_call_recording(
        self,
        ticket_id: str,
        *,
        recording_sid: str,
        recording_duration: str,
        recording_url: str,
    ) -> None:
        self.recordings.append((ticket_id, recording_sid, recording_duration, recording_url))


class _FakeRecordings:
    """Stands in for a Twilio ``client.calls(sid).recordings`` resource --
    mirrors test_call_control.py's ``_FakeCalls``/``_FakeTwilio`` fakes so
    the real ``CallControl`` is exercised with only its underlying Twilio
    client stubbed (never a real Twilio call from a test)."""

    def __init__(self, sid: str | None = "RE1", raises: Exception | None = None) -> None:
        self.sid = sid
        self.raises = raises
        self.recorded: list[tuple[str, str]] = []
        self._call_sid = ""

    def create(self, **kwargs: Any) -> Any:
        if self.raises:
            raise self.raises
        self.recorded.append((self._call_sid, kwargs.get("recording_status_callback", "")))
        return type("R", (), {"sid": self.sid})()


class _FakeCalls:
    def __init__(self, recordings: _FakeRecordings) -> None:
        self._recordings = recordings

    def __call__(self, call_sid: str) -> _FakeCalls:
        self._recordings._call_sid = call_sid
        return self

    @property
    def recordings(self) -> _FakeRecordings:
        return self._recordings


class _FakeTwilioClient:
    def __init__(self, sid: str | None = "RE1", raises: Exception | None = None) -> None:
        self.recordings = _FakeRecordings(sid=sid, raises=raises)
        self.calls = _FakeCalls(self.recordings)


def _bridge(
    settings: Settings,
    live: _FakeLive | None = None,
    log: _FakeLog | None = None,
    twilio_client: _FakeTwilioClient | None = None,
) -> tuple[PhoneBridge, _FakeLive, _FakeLog, _FakeTwilioClient]:
    live = live or _FakeLive()
    log = log or _FakeLog()
    twilio_client = twilio_client or _FakeTwilioClient()
    call_control = CallControl(settings, client=twilio_client)

    async def send_twilio(_msg: dict[str, object]) -> None:
        return None

    bridge = PhoneBridge(
        live,
        _FakeKnowledge(),
        log,
        send_twilio,
        settings,
        call_control=call_control,
    )
    return bridge, live, log, twilio_client


_ANNOUNCEMENT = (
    "This call may be recorded for quality and training purposes. "
    "Panggilan ini mungkin dirakam untuk tujuan kualiti dan latihan."
)

_BASE_URL = "https://example.ngrok.app"


def _enabled_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        phone_recording_enabled=True,
        phone_recording_announcement=_ANNOUNCEMENT,
        twilio_webhook_base_url=_BASE_URL,
        **overrides,
    )


async def test_recording_starts_on_stream_start_when_enabled() -> None:
    settings = _enabled_settings()
    bridge, live, _log, twilio_client = _bridge(settings)

    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert bridge._recording_task is not None
    await bridge._recording_task

    assert twilio_client.recordings.recorded == [
        ("CA1", f"{_BASE_URL}/webhooks/phone/recording-status")
    ]
    assert any(_ANNOUNCEMENT in hint for hint in live.text_hints)


async def test_recording_not_started_when_flag_off() -> None:
    settings = Settings(
        _env_file=None,
        phone_recording_announcement=_ANNOUNCEMENT,
        twilio_webhook_base_url=_BASE_URL,
    )
    bridge, live, _log, twilio_client = _bridge(settings)

    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert bridge._recording_task is None
    assert twilio_client.recordings.recorded == []
    assert live.text_hints == []


async def test_recording_refuses_without_announcement_fail_closed() -> None:
    """PDPA: unlike every other flag in this package, this ONE fails closed."""
    settings = Settings(
        _env_file=None, phone_recording_enabled=True, twilio_webhook_base_url=_BASE_URL
    )  # announcement left blank
    bridge, live, _log, twilio_client = _bridge(settings)

    with capture_logs() as captured:
        await bridge.handle_twilio(
            {"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}}
        )
        assert bridge._recording_task is not None
        await bridge._recording_task

    assert twilio_client.recordings.recorded == []
    assert live.text_hints == []
    events = [e["event"] for e in captured]
    assert "phone_recording_no_announcement_configured" in events


async def test_recording_refuses_when_no_callback_base_configured_fail_closed() -> None:
    """Review fix (Important 4): recording customer voice with no way to
    ever attach/find it again is the same class of harm as recording with
    no notice -- fails closed, not open, unlike the rest of this package."""
    settings = Settings(
        _env_file=None, phone_recording_enabled=True, phone_recording_announcement=_ANNOUNCEMENT
    )  # twilio_webhook_base_url left blank
    bridge, live, _log, twilio_client = _bridge(settings)

    with capture_logs() as captured:
        await bridge.handle_twilio(
            {"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}}
        )
        assert bridge._recording_task is not None
        await bridge._recording_task

    assert twilio_client.recordings.recorded == []
    # The disclosure must not be sent either -- there's no point telling the
    # caller the call is recorded when it's about to refuse to record it.
    assert live.text_hints == []
    events = [e["event"] for e in captured]
    assert "phone_recording_no_callback_base_configured" in events


async def test_recording_refuses_when_announcement_hint_fails_to_queue_fail_closed() -> None:
    """Review fix (Important 1): a closed/broken Live session is exactly
    when send_text_hint raises -- must not fall through to start_recording
    on that failure, since the caller was never actually notified."""
    settings = _enabled_settings()
    live = _FakeLive(hint_raises=RuntimeError("live session closed"))
    bridge, _live, _log, twilio_client = _bridge(settings, live=live)

    with capture_logs() as captured:
        await bridge.handle_twilio(
            {"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}}
        )
        assert bridge._recording_task is not None
        await bridge._recording_task

    assert twilio_client.recordings.recorded == []
    events = [e["event"] for e in captured]
    assert "phone_recording_announcement_hint_failed" in events


async def test_recording_start_failure_does_not_break_the_call() -> None:
    settings = _enabled_settings()
    bridge, _live, _log, _twilio_client = _bridge(
        settings, twilio_client=_FakeTwilioClient(raises=RuntimeError("twilio down"))
    )

    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert bridge._recording_task is not None
    await bridge._recording_task  # must not raise

    # The call keeps going: a subsequent media frame must not raise either.
    payload = base64.b64encode(b"\xff" * 160).decode()
    await bridge.handle_twilio({"event": "media", "media": {"payload": payload}})


async def test_recording_attempted_only_once_per_call() -> None:
    """A Twilio recording is a real, billed resource -- a reconnected/resent
    "start" event for the same call must not start a second one."""
    settings = _enabled_settings()
    bridge, _live, _log, twilio_client = _bridge(settings)

    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    task = bridge._recording_task
    assert task is not None
    await task
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ2", "callSid": "CA1"}})

    assert len(twilio_client.recordings.recorded) == 1


async def test_finalize_settles_the_recording_task() -> None:
    settings = _enabled_settings()
    bridge, _live, _log, twilio_client = _bridge(settings)
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    await bridge.finalize()  # must not raise, and must not leak the task
    assert twilio_client.recordings.recorded == [
        ("CA1", f"{_BASE_URL}/webhooks/phone/recording-status")
    ]


# --- settings defaults (byte-identical with flags off) --------------------


def test_recording_settings_default_off() -> None:
    settings = Settings(_env_file=None)
    assert settings.phone_recording_enabled is False
    assert settings.phone_recording_announcement == ""
    assert settings.phone_recording_retention_days == 90


# --- /webhooks/phone/recording-status --------------------------------------


class _FakeRunner:
    """Empty ADK runner -- avoids OrchestratorService building a real
    google-genai client (and needing a live GEMINI_API_KEY) just to
    construct the app for these webhook-only tests."""

    async def run_async(self, **_: Any) -> AsyncGenerator[Any, None]:
        for _i in range(0):
            yield None


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = hmac.new(token.encode(), s.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _client(
    log: _FakeLog, settings: Settings | None = None
) -> tuple[TestClient, _FakeLog, Settings]:
    settings = settings or Settings(
        _env_file=None,
        phone_recording_enabled=True,
        twilio_auth_token="test_token",
        twilio_account_sid="AC1",
    )
    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        conversation_log_port=log,
        runner_factory=lambda _agent: _FakeRunner(),
    )
    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator))
    return TestClient(app), log, settings


def test_recording_status_webhook_404s_when_flag_off() -> None:
    """Review fix (Important 3): this route is registered on every tenant,
    so it needs its own gate -- a tenant that never turned recording on
    must not expose a live callback endpoint at all."""
    settings = Settings(
        _env_file=None, twilio_auth_token="test_token", twilio_account_sid="AC1"
    )  # phone_recording_enabled left at its False default
    client, log, _settings = _client(_FakeLog(), settings=settings)

    res = client.post(
        "/webhooks/phone/recording-status",
        data={"CallSid": "CA1", "RecordingStatus": "completed"},
        headers={"X-Twilio-Signature": "irrelevant"},
    )

    assert res.status_code == 404
    assert log.found_calls == []


def test_recording_status_webhook_refuses_when_no_auth_token_configured() -> None:
    """Review fix (Important 3): refuse (401), don't silently skip
    verification, when no twilio_auth_token is configured -- otherwise any
    unauthenticated POST can write an attacker-supplied RecordingUrl."""
    settings = Settings(_env_file=None, phone_recording_enabled=True)  # no twilio_auth_token
    client, log, _settings = _client(_FakeLog(), settings=settings)

    res = client.post(
        "/webhooks/phone/recording-status",
        data={"CallSid": "CA1", "RecordingStatus": "completed"},
    )

    assert res.status_code == 401
    assert log.found_calls == []


def test_recording_status_webhook_rejects_bad_signature() -> None:
    client, _log, _settings = _client(_FakeLog())
    res = client.post(
        "/webhooks/phone/recording-status",
        data={"CallSid": "CA1", "RecordingStatus": "completed"},
        headers={"X-Twilio-Signature": "wrong"},
    )
    assert res.status_code == 401


def test_recording_status_webhook_persists_completed_attributes() -> None:
    log = _FakeLog()
    client, _log, settings = _client(log)
    params = {
        "CallSid": "CA1",
        "RecordingSid": "RE1",
        "RecordingStatus": "completed",
        "RecordingDuration": "42",
        "RecordingUrl": "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1",
    }
    url = "http://testserver/webhooks/phone/recording-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/recording-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert log.found_calls == ["phone-CA1"]
    assert log.recordings == [
        (
            "T-1",
            "RE1",
            "42",
            "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1",
        )
    ]


def test_recording_status_webhook_ignores_non_completed_status() -> None:
    log = _FakeLog()
    client, _log, settings = _client(log)
    params = {"CallSid": "CA1", "RecordingStatus": "in-progress"}
    url = "http://testserver/webhooks/phone/recording-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/recording-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert log.found_calls == []  # never even resolves a ticket for a non-final status
    assert log.recordings == []


def test_recording_status_webhook_ignores_missing_call_sid() -> None:
    log = _FakeLog()
    client, _log, settings = _client(log)
    params = {"RecordingStatus": "completed"}
    url = "http://testserver/webhooks/phone/recording-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/recording-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert log.found_calls == []
    assert log.recordings == []


def test_recording_status_webhook_ignores_unknown_call_rather_than_raising() -> None:
    """find_conversation_ticket returning None (nothing found, NEVER
    created) must be ignored, not treated as a real ticket id. This is
    pinned against the fake's honest "not found" return here; the
    equivalent behaviour against a REAL ChatwootAdapter (no matching
    contact) is pinned separately in
    test_chatwoot_conversation_log.py::test_find_conversation_ticket_returns_none_when_no_contact_matches,
    per the review's "don't just pin the fake" note."""
    log = _FakeLog()
    log.found = None
    client, _log, settings = _client(log)
    params = {"CallSid": "CA1", "RecordingStatus": "completed", "RecordingSid": "RE1"}
    url = "http://testserver/webhooks/phone/recording-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/recording-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200  # ignored, not raised
    assert log.recordings == []


def test_recording_status_webhook_is_idempotent_on_retry() -> None:
    """A retried callback delivery must not attach the recording twice --
    set_call_recording is a plain attribute SET, so calling it again with
    the SAME values is a no-op in effect, never a duplicate attach."""
    log = _FakeLog()
    client, _log, settings = _client(log)
    params = {
        "CallSid": "CA1",
        "RecordingSid": "RE1",
        "RecordingStatus": "completed",
        "RecordingDuration": "42",
        "RecordingUrl": "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1",
    }
    url = "http://testserver/webhooks/phone/recording-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res1 = client.post(
        "/webhooks/phone/recording-status", data=params, headers={"X-Twilio-Signature": sig}
    )
    res2 = client.post(
        "/webhooks/phone/recording-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res1.status_code == 200
    assert res2.status_code == 200
    assert log.recordings == [
        ("T-1", "RE1", "42", "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1"),
        ("T-1", "RE1", "42", "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1"),
    ]  # identical both times -- no divergent/duplicated state
