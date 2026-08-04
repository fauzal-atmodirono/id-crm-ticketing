"""Package C Task 6: real hand-off of a live call to a human.

Covers (per the task brief):
- `request_human_handoff` issues exactly one call-update with well-formed
  dial TwiML, and the tool response becomes {"status": "transferring"}
  rather than the old, inaccurate {"status": "ticket_created"};
- a Twilio redirect failure (or no target/disabled/unconfigured/out-of-
  hours) leaves the call alive and falls back to today's ticket-only
  behaviour, never raising out of the tool-call turn;
- HandoffTargetResolver: disabled / unconfigured / out-of-business-hours
  all resolve to None; a hours-check failure fails OPEN (attempts the
  dial), matching the rest of this package's fail-open convention;
- dial_twiml's shape (<Number>/<Client>, action, timeout);
- every /webhooks/phone/dial-status outcome (completed, no-answer, busy,
  failed, and an unrecognised status) drives the right fallback;
- the dial-status webhook is only registered when phone_handoff_enabled is
  on, and refuses (401) rather than skips verification when no Twilio auth
  token is configured -- same convention as recording-status;
- flags off -> zero new Twilio/CRM calls (byte-identical).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
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
from chatbot.features.chat.phone.handoff_target import (
    HandoffTarget,
    HandoffTargetResolver,
    dial_twiml,
)
from chatbot.features.chat.phone.live_events import LiveEvent, ToolCall
from chatbot.features.chat.ports import ConversationLogResult
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


@pytest.fixture(autouse=True)
def _fake_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """See test_recording.py's identical fixture: OrchestratorService's
    constructor always eagerly builds a real google-genai Client(), even
    for tests below that only exercise the dial-status webhook. Nothing in
    this file ever makes a real Gemini call."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-a-real-credential")


_BASE_URL = "https://example.ngrok.app"
_TARGET_NUMBER = "+60123456789"


def _enabled_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        phone_handoff_enabled=True,
        phone_handoff_target_number=_TARGET_NUMBER,
        twilio_webhook_base_url=_BASE_URL,
        **overrides,
    )


# --- handoff_target.py: pure/unit -------------------------------------------


class _HoursLog:
    """Minimal ConversationLogPort fake -- only get_inbox_working_hours."""

    def __init__(
        self, inbox: dict[str, Any] | None = None, raises: Exception | None = None
    ) -> None:
        self.inbox = inbox
        self.raises = raises
        self.calls: list[int] = []

    async def get_inbox_working_hours(self, inbox_id: int) -> dict[str, Any] | None:
        self.calls.append(inbox_id)
        if self.raises is not None:
            raise self.raises
        return self.inbox

    # Unused by the resolver -- present only so this can stand in for the
    # full ConversationLogPort where a test needs one.
    async def ensure_conversation_ticket(self, *a: Any, **k: Any) -> str:
        raise NotImplementedError

    async def rotate_conversation_ticket(self, *a: Any, **k: Any) -> str:
        raise NotImplementedError

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        raise NotImplementedError

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        raise NotImplementedError

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        raise NotImplementedError

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        raise NotImplementedError

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        raise NotImplementedError

    async def set_ticket_classification(self, ticket_id: str, **kwargs: Any) -> None:
        raise NotImplementedError

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        raise NotImplementedError

    async def set_call_recording(self, ticket_id: str, **kwargs: Any) -> None:
        raise NotImplementedError


def _open_all_day_inbox() -> dict[str, Any]:
    dow = datetime.now(UTC).isoweekday() % 7
    return {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [{"day_of_week": dow, "open_all_day": True}],
    }


def _closed_all_day_inbox() -> dict[str, Any]:
    dow = datetime.now(UTC).isoweekday() % 7
    return {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [{"day_of_week": dow, "closed_all_day": True}],
    }


async def test_resolve_returns_none_when_disabled() -> None:
    settings = Settings(_env_file=None, phone_handoff_target_number=_TARGET_NUMBER)
    resolver = HandoffTargetResolver(settings, _HoursLog())
    assert await resolver.resolve() is None


async def test_resolve_returns_none_when_no_target_number_configured() -> None:
    settings = Settings(_env_file=None, phone_handoff_enabled=True)
    resolver = HandoffTargetResolver(settings, _HoursLog())
    assert await resolver.resolve() is None


