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

import asyncio
import base64
import hashlib
import hmac
import time
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from starlette.requests import Request
from structlog.testing import capture_logs

import chatbot.features.chat.phone.bridge as bridge_module
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
from chatbot.features.chat.router import ChatRouter, build_chat_router
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
_CALLER_ID = "+60129999999"


def _enabled_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        # PHONE_HANDOFF_ENABLED now REQUIRES PHONE_TRANSCRIPT_LIVE_ENABLED
        # (whole-branch review, Important 10) -- the dial-status callback
        # resolves the conversation by session id and never creates one.
        phone_transcript_live_enabled=True,
        phone_handoff_enabled=True,
        phone_handoff_target_number=_TARGET_NUMBER,
        phone_handoff_caller_id=_CALLER_ID,
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

    async def has_ticket_tag(self, ticket_id: str, tag: str) -> bool:
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
    settings = Settings(
        _env_file=None, phone_transcript_live_enabled=True, phone_handoff_enabled=True
    )
    resolver = HandoffTargetResolver(settings, _HoursLog())
    assert await resolver.resolve() is None


async def test_resolve_returns_none_when_no_caller_id_configured() -> None:
    """Review fix (Critical): a <Dial><Number> with no callerId defaults to
    the parent leg's From, which on the browser-softphone inbound path is a
    `client:` identifier Twilio rejects for a PSTN caller id (error 13214)
    -- refuse to resolve a target at all rather than dial blind."""
    settings = Settings(
        _env_file=None,
        phone_transcript_live_enabled=True,
        phone_handoff_enabled=True,
        phone_handoff_target_number=_TARGET_NUMBER,
    )  # phone_handoff_caller_id left at its "" default
    resolver = HandoffTargetResolver(settings, _HoursLog())
    with capture_logs() as captured:
        target = await resolver.resolve()
    assert target is None
    assert any(e["event"] == "phone_handoff_no_caller_id_configured" for e in captured)


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


async def test_prefetch_warms_the_hours_check_so_resolve_makes_no_http_call() -> None:
    """Whole-branch review fix (Important 6): `_attempt_transfer` runs
    INLINE in pump(), the sole Gemini->Twilio audio forwarder. The
    business-hours `GET /inboxes/{id}` must therefore happen off that path
    -- prefetch() at call start -- so resolve() answers from the warmed
    value and only the redirect stays inline."""
    settings = _enabled_settings(chatwoot_inbox_id=1)
    log = _HoursLog(inbox=_open_all_day_inbox())
    resolver = HandoffTargetResolver(settings, log)
    await resolver.prefetch()
    assert log.calls == [1]
    target = await resolver.resolve()
    assert target == HandoffTarget(kind="pstn", value=_TARGET_NUMBER)
    assert log.calls == [1]  # resolve() added no second lookup


async def test_prefetch_result_is_honoured_when_it_says_closed() -> None:
    """The warmed value must be a real answer, not just a latency trick:
    a prefetch that resolved to "closed" still blocks the transfer."""
    settings = _enabled_settings(chatwoot_inbox_id=1)
    log = _HoursLog(inbox=_closed_all_day_inbox())
    resolver = HandoffTargetResolver(settings, log)
    await resolver.prefetch()
    assert await resolver.resolve() is None
    assert log.calls == [1]


async def test_resolve_still_checks_hours_inline_when_prefetch_never_ran() -> None:
    """Cold cache (prefetch disabled, still in flight, or failed) must fall
    back to the pre-fix inline lookup rather than assuming "open"."""
    settings = _enabled_settings(chatwoot_inbox_id=1)
    log = _HoursLog(inbox=_closed_all_day_inbox())
    resolver = HandoffTargetResolver(settings, log)
    assert await resolver.resolve() is None
    assert log.calls == [1]


def test_dial_twiml_pstn_target() -> None:
    xml = dial_twiml(
        HandoffTarget(kind="pstn", value="+60123456789"),
        "https://example.test/webhooks/phone/dial-status",
        30,
        _CALLER_ID,
    )
    assert xml.startswith("<?xml")
    assert "<Dial" in xml
    assert 'action="https://example.test/webhooks/phone/dial-status"' in xml
    assert 'timeout="30"' in xml
    assert f'callerId="{_CALLER_ID}"' in xml
    assert "<Number>+60123456789</Number>" in xml
    assert "<Client>" not in xml


