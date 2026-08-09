"""P7 task 1: sentiment classification.

The plumbing (models.Sentiment, TurnResult.sentiment, detection.py's ticket
gate, router.py's API surface) already existed and is exercised elsewhere;
this file tests the one thing that was missing -- something actually writing
session_state["sentiment"] -- plus the two invariants that make it safe to
ship: exactly one Gemini call per turn (no second round-trip for
classification), and a flag that is genuinely off, not just differently
labelled.

Tests 1-5, 9 and 11 drive `OrchestratorService.handle_turn` through the same
`runner_factory` injection point `test_service.py` uses: a fake ADK Runner
stands in for the whole ADK+Gemini round trip and mutates session state the
way `classify_ticket_tool`'s new `sentiment` argument would, without a real
Gemini call. That is also what makes test 9 a meaningful regression guard --
`runner_calls` counts how many times `handle_turn` asked for a Runner at all,
so a future change that added a second round-trip just for classification
would be caught here, not just in production latency.

Tests 6-8 exercise `detection.should_open_ticket` directly (no ADK/Gemini
involved at all). Test 10 exercises `OrchestratorService.capture_conversation`,
the one place per-turn state reaches Chatwoot as a conversation custom
attribute.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.detection import should_open_ticket
from chatbot.features.chat.ports import ConversationLogPort, ConversationLogResult
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    # get_settings() is a cached singleton; another test module may have left
    # it pointed at "firestore". Pin it back, same defensive reset
    # test_service.py uses, so these tests don't depend on run order.
    get_settings().session_store = "memory"


# --- Fake ADK runner: replays a canned reply and optionally mutates session
# state the way classify_ticket_tool's `sentiment` argument would, without a
# real Gemini call. ---


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)

    def is_final_response(self) -> bool:
        return True


_UNSET = (
    object()
)  # distinguishes "the model omitted the argument" from any real value, including None


class _SentimentRunner:
    """Fake ADK Runner standing in for the support agent's per-turn Gemini call.

    `sentiment=_UNSET` (default) simulates the model never touching the
    argument at all -- the key is simply absent from session_state, exactly
    like a turn where `classify_ticket_tool` wasn't reached or the model left
    the (optional) argument out entirely. Any other value, including an
    invalid string, is written to state as-is so the read side's
    normalisation is what's under test, not this fake.
    """

    def __init__(
        self,
        reply: str,
        session_service: Any,
        session_id: str,
        sentiment: Any = _UNSET,
    ) -> None:
        self._reply = reply
        self._session_service = session_service
        self._session_id = session_id
        self._sentiment = sentiment

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        if self._sentiment is not _UNSET:
            session = self._session_service.sessions["chatbot"][self._session_id][self._session_id]
            session.state["sentiment"] = self._sentiment
        yield _FakeEvent(self._reply)


def _svc(settings: Any, runner_factory: Any) -> OrchestratorService:
    return OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=runner_factory,
    )


def _enabled_settings() -> Any:
    return get_settings().model_copy(update={"sentiment_classifier_enabled": True})


# --- 1-3: each level rides the existing per-turn tool call and lands on
# session_state["sentiment"], read back out via TurnResult.sentiment. ---


@pytest.mark.asyncio
async def test_a_positive_turn_writes_positive_to_session_state() -> None:
    settings = _enabled_settings()

    def fake_runner_factory(_agent: Any) -> _SentimentRunner:
        return _SentimentRunner(
            reply="Glad to hear it!",
            session_service=svc._adk_sessions,
            session_id="s-positive",
            sentiment="positive",
        )

    svc = _svc(settings, fake_runner_factory)
    result = await svc.handle_turn(
        session_id="s-positive", text="This service is great, thank you!"
    )

    assert result.sentiment == "positive"


@pytest.mark.asyncio
async def test_an_angry_turn_writes_negative() -> None:
    settings = _enabled_settings()

    def fake_runner_factory(_agent: Any) -> _SentimentRunner:
        return _SentimentRunner(
            reply="I'm sorry to hear that, let me help.",
            session_service=svc._adk_sessions,
            session_id="s-negative",
            sentiment="negative",
        )

    svc = _svc(settings, fake_runner_factory)
    result = await svc.handle_turn(
        session_id="s-negative", text="This is unacceptable, I've been waiting for hours!"
    )

    assert result.sentiment == "negative"


@pytest.mark.asyncio
async def test_a_safety_critical_turn_writes_urgent() -> None:
    settings = _enabled_settings()

    def fake_runner_factory(_agent: Any) -> _SentimentRunner:
        return _SentimentRunner(
            reply="Please pull over safely -- a human agent will call you immediately.",
            session_service=svc._adk_sessions,
            session_id="s-urgent",
            sentiment="urgent",
        )

    svc = _svc(settings, fake_runner_factory)
    result = await svc.handle_turn(
        session_id="s-urgent", text="My brakes just failed and I'm still driving!"
    )

    assert result.sentiment == "urgent"


# --- 4-5: the honesty requirement. Never None once the flag is on -- absence
# (the model omitted the argument) and garbage (an unrecognised value) both
# fall back to "neutral", not None. ---


@pytest.mark.asyncio
async def test_a_turn_where_the_model_omits_sentiment_falls_back_to_neutral() -> None:
    settings = _enabled_settings()

    def fake_runner_factory(_agent: Any) -> _SentimentRunner:
        # No `sentiment=` passed -- session_state never gets the key at all.
        return _SentimentRunner(
            reply="We're open 9am-6pm daily.",
            session_service=svc._adk_sessions,
            session_id="s-omitted",
        )

    svc = _svc(settings, fake_runner_factory)
    result = await svc.handle_turn(session_id="s-omitted", text="What time do you open?")

    assert result.sentiment == "neutral"


@pytest.mark.asyncio
async def test_the_fallback_is_neutral_and_never_none_when_the_flag_is_on() -> None:
    settings = _enabled_settings()

    def fake_runner_factory(_agent: Any) -> _SentimentRunner:
        # An unrecognised value reaching session_state (e.g. a stale/garbage
        # write) must be normalised exactly like an absent one -- the read
        # side, not just the tool's own default, guarantees "never None".
        return _SentimentRunner(
            reply="Let me check that for you.",
            session_service=svc._adk_sessions,
            session_id="s-garbage",
            sentiment="mildly_annoyed",
        )

    svc = _svc(settings, fake_runner_factory)
    result = await svc.handle_turn(session_id="s-garbage", text="hmm, not sure about this")

    assert result.sentiment is not None
    assert result.sentiment == "neutral"


# --- 6-8: the gate got stricter, not different. ---


def test_urgent_trips_the_existing_ticket_creation_gate() -> None:
    assert should_open_ticket({"sentiment": "urgent"}) is True


def test_negative_still_trips_the_gate_exactly_as_before() -> None:
    assert should_open_ticket({"sentiment": "negative"}) is True


def test_positive_and_neutral_do_not_trip_the_gate() -> None:
    assert should_open_ticket({"sentiment": "positive"}) is False
    assert should_open_ticket({"sentiment": "neutral"}) is False


# --- 9: the latency/cost guard. ---


@pytest.mark.asyncio
async def test_exactly_one_gemini_call_is_made_per_turn() -> None:
    settings = _enabled_settings()
    runner_calls = 0

    def fake_runner_factory(_agent: Any) -> _SentimentRunner:
        nonlocal runner_calls
        runner_calls += 1
        return _SentimentRunner(
            reply="Here is the answer you were after.",
            session_service=svc._adk_sessions,
            session_id="s-one-call",
            sentiment="negative",
        )

    svc = _svc(settings, fake_runner_factory)
    result = await svc.handle_turn(session_id="s-one-call", text="This is terrible service")

    # Sentiment rides the SAME per-turn tool call -- a second Runner
    # invocation here would mean someone added a dedicated classification
    # round-trip, doubling latency/cost on every customer message.
    assert runner_calls == 1
    assert result.sentiment == "negative"


# --- 10: the conversation custom attribute, via the existing merge-safe path
# (`ConversationLogPort.set_ticket_classification`), so it reaches BigQuery
# via the existing mapping without inventing new Chatwoot API surface. ---


class _LiveSession:
    """A session whose `.state` is a live dict (no copy-on-read).

    Mirrors test_capture_conversation.py's double: the installed ADK
    InMemorySessionService deep-copies state on get_session, which would
    defeat a test that pre-seeds state["sentiment"] and expects
    capture_conversation to read it back.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