async def test_resolve_returns_pstn_target_when_within_hours() -> None:
    settings = _enabled_settings(chatwoot_inbox_id=1)
    resolver = HandoffTargetResolver(settings, _HoursLog(inbox=_open_all_day_inbox()))
    target = await resolver.resolve()
    assert target == HandoffTarget(kind="pstn", value=_TARGET_NUMBER)


async def test_resolve_returns_none_when_out_of_business_hours() -> None:
    settings = _enabled_settings(chatwoot_inbox_id=1)
    log = _HoursLog(inbox=_closed_all_day_inbox())
    resolver = HandoffTargetResolver(settings, log)
    assert await resolver.resolve() is None
    assert log.calls == [1]


async def test_resolve_ignores_hours_when_no_inbox_configured() -> None:
    """chatwoot_inbox_id == 0 (unset) -> skip the check entirely, same as
    "not configured -> always open" everywhere else in this package."""
    settings = _enabled_settings(chatwoot_inbox_id=0)
    log = _HoursLog(inbox=_closed_all_day_inbox())
    resolver = HandoffTargetResolver(settings, log)
    target = await resolver.resolve()
    assert target == HandoffTarget(kind="pstn", value=_TARGET_NUMBER)
    assert log.calls == []  # never even asked


async def test_resolve_fails_open_when_hours_check_returns_none() -> None:
    settings = _enabled_settings(chatwoot_inbox_id=1)
    resolver = HandoffTargetResolver(settings, _HoursLog(inbox=None))
    target = await resolver.resolve()
    assert target == HandoffTarget(kind="pstn", value=_TARGET_NUMBER)


async def test_resolve_fails_open_when_hours_check_raises() -> None:
    settings = _enabled_settings(chatwoot_inbox_id=1)
    resolver = HandoffTargetResolver(settings, _HoursLog(raises=RuntimeError("chatwoot down")))
    with capture_logs() as captured:
        target = await resolver.resolve()
    assert target == HandoffTarget(kind="pstn", value=_TARGET_NUMBER)
    assert any(e["event"] == "phone_handoff_hours_check_failed" for e in captured)


def test_dial_twiml_pstn_target() -> None:
    xml = dial_twiml(
        HandoffTarget(kind="pstn", value="+60123456789"),
        "https://example.test/webhooks/phone/dial-status",
        30,
    )
    assert xml.startswith("<?xml")
    assert "<Dial" in xml
    assert 'action="https://example.test/webhooks/phone/dial-status"' in xml
    assert 'timeout="30"' in xml
    assert "<Number>+60123456789</Number>" in xml
    assert "<Client>" not in xml


def test_dial_twiml_client_target() -> None:
    xml = dial_twiml(HandoffTarget(kind="client", value="proton-agent-1"), "https://x/y", 15)
    assert "<Client>proton-agent-1</Client>" in xml
    assert "<Number>" not in xml


# --- bridge wiring: request_human_handoff -----------------------------------


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent] | None = None) -> None:
        self._scripted = scripted or []
        self.tool_responses: list[tuple[str, str, dict[str, Any]]] = []

    async def send_audio(self, pcm16k: bytes) -> None:
        return None

    async def send_tool_response(self, call_id: str, name: str, response: dict[str, Any]) -> None:
        self.tool_responses.append((call_id, name, response))

    async def send_text_hint(self, text: str) -> None:
        return None

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        return []


class _FakeLog:
    """Minimal ConversationLogPort fake for bridge-level handoff tests."""

    def __init__(self) -> None:
        self.working_hours: dict[str, Any] | None = None

    async def ensure_conversation_ticket(self, *a: Any, **k: Any) -> str:
        return "T-1"

    async def rotate_conversation_ticket(self, *a: Any, **k: Any) -> str:
        return "T-1"

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        return None

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

    async def set_ticket_classification(self, ticket_id: str, **kwargs: Any) -> None:
        return None

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        return ("", None, None)

    async def set_call_recording(self, ticket_id: str, **kwargs: Any) -> None:
        return None

    async def get_inbox_working_hours(self, inbox_id: int) -> dict[str, Any] | None:
        return self.working_hours


class _FakeCalls:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.updated: list[tuple[str, str]] = []
        self._sid = ""

    def __call__(self, call_sid: str) -> _FakeCalls:
        self._sid = call_sid
        return self

    def update(self, twiml: str) -> object:
        if self.raises:
            raise self.raises
        self.updated.append((self._sid, twiml))
        return object()