def test_dial_twiml_client_target() -> None:
    """Package C Task 5 switched the client noun to the long
    `<Client><Identity>` form (see `handoff_target._client_noun`): the
    shorthand `<Client>id</Client>` has no room for the `<Parameter>`
    children a ringing agent's browser needs."""
    xml = dial_twiml(
        HandoffTarget(kind="client", value="proton-agent-1"), "https://x/y", 15, _CALLER_ID
    )
    assert "<Client><Identity>proton-agent-1</Identity></Client>" in xml
    assert "<Number>" not in xml


def test_dial_twiml_renders_an_integer_timeout_for_a_float_setting() -> None:
    """Twilio rejects a non-integer `<Dial timeout>`, so the dial never connects.

    `phone_handoff_dial_timeout_seconds` is an env-sourced number: a tenant that
    writes `15.0` yields a float here. P11 removed the `int()` coercion from this
    builder while editing an adjacent line and no test noticed, because every
    existing case passes an int literal.
    """
    xml = dial_twiml(
        HandoffTarget(kind="pstn", value="+60123456789"),
        "https://x/y",
        15.0,  # type: ignore[arg-type]
        _CALLER_ID,
    )
    assert 'timeout="15"' in xml
    assert 'timeout="15.0"' not in xml


def test_dial_twiml_omits_caller_id_attr_when_empty() -> None:
    """The pure builder degrades gracefully (Twilio's own default
    behaviour) rather than raising if ever called with an empty caller id
    -- HandoffTargetResolver.resolve() is what actually prevents that in
    practice by refusing to resolve a target at all in that case."""
    xml = dial_twiml(HandoffTarget(kind="pstn", value="+60123456789"), "https://x/y", 15, "")
    assert "callerId" not in xml


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
        self.tags: list[tuple[str, str]] = []

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
        self.tags.append((ticket_id, tag))

    async def has_ticket_tag(self, ticket_id: str, tag: str) -> bool:
        return (ticket_id, tag) in self.tags

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
    handoff_resolver: HandoffTargetResolver | None = None,
) -> tuple[PhoneBridge, _FakeLive, _FakeLog, _FakeTwilioClient]:
    live = _FakeLive(scripted if scripted is not None else [_HANDOFF_CALL])
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
        handoff_resolver=handoff_resolver,
    )
    return bridge, live, log, twilio_client


async def test_bridge_prefetches_business_hours_at_call_start() -> None:
    """Whole-branch review fix (Important 6): the hours lookup is fired as
    a DETACHED task at the Twilio "start" event, off the audio path, so
    resolve() inside pump() answers from cache."""
    settings = _enabled_settings(chatwoot_inbox_id=1)
    hours = _HoursLog(inbox=_open_all_day_inbox())
    bridge, _live, _log, _twilio = _bridge(
        settings,
        scripted=[],
        handoff_resolver=HandoffTargetResolver(settings, hours),
    )
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert bridge._handoff_prefetch_task is not None
    await bridge._handoff_prefetch_task
    assert hours.calls == [1]


async def test_bridge_does_not_prefetch_business_hours_when_handoff_is_off() -> None:
    """Flags off -> byte-identical: no extra Chatwoot call at call start."""
    settings = Settings(_env_file=None, chatwoot_inbox_id=1)
    hours = _HoursLog(inbox=_open_all_day_inbox())
    bridge, _live, _log, _twilio = _bridge(
        settings,
        scripted=[],
        handoff_resolver=HandoffTargetResolver(settings, hours),
    )
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert bridge._handoff_prefetch_task is None
    assert hours.calls == []


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
    assert f'callerId="{_CALLER_ID}"' in twiml


async def test_handoff_issues_exactly_one_call_update() -> None:
    settings = _enabled_settings()
    bridge, _live, _log, twilio_client = _bridge(settings)
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert len(twilio_client.calls.updated) == 1