class _LiveSessions:
    def __init__(self) -> None:
        self._sessions: dict[str, _LiveSession] = {}

    async def create_session(
        self, *, app_name: str, user_id: str, session_id: str, state: dict[str, Any]
    ) -> _LiveSession:
        session = _LiveSession(state)
        self._sessions[session_id] = session
        return session

    async def get_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> _LiveSession | None:
        return self._sessions.get(session_id)


class _FakeLog(ConversationLogPort):
    """Minimal ConversationLogPort recording set_ticket_classification calls."""

    def __init__(self) -> None:
        self.classification_calls: list[tuple[str, dict[str, Any]]] = []

    async def ensure_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> str:
        return "T1"

    async def rotate_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> str:
        return "T2"

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
        sentiment: str | None = None,
    ) -> None:
        self.classification_calls.append(
            (
                ticket_id,
                {
                    "case_type": case_type,
                    "division": division,
                    "concern": concern,
                    "sentiment": sentiment,
                },
            )
        )

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        return ("", None, None)

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        return None

    async def set_call_recording(
        self,
        ticket_id: str,
        *,
        recording_sid: str,
        recording_duration: str,
        recording_url: str,
    ) -> None:
        return None

    async def get_inbox_working_hours(self, inbox_id: int) -> dict[str, Any] | None:
        return None

    async def has_ticket_tag(self, ticket_id: str, tag: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_the_sentiment_reaches_the_conversation_custom_attributes() -> None:
    settings = _enabled_settings()
    log = _FakeLog()
    orch = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        conversation_log_port=log,
        runner_factory=lambda _agent: None,
    )
    orch._adk_sessions = _LiveSessions()  # type: ignore[assignment]
    session_id = "whatsapp-+60111222333"
    await orch._adk_sessions.create_session(
        app_name="chatbot",
        user_id=session_id,
        session_id=session_id,
        state={
            "session_id": session_id,
            "chat_history": [{"role": "user", "text": "my car caught fire!!"}],
            "sentiment": "urgent",
        },
    )

    await orch.capture_conversation(session_id, channel="WhatsApp")

    assert log.classification_calls, "expected set_ticket_classification to be called"
    ticket_id, kwargs = log.classification_calls[0]
    assert ticket_id == "T1"
    assert kwargs["sentiment"] == "urgent"


# --- 11: off means off, byte-for-byte. ---


@pytest.mark.asyncio
async def test_the_flag_off_leaves_sentiment_none_exactly_as_today() -> None:
    settings = get_settings().model_copy(update={"sentiment_classifier_enabled": False})

    def fake_runner_factory(_agent: Any) -> _SentimentRunner:
        # Simulates a stray write reaching session_state despite the flag
        # being off (defence in depth) -- the gate must still report None.
        return _SentimentRunner(
            reply="Sure, here's the info.",
            session_service=svc._adk_sessions,
            session_id="s-flag-off",
            sentiment="urgent",
        )

    svc = _svc(settings, fake_runner_factory)
    result = await svc.handle_turn(session_id="s-flag-off", text="hello")

    assert result.sentiment is None