class _FakeTwilioClient:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls = _FakeCalls(raises)


_HANDOFF_CALL = ToolCall(
    id="h1", name="request_human_handoff", args={"reason": "billing", "summary": "double charge"}
)


def _bridge(
    settings: Settings,
    log: _FakeLog | None = None,
    twilio_client: _FakeTwilioClient | None = None,
    scripted: list[LiveEvent] | None = None,
) -> tuple[PhoneBridge, _FakeLive, _FakeLog, _FakeTwilioClient]:
    live = _FakeLive(scripted if scripted is not None else [_HANDOFF_CALL])
    log = log or _FakeLog()
    twilio_client = twilio_client or _FakeTwilioClient()
    call_control = CallControl(settings, client=twilio_client)

    async def send_twilio(_msg: dict[str, object]) -> None:
        return None

    bridge = PhoneBridge(
        live, _FakeKnowledge(), log, send_twilio, settings, call_control=call_control
    )
    return bridge, live, log, twilio_client


async def test_handoff_disabled_keeps_todays_ticket_created_status() -> None:
    """Flags off -> byte-identical to before this task: no redirect
    attempt, same status string as today."""
    settings = Settings(_env_file=None)
    bridge, live, _log, twilio_client = _bridge(settings)
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert live.tool_responses[0][2] == {"status": "ticket_created"}
    assert twilio_client.calls.updated == []
    assert bridge.handoff == {"reason": "billing", "summary": "double charge"}


async def test_handoff_redirects_and_responds_transferring() -> None:
    settings = _enabled_settings()
    bridge, live, _log, twilio_client = _bridge(settings)
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert live.tool_responses[0][2] == {"status": "transferring"}
    assert len(twilio_client.calls.updated) == 1
    call_sid, twiml = twilio_client.calls.updated[0]
    assert call_sid == "CA1"
    assert f"{_BASE_URL}/webhooks/phone/dial-status" in twiml
    assert f"<Number>{_TARGET_NUMBER}</Number>" in twiml
    assert 'timeout="30"' in twiml


async def test_handoff_issues_exactly_one_call_update() -> None:
    settings = _enabled_settings()
    bridge, _live, _log, twilio_client = _bridge(settings)
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert len(twilio_client.calls.updated) == 1


async def test_handoff_twilio_failure_falls_back_and_does_not_raise() -> None:
    settings = _enabled_settings()
    bridge, live, _log, _twilio_client = _bridge(
        settings, twilio_client=_FakeTwilioClient(raises=RuntimeError("twilio down"))
    )
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert live.tool_responses[0][2] == {"status": "ticket_created"}
    # The call must still be usable afterwards -- a subsequent media frame
    # must not raise either.
    payload = base64.b64encode(b"\xff" * 160).decode()
    await bridge.handle_twilio({"event": "media", "media": {"payload": payload}})


async def test_handoff_no_call_sid_falls_back_without_attempting_redirect() -> None:
    settings = _enabled_settings()
    bridge, live, _log, twilio_client = _bridge(settings)
    # call_sid stays None -- Twilio "start" never arrived.

    await bridge.pump()

    assert live.tool_responses[0][2] == {"status": "ticket_created"}
    assert twilio_client.calls.updated == []


async def test_handoff_no_action_url_falls_back_without_attempting_redirect() -> None:
    """twilio_webhook_base_url unset -> no callback base can be built for
    the <Dial action>, so this must not dial blind."""
    settings = Settings(
        _env_file=None, phone_handoff_enabled=True, phone_handoff_target_number=_TARGET_NUMBER
    )
    bridge, live, _log, twilio_client = _bridge(settings)
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert live.tool_responses[0][2] == {"status": "ticket_created"}
    assert twilio_client.calls.updated == []


async def test_handoff_out_of_business_hours_falls_back_without_dialing() -> None:
    settings = _enabled_settings(chatwoot_inbox_id=1)
    log = _FakeLog()
    dow = datetime.now(UTC).isoweekday() % 7
    log.working_hours = {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [{"day_of_week": dow, "closed_all_day": True}],
    }
    bridge, live, _log2, twilio_client = _bridge(settings, log=log)
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert live.tool_responses[0][2] == {"status": "ticket_created"}
    assert twilio_client.calls.updated == []


async def test_handoff_settings_default_off() -> None:
    settings = Settings(_env_file=None)
    assert settings.phone_handoff_enabled is False
    assert settings.phone_handoff_target_number == ""
    assert settings.phone_handoff_timeout_seconds == 30