async def test_handoff_second_request_does_not_redial() -> None:
    """Review fix (Important 2): once a transfer has actually been dialled,
    a SECOND request_human_handoff arriving before the websocket tears down
    must not issue a second calls.update() -- that would replace the
    in-progress <Dial> and restart the ring from zero. Scripting exactly
    one ToolCall (as test_handoff_issues_exactly_one_call_update does)
    cannot detect a missing guard; this scripts two."""
    settings = _enabled_settings()
    second_call = ToolCall(
        id="h2", name="request_human_handoff", args={"reason": "billing", "summary": "again"}
    )
    bridge, live, _log, twilio_client = _bridge(settings, scripted=[_HANDOFF_CALL, second_call])
    bridge.call_sid = "CA1"

    await bridge.pump()

    assert len(twilio_client.calls.updated) == 1
    assert [r[2]["status"] for r in live.tool_responses] == ["transferring", "transferring"]


async def test_handoff_resolve_timeout_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review fix (Important 1): _attempt_transfer runs INLINE inside
    pump() -- the only coroutine forwarding Gemini audio to Twilio -- so a
    slow business-hours check must not hold that loop open indefinitely.
    Patches the bound down to something tiny and makes resolve() sleep
    well past it, then asserts pump() actually returns quickly (bounded by
    the patched timeout, not the sleep) and falls back cleanly."""
    monkeypatch.setattr(bridge_module, "_HANDOFF_RESOLVE_TIMEOUT_SECONDS", 0.05)

    class _SlowResolver(HandoffTargetResolver):
        def __init__(self) -> None:  # deliberately skip super().__init__
            pass

        async def resolve(self) -> HandoffTarget | None:
            await asyncio.sleep(0.3)
            return HandoffTarget(kind="pstn", value=_TARGET_NUMBER)

    settings = _enabled_settings()
    live = _FakeLive([_HANDOFF_CALL])
    log = _FakeLog()
    twilio_client = _FakeTwilioClient()
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
        handoff_resolver=_SlowResolver(),
    )
    bridge.call_sid = "CA1"

    started = time.monotonic()
    await bridge.pump()
    elapsed = time.monotonic() - started

    assert elapsed < 0.3  # bounded by the patched-down timeout, not the sleep
    assert live.tool_responses[0][2] == {"status": "ticket_created"}
    assert twilio_client.calls.updated == []


async def test_handoff_redirect_timeout_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same bound, on the redirect() side: a blackholed Twilio REST call
    must not hold the audio pump open either."""
    monkeypatch.setattr(bridge_module, "_HANDOFF_REDIRECT_TIMEOUT_SECONDS", 0.05)

    class _SlowFakeCalls(_FakeCalls):
        def update(self, twiml: str) -> object:
            time.sleep(0.3)
            return super().update(twiml)

    settings = _enabled_settings()
    twilio_client = _FakeTwilioClient()
    twilio_client.calls = _SlowFakeCalls()
    bridge, live, _log, _twilio_client = _bridge(settings, twilio_client=twilio_client)
    bridge.call_sid = "CA1"

    started = time.monotonic()
    await bridge.pump()
    elapsed = time.monotonic() - started

    assert elapsed < 0.3
    assert live.tool_responses[0][2] == {"status": "ticket_created"}


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
        _env_file=None,
        # PHONE_HANDOFF_ENABLED now REQUIRES PHONE_TRANSCRIPT_LIVE_ENABLED
        # (whole-branch review, Important 10) -- the dial-status callback
        # resolves the conversation by session id and never creates one.
        phone_transcript_live_enabled=True,
        phone_handoff_enabled=True,
        phone_handoff_target_number=_TARGET_NUMBER,
        phone_handoff_caller_id=_CALLER_ID,
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


def test_handoff_requires_ticket_at_call_start_and_fails_fast() -> None:
    """Whole-branch review fix (Important 10): a 30s `no-answer` dial-status
    callback can easily resolve before finalize() has created the
    conversation, in which case the owed `unanswered_handoff` tag is
    silently dropped (the handler answers 200; Twilio does not retry)."""
    with pytest.raises(ValueError, match="PHONE_TRANSCRIPT_LIVE_ENABLED"):
        Settings(_env_file=None, phone_handoff_enabled=True)


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
        phone_transcript_live_enabled=True,  # required by PHONE_HANDOFF_ENABLED
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
    settings = Settings(
        _env_file=None, phone_transcript_live_enabled=True, phone_handoff_enabled=True
    )  # no twilio_auth_token
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
    assert "<Say" not in res.text
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
    assert "<Say " in res.text and "<Hangup/>" in res.text
    # Review fix (Minor, round 2): language= alone doesn't select a Malay
    # voice -- both attributes must be present for each segment.
    assert 'language="en-US"' in res.text and 'voice="Google.en-US-Standard-C"' in res.text
    assert 'language="ms-MY"' in res.text and 'voice="Google.ms-MY-Standard-A"' in res.text
    assert log.found_calls == ["phone-CA1"]
    assert log.comments and log.comments[0] == (
        "T-1",
        f"[Handoff unanswered -- {status}]",
        "open",
    )
    assert log.tags == [("T-1", "unanswered_handoff")]


def test_dial_status_unanswered_callback_redelivery_does_not_duplicate_note() -> None:
    """Review fix (Important 3): Twilio may redeliver this callback (retry
    on a non-2xx, or a genuine duplicate). append_conversation_comment is
    an APPEND, so a naive retry would post a second "[Handoff unanswered]"
    note and re-flip the status on every redelivery -- gated on
    has_ticket_tag so the second delivery skips the comment (and the
    status flip riding along with it). This fake's add_ticket_tag doesn't
    itself dedupe (that idempotency is proven against the REAL adapter's
    GET-then-union in test_chatwoot_ticketing.py) -- only the comment-skip
    behaviour under test here.
    """
    log = _DialLog()
    client, settings = _client(log)
    params = {"CallSid": "CA1", "DialCallStatus": "no-answer"}
    url = "http://testserver/webhooks/phone/dial-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res1 = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )
    res2 = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res1.status_code == 200
    assert res2.status_code == 200
    assert len(log.comments) == 1
    assert log.comments[0] == ("T-1", "[Handoff unanswered -- no-answer]", "open")


class _TagCheckRaisesLog(_DialLog):
    async def has_ticket_tag(self, ticket_id: str, tag: str) -> bool:
        raise RuntimeError("chatwoot down")


def test_dial_status_tag_check_failure_fails_open_and_still_posts() -> None:
    """A has_ticket_tag failure fails to False (assume not yet handled) --
    worst case a Chatwoot outage duplicates the note on a genuine
    redelivery; it must never silently drop the very first delivery."""
    log = _TagCheckRaisesLog()
    client, settings = _client(log)
    params = {"CallSid": "CA1", "DialCallStatus": "busy"}
    url = "http://testserver/webhooks/phone/dial-status"
    sig = _sign(settings.twilio_auth_token, url, params)

    res = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert log.comments and log.comments[0] == ("T-1", "[Handoff unanswered -- busy]", "open")
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
    assert "<Say " in res.text
    assert log.comments == []
    assert log.tags == []


# --- P6 After-Call-Work entry on a completed dial (review-final I1) ----------