# --- /webhooks/phone/dial-status --------------------------------------------


class _FakeRunner:
    """Empty ADK runner -- avoids OrchestratorService building a real
    google-genai client just to construct the app for these webhook-only
    tests."""

    async def run_async(self, **_: Any) -> AsyncGenerator[Any, None]:
        for _i in range(0):
            yield None


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = hmac.new(token.encode(), s.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _client(log: _FakeLog, settings: Settings | None = None) -> tuple[TestClient, Settings]:
    settings = settings or Settings(
        _env_file=None,
        phone_handoff_enabled=True,
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
    return TestClient(app), settings


class _DialLog(_FakeLog):
    def __init__(self) -> None:
        super().__init__()
        self.found: str | None = "T-1"
        self.found_calls: list[str] = []
        self.comments: list[tuple[str, str, str | None]] = []
        self.tags: list[tuple[str, str]] = []

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        self.found_calls.append(session_id)
        return self.found

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        self.comments.append((ticket_id, text, status))
        return ConversationLogResult.OK

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        self.tags.append((ticket_id, tag))


def test_dial_status_route_not_registered_when_flag_off() -> None:
    """Unlike recording-status, this route must not exist at all on a
    tenant that never turned the feature on."""
    settings = Settings(
        _env_file=None, twilio_auth_token="test_token", twilio_account_sid="AC1"
    )  # phone_handoff_enabled left at its False default
    client, _settings = _client(_DialLog(), settings=settings)

    res = client.post(
        "/webhooks/phone/dial-status",
        data={"CallSid": "CA1", "DialCallStatus": "completed"},
        headers={"X-Twilio-Signature": "irrelevant"},
    )

    assert res.status_code == 404


def test_dial_status_refuses_when_no_auth_token_configured() -> None:
    settings = Settings(_env_file=None, phone_handoff_enabled=True)  # no twilio_auth_token
    client, _settings = _client(_DialLog(), settings=settings)

    res = client.post(
        "/webhooks/phone/dial-status", data={"CallSid": "CA1", "DialCallStatus": "no-answer"}
    )

    assert res.status_code == 401


def test_dial_status_rejects_bad_signature() -> None:
    client, _settings = _client(_DialLog())
    res = client.post(
        "/webhooks/phone/dial-status",
        data={"CallSid": "CA1", "DialCallStatus": "completed"},
        headers={"X-Twilio-Signature": "wrong"},
    )
    assert res.status_code == 401


def test_dial_status_completed_hangs_up_without_crm_write() -> None:
    log = _DialLog()
    client, settings = _client(log)
    params = {"CallSid": "CA1", "DialCallStatus": "completed"}
    url = "http://testserver/webhooks/phone/dial-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert "<Hangup/>" in res.text
    assert "<Say>" not in res.text
    assert log.found_calls == []
    assert log.comments == []
    assert log.tags == []


@pytest.mark.parametrize("status", ["no-answer", "busy", "failed", "canceled"])
def test_dial_status_unanswered_outcomes_apologise_and_tag(status: str) -> None:
    log = _DialLog()
    client, settings = _client(log)
    params = {"CallSid": "CA1", "DialCallStatus": status}
    url = "http://testserver/webhooks/phone/dial-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert "<Say>" in res.text and "<Hangup/>" in res.text
    assert log.found_calls == ["phone-CA1"]
    assert log.comments and log.comments[0] == (
        "T-1",
        f"[Handoff unanswered -- {status}]",
        "open",
    )
    assert log.tags == [("T-1", "unanswered_handoff")]


def test_dial_status_ignores_missing_call_sid() -> None:
    log = _DialLog()
    client, settings = _client(log)
    params = {"DialCallStatus": "no-answer"}
    url = "http://testserver/webhooks/phone/dial-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert "<Hangup/>" in res.text
    assert log.found_calls == []
    assert log.comments == []


def test_dial_status_unknown_call_is_ignored_rather_than_raising() -> None:
    log = _DialLog()
    log.found = None
    client, settings = _client(log)
    params = {"CallSid": "CA1", "DialCallStatus": "no-answer"}
    url = "http://testserver/webhooks/phone/dial-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200  # ignored, not raised -- still valid TwiML
    assert "<Say>" in res.text
    assert log.comments == []
    assert log.tags == []