class _FakeACWController:
    """The one `ACWController` method the webhook calls, plus a record of when
    it was called relative to the response."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def start_after_call(self, conversation_id: int, *, now: Any = None) -> int | None:
        self.calls.append(conversation_id)
        return 7


def _acw_settings(**overrides: Any) -> Settings:
    kwargs: dict[str, Any] = {
        "phone_transcript_live_enabled": True,
        "phone_handoff_enabled": True,
        "twilio_auth_token": "test_token",
        "twilio_account_sid": "AC1",
        "acw_enabled": True,
        "twilio_webhook_base_url": _BASE_URL,
    }
    kwargs.update(overrides)
    return Settings(_env_file=None, **kwargs)


def _orchestrator(log: _FakeLog, settings: Settings) -> OrchestratorService:
    return OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        conversation_log_port=log,
        runner_factory=lambda _agent: _FakeRunner(),
    )


def _dial_request(params: dict[str, str], signature: str) -> Request:
    """A real Starlette Request carrying `params` as a signed Twilio form post,
    so the handler can be called directly (with a BackgroundTasks of our own)
    rather than through TestClient -- which is the only way to observe what has
    and has not run at the moment the response object is handed back."""
    body = urlencode(params).encode()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "server": ("example.ngrok.app", 443),
        "path": "/webhooks/phone/dial-status",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
            (b"x-twilio-signature", signature.encode()),
        ],
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def test_acw_entry_cannot_delay_the_twiml_on_a_completed_call() -> None:
    """Review-final I1. ACW entry used to be `await`ed inside this handler.
    The `Response` was *constructed* on the line above, which made it look
    settled, but constructing is not sending: the await still sat in front of
    the response, putting up to six sequential Chatwoot/Firestore calls (three
    on a 10 s timeout) inside Twilio's ~15 s webhook budget -- so a degraded
    Chatwoot made this callback answer in 25-35 s and Twilio abandoned it on a
    still-live call.

    Asserted where it can actually be seen: the handler is called directly with
    our own BackgroundTasks, and at the moment it returns the TwiML the ACW
    controller has NOT been touched -- work that has not started cannot delay
    anything. It is queued, and running the queue (what Starlette does after
    the response is sent) is what finally enters ACW.
    """
    log = _DialLog()
    log.found = "42"
    settings = _acw_settings()
    acw = _FakeACWController()
    router = ChatRouter(orchestrator=_orchestrator(log, settings), acw_controller=acw)
    params = {"CallSid": "CA9", "DialCallStatus": "completed"}
    sig = _sign("test_token", f"{_BASE_URL}/webhooks/phone/dial-status", params)
    tasks = BackgroundTasks()

    res = await router.phone_dial_status_webhook(_dial_request(params, sig), tasks)

    assert res.status_code == 200
    assert b"<Hangup/>" in res.body  # always valid TwiML: Twilio runs this next
    assert acw.calls == [], "ACW ran before the TwiML was returned"
    assert log.found_calls == [], "even the ticket lookup ran ahead of the response"
    assert len(tasks.tasks) == 1

    await tasks()  # what Starlette does once the response has been sent

    assert acw.calls == [42]


def test_a_completed_call_still_enters_acw_end_to_end() -> None:
    """The deferral must not turn into a silent drop: driven through the real
    app, a completed dial still puts the conversation's assignee into ACW."""
    log = _DialLog()
    log.found = "42"
    settings = _acw_settings()
    acw = _FakeACWController()
    app = create_app(settings)
    app.include_router(build_chat_router(_orchestrator(log, settings), acw_controller=acw))
    params = {"CallSid": "CA9", "DialCallStatus": "completed"}
    sig = _sign("test_token", f"{_BASE_URL}/webhooks/phone/dial-status", params)

    res = TestClient(app).post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )

    assert res.status_code == 200
    assert "<Hangup/>" in res.text
    assert acw.calls == [42]


async def test_acw_disabled_queues_nothing_on_a_completed_call() -> None:
    """`acw_enabled` is checked at this call site, not only inside the
    controller, so a tenant with the flag off makes zero extra calls at call
    end -- not even the `find_conversation_ticket` lookup -- and queues no
    background work at all."""
    log = _DialLog()
    log.found = "42"
    settings = _acw_settings(acw_enabled=False)
    acw = _FakeACWController()
    router = ChatRouter(orchestrator=_orchestrator(log, settings), acw_controller=acw)
    params = {"CallSid": "CA9", "DialCallStatus": "completed"}
    sig = _sign("test_token", f"{_BASE_URL}/webhooks/phone/dial-status", params)
    tasks = BackgroundTasks()

    res = await router.phone_dial_status_webhook(_dial_request(params, sig), tasks)

    assert res.status_code == 200
    assert b"<Hangup/>" in res.body
    assert tasks.tasks == []
    assert acw.calls == []
    assert log.found_calls == []
