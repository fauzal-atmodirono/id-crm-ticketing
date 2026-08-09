"""P7 task 11a: what the model ACTUALLY receives on a live turn.

Tasks 2 (sentiment tone adjustment) and 8 (media diagnosis prompting) each
shipped a pure, fully unit-tested composer -- `chat_persona.select_tone_block`
/ `compose_chat_agent_instruction` and `prompts.build_agent_instruction` --
and neither was reachable from a real turn: nothing passed the tone kwargs,
and the wiring point had no per-turn media signal. Green tests on a function
nobody calls is exactly the failure mode this file exists to prevent, so
every test here drives `OrchestratorService.handle_turn` and asserts on the
instruction string the ADK InstructionProvider actually served, not on a
composer's return value.

## Why the fake runner probes the provider twice

`_ProviderProbeRunner` stands in for the whole ADK+Gemini round trip, and it
reproduces the one ADK behaviour the tone fix depends on: the
InstructionProvider is re-resolved before EVERY LLM request inside a single
run (`google.adk.flows.llm_flows.instructions._build_instructions` runs as a
per-request processor), and a tool's `tool_context.state[...] = x` write is
visible immediately through `ReadonlyContext.state`
(`sessions.state.State.__setitem__` writes straight into
`invocation_context.session.state`). So request 1 asks for the instruction,
the tool runs, and request 2 -- the one that generates the customer-facing
reply -- asks again and sees this turn's own sentiment.

The probe does not fake the state write: it calls the REAL
`classify_ticket_tool` off the service's real support agent, so the flag
gating and the `sentiment`/`sentiment_at` keys under test are production
code, not a re-implementation in a test double. Only Gemini is faked.

`instructions[-1]` is therefore "what the model was told when it wrote the
reply", and that is what these tests assert against.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig
from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.agents import build_ai_agent
from chatbot.features.chat.chat_persona import compose_chat_agent_instruction
from chatbot.features.chat.prompts import (
    AGENT_INSTRUCTION,
    DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION,
)
from chatbot.features.chat.service import (
    OrchestratorService,
    _media_kinds_in_user_content,
)
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    # get_settings() is a cached singleton another module may have repointed at
    # firestore; same defensive reset test_service.py/test_sentiment.py use.
    get_settings().session_store = "memory"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


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


class _Ctx:
    """Mirrors the ReadonlyContext surface the provider reads.

    `.session.id` (already used pre-P7), `.state` as a MappingProxyType over
    the LIVE session state dict (exactly what `ReadonlyContext.state`
    returns), and `.user_content` -- the `types.Content` the run was started
    with, which is where the per-turn media signal comes from.
    """

    def __init__(self, session_id: str, state: dict[str, Any], user_content: Any) -> None:
        self.session = SimpleNamespace(id=session_id)
        self._state = state
        self.user_content = user_content
        self.invocation_id = "inv-1"

    @property
    def state(self) -> MappingProxyType[str, Any]:
        return MappingProxyType(self._state)


def _find_tool(agent: Any, name: str) -> Any:
    for tool in agent.tools:
        func = getattr(tool, "func", tool)
        if getattr(func, "__name__", "") == name:
            return func
    raise AssertionError(f"tool {name} not registered")


class _ProviderProbeRunner:
    """Fake ADK Runner recording every instruction the provider served.

    `tool_sentiment=None` simulates a turn where the model called no tool at
    all (one LLM request, so one instruction). Any string calls the real
    `classify_ticket_tool` with that sentiment between the two requests.
    """

    def __init__(
        self,
        svc: OrchestratorService,
        *,
        reply: str = "ok",
        tool_sentiment: str | None = None,
    ) -> None:
        self._svc = svc
        self._reply = reply
        self._tool_sentiment = tool_sentiment
        self.instructions: list[str] = []

    async def run_async(
        self, *, user_id: str, session_id: str, new_message: Any, **_: Any
    ) -> AsyncIterator[_FakeEvent]:
        state = self._svc._adk_sessions.sessions["chatbot"][session_id][session_id].state  # type: ignore[attr-defined]
        ctx = _Ctx(session_id, state, new_message)

        # LLM request 1 of this run.
        self.instructions.append(self._svc._chat_instruction_provider(ctx))

        if self._tool_sentiment is not None:
            tool = _find_tool(self._svc._support_agent, "classify_ticket_tool")
            await tool(
                SimpleNamespace(state=state),
                category="Support",
                subcategory="Escalation",
                priority="URGENT",
                sla_minutes=30,
                case_type="Complaint",
                vehicle_model="",
                sentiment=self._tool_sentiment,
            )
            # LLM request 2: the one that generates the reply text.
            self.instructions.append(self._svc._chat_instruction_provider(ctx))

        yield _FakeEvent(self._reply)


def _svc(settings: Any, runner_holder: dict[str, Any], **runner_kwargs: Any) -> OrchestratorService:
    """A real OrchestratorService whose runner is the probe above.

    `runner_holder["runner"]` is populated on first use so a test can read
    back the instructions, and `runner_holder["calls"]` counts how many times
    a Runner was requested -- the one-Gemini-call-per-turn guard.
    """
    runner_holder["calls"] = 0

    def factory(_agent: Any) -> _ProviderProbeRunner:
        runner_holder["calls"] += 1
        runner = _ProviderProbeRunner(runner_holder["svc"], **runner_kwargs)
        runner_holder["runner"] = runner
        return runner

    svc = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=factory,
    )
    runner_holder["svc"] = svc
    return svc


def _settings(**overrides: Any) -> Any:
    return get_settings().model_copy(update=overrides)


def _tone_on() -> Any:
    return _settings(sentiment_classifier_enabled=True, sentiment_tone_adjustment_enabled=True)


def _assistant(**config_kwargs: Any) -> Assistant:
    return Assistant(
        id="asst_wiring",
        name="A",
        description="",
        product_name="",
        config=AssistantConfig(**config_kwargs),
        enabled=True,
        is_default=False,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _stub_assistant(svc: OrchestratorService, assistant: Any) -> None:
    """Pin the resolved assistant without needing Firestore."""

    async def _resolve(_inbox_id: int | None) -> Any:
        return assistant

    svc._resolve_chat_assistant = _resolve  # type: ignore[method-assign]


_APOLOGETIC = "acknowledge the trouble this has caused before anything else"
_IMAGE_B64 = "aW1hZ2VieXRlcw=="  # base64("imagebytes")


# --------------------------------------------------------------------------
# Job 1 -- tone
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_angry_turn_reaches_the_model_with_the_apologetic_tone_block() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment="negative")

    await svc.handle_turn(session_id="s-angry", text="This is terrible service")

    served = holder["runner"].instructions
    # The reply is generated on the LAST request of the run, after the tool
    # reported this turn's sentiment -- so the customer's FIRST angry message
    # already gets the measured/apologetic register.
    assert _APOLOGETIC in served[-1]
    # ...and it is an addition, never a rewrite of the base instruction.
    assert served[-1].startswith(AGENT_INSTRUCTION)


@pytest.mark.asyncio
async def test_the_tone_block_is_absent_before_the_tool_reports_this_turns_sentiment() -> None:
    """The documented one-request lag inside the turn.

    Request 1 cannot know a sentiment that request 1's own output produces, so
    it carries today's wording. This is only observable if the model answers
    without calling any tool at all (see the next test).
    """
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment="negative")

    await svc.handle_turn(session_id="s-angry-first-request", text="This is terrible service")

    served = holder["runner"].instructions
    assert len(served) == 2
    assert served[0] == AGENT_INSTRUCTION
    assert _APOLOGETIC not in served[0]


@pytest.mark.asyncio
async def test_a_toolless_angry_turn_falls_back_to_todays_wording() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment=None)

    await svc.handle_turn(session_id="s-toolless", text="This is terrible service")

    served = holder["runner"].instructions
    assert served == [AGENT_INSTRUCTION]


@pytest.mark.asyncio
async def test_an_operator_tone_override_reaches_the_model_instead_of_the_default() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment="negative")
    _stub_assistant(svc, _assistant(tone_negative="Bersabar dan minta maaf dahulu."))

    await svc.handle_turn(session_id="s-override", text="Teruk betul servis ni", inbox_id=7)

    served = holder["runner"].instructions[-1]
    assert "Bersabar dan minta maaf dahulu." in served
    assert _APOLOGETIC not in served


@pytest.mark.asyncio
async def test_a_recent_earlier_turns_sentiment_carries_into_the_next_turn() -> None:
    """The customer stays angry between turns; a turn with no tool call still
    gets the register the previous turn established."""
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment=None)
    await svc.handle_turn(session_id="s-carry", text="hello")
    state = svc._adk_sessions.sessions["chatbot"]["s-carry"]["s-carry"].state  # type: ignore[attr-defined]
    state["sentiment"] = "negative"
    state["sentiment_at"] = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()

    await svc.handle_turn(session_id="s-carry", text="and now this")

    assert _APOLOGETIC in holder["runner"].instructions[-1]


@pytest.mark.asyncio
async def test_a_stale_sentiment_is_not_applied_to_a_fresh_message() -> None:
    """An hour-old anger must not colour a cheerful new message."""
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment=None)
    await svc.handle_turn(session_id="s-stale", text="hello")
    state = svc._adk_sessions.sessions["chatbot"]["s-stale"]["s-stale"].state  # type: ignore[attr-defined]
    state["sentiment"] = "negative"
    state["sentiment_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    await svc.handle_turn(session_id="s-stale", text="thanks, all sorted!")

    assert holder["runner"].instructions == [AGENT_INSTRUCTION]


@pytest.mark.asyncio
async def test_an_unstamped_sentiment_is_treated_as_stale() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment=None)
    await svc.handle_turn(session_id="s-unstamped", text="hello")
    state = svc._adk_sessions.sessions["chatbot"]["s-unstamped"]["s-unstamped"].state  # type: ignore[attr-defined]
    state["sentiment"] = "negative"  # no sentiment_at: a pre-upgrade session

    await svc.handle_turn(session_id="s-unstamped", text="hi again")

    assert holder["runner"].instructions == [AGENT_INSTRUCTION]


@pytest.mark.asyncio
async def test_the_classify_tool_stamps_freshness_only_while_the_flag_is_on() -> None:
    """The stamp the freshness window reads is written by production code."""
    on = build_ai_agent(
        _settings(sentiment_classifier_enabled=True),
        InMemoryTicketingAdapter(),
        InMemoryKnowledgeAdapter(),
    )
    off = build_ai_agent(
        _settings(sentiment_classifier_enabled=False),
        InMemoryTicketingAdapter(),
        InMemoryKnowledgeAdapter(),
    )
    args: dict[str, Any] = {
        "category": "Support",
        "subcategory": "Escalation",
        "priority": "URGENT",
        "sla_minutes": 30,
        "case_type": "Complaint",
        "vehicle_model": "",
        "sentiment": "negative",
    }

    ctx_on = SimpleNamespace(state={})
    await _find_tool(on, "classify_ticket_tool")(ctx_on, **args)
    assert ctx_on.state["sentiment"] == "negative"
    datetime.fromisoformat(ctx_on.state["sentiment_at"])  # parseable ISO-8601

    ctx_off = SimpleNamespace(state={})
    await _find_tool(off, "classify_ticket_tool")(ctx_off, **args)
    assert "sentiment" not in ctx_off.state
    assert "sentiment_at" not in ctx_off.state


@pytest.mark.asyncio
async def test_sentiment_none_never_survives_into_the_tone_lookup() -> None:
    """Nothing classified yet, flag on: "neutral", never None -- and neutral's
    default body is today's wording, so the instruction is unchanged."""
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment=None)

    await svc.handle_turn(session_id="s-none", text="what are your service hours?")

    assert svc._tone_sentiment(_Ctx("s-none", {}, None)) == "neutral"
    assert holder["runner"].instructions == [AGENT_INSTRUCTION]


# --------------------------------------------------------------------------
# Job 2 -- media
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_with_an_image_reaches_the_model_with_the_diagnosis_instruction() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_settings(media_diagnosis_prompt_enabled=True), holder)

    await svc.handle_turn(
        session_id="s-img",
        text="what is wrong with my car",
        image_base64=_IMAGE_B64,
        image_mime_type="image/jpeg",
    )

    served = holder["runner"].instructions[-1]
    assert DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION in served
    assert served.startswith(AGENT_INSTRUCTION)


@pytest.mark.asyncio
async def test_a_turn_with_a_video_reaches_the_model_with_the_diagnosis_instruction() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_settings(media_diagnosis_prompt_enabled=True), holder)

    await svc.handle_turn(
        session_id="s-vid",
        text="listen to this noise",
        video_base64=_IMAGE_B64,
        video_mime_type="video/mp4",
    )

    assert DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION in holder["runner"].instructions[-1]


@pytest.mark.asyncio
async def test_a_turn_without_media_does_not_get_the_diagnosis_instruction() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_settings(media_diagnosis_prompt_enabled=True), holder)

    await svc.handle_turn(session_id="s-nomedia", text="what are your service hours?")

    assert holder["runner"].instructions == [AGENT_INSTRUCTION]


@pytest.mark.asyncio
async def test_an_audio_only_turn_does_not_get_the_diagnosis_instruction() -> None:
    """A voice note is not a photo of a fault -- there is nothing to look at,
    so asking the model to describe what it observes would be nonsense."""
    holder: dict[str, Any] = {}
    svc = _svc(_settings(media_diagnosis_prompt_enabled=True), holder)

    await svc.handle_turn(
        session_id="s-audio",
        text="",
        audio_base64=_IMAGE_B64,
        audio_mime_type="audio/wav",
    )

    assert holder["runner"].instructions == [AGENT_INSTRUCTION]


@pytest.mark.asyncio
async def test_a_media_turn_does_not_leak_the_instruction_into_the_next_turn() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_settings(media_diagnosis_prompt_enabled=True), holder)

    await svc.handle_turn(
        session_id="s-leak",
        text="look at this",
        image_base64=_IMAGE_B64,
        image_mime_type="image/jpeg",
    )
    assert DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION in holder["runner"].instructions[-1]

    await svc.handle_turn(session_id="s-leak", text="and what does it cost?")
    assert holder["runner"].instructions == [AGENT_INSTRUCTION]


@pytest.mark.asyncio
async def test_an_operator_media_instruction_override_reaches_the_model() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_settings(media_diagnosis_prompt_enabled=True), holder)
    _stub_assistant(svc, _assistant(media_diagnosis_instruction="## Lihat\nTerangkan kerosakan."))

    await svc.handle_turn(
        session_id="s-media-override",
        text="tengok ni",
        inbox_id=7,
        image_base64=_IMAGE_B64,
        image_mime_type="image/jpeg",
    )

    served = holder["runner"].instructions[-1]
    assert "## Lihat\nTerangkan kerosakan." in served
    assert DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION not in served


@pytest.mark.asyncio
async def test_media_and_tone_compose_together_on_one_turn() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(
        _settings(
            sentiment_classifier_enabled=True,
            sentiment_tone_adjustment_enabled=True,
            media_diagnosis_prompt_enabled=True,
        ),
        holder,
        tool_sentiment="urgent",
    )

    await svc.handle_turn(
        session_id="s-both",
        text="smoke coming from the bonnet",
        image_base64=_IMAGE_B64,
        image_mime_type="image/jpeg",
    )

    served = holder["runner"].instructions[-1]
    assert DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION in served
    assert "Acknowledge the urgency in the first sentence" in served
    assert served.startswith(AGENT_INSTRUCTION)


# --------------------------------------------------------------------------
# Invariants: flags off, one call per turn, fail-open
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_flags_off_serve_the_instruction_that_shipped_before_p7() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(
        _settings(
            sentiment_classifier_enabled=False,
            sentiment_tone_adjustment_enabled=False,
            media_diagnosis_prompt_enabled=False,
        ),
        holder,
        tool_sentiment="negative",
    )

    await svc.handle_turn(
        session_id="s-off",
        text="This is terrible service",
        image_base64=_IMAGE_B64,
        image_mime_type="image/jpeg",
    )

    # An angry turn WITH an image and a tool call: every ingredient both
    # features need, and still byte-identical to the pre-P7 instruction.
    assert set(holder["runner"].instructions) == {AGENT_INSTRUCTION}


@pytest.mark.asyncio
async def test_all_flags_off_keep_an_operator_personas_instruction_byte_identical() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_settings(), holder, tool_sentiment="negative")
    assistant = _assistant(instructions="Be brief.", language="Bahasa Melayu")
    _stub_assistant(svc, assistant)

    await svc.handle_turn(
        session_id="s-off-persona",
        text="This is terrible service",
        inbox_id=7,
        image_base64=_IMAGE_B64,
        image_mime_type="image/jpeg",
    )

    expected = compose_chat_agent_instruction(AGENT_INSTRUCTION, assistant)
    assert set(holder["runner"].instructions) == {expected}


@pytest.mark.asyncio
async def test_exactly_one_gemini_call_is_made_per_turn_with_both_features_on() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(
        _settings(
            sentiment_classifier_enabled=True,
            sentiment_tone_adjustment_enabled=True,
            media_diagnosis_prompt_enabled=True,
        ),
        holder,
        tool_sentiment="negative",
    )

    await svc.handle_turn(
        session_id="s-one-call",
        text="This is terrible service",
        image_base64=_IMAGE_B64,
        image_mime_type="image/jpeg",
    )

    # Re-composing an instruction is free; a second agent run is not. If this
    # ever reads 2, someone added a round-trip to classify before generating.
    assert holder["calls"] == 1


@pytest.mark.asyncio
async def test_a_broken_service_state_degrades_to_the_registered_instruction() -> None:
    """Fail-open: any error composing the per-turn instruction must serve
    today's wording, never an exception and never an empty tone block."""
    svc = OrchestratorService.__new__(OrchestratorService)
    svc._instruction_by_session = {"crm-1": "PERSONA-INSTRUCTION"}
    # No _settings / _assistant_by_session at all -- the harshest version of a
    # half-initialised service.
    assert svc._chat_instruction_provider(_Ctx("crm-1", {}, None)) == "PERSONA-INSTRUCTION"
    assert svc._chat_instruction_provider(_Ctx("crm-2", {}, None)) == AGENT_INSTRUCTION


@pytest.mark.asyncio
async def test_a_tenant_store_outage_still_serves_a_non_empty_tone_block() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_tone_on(), holder, tool_sentiment="negative")

    async def _boom(_inbox_id: int | None) -> Any:
        raise RuntimeError("firestore down")

    svc._resolve_chat_assistant = _boom  # type: ignore[method-assign]

    result = await svc.handle_turn(session_id="s-outage", text="terrible", inbox_id=7)

    assert result.reply == "ok"  # the customer's turn survived
    served = holder["runner"].instructions[-1]
    assert served.startswith(AGENT_INSTRUCTION)
    # The built-in default wording, not an empty "## Tone" section.
    assert _APOLOGETIC in served


@pytest.mark.asyncio
async def test_a_turn_whose_user_content_is_unreadable_still_serves_an_instruction() -> None:
    holder: dict[str, Any] = {}
    svc = _svc(_settings(media_diagnosis_prompt_enabled=True), holder)
    await svc.handle_turn(session_id="s-weird", text="hi")
    state = svc._adk_sessions.sessions["chatbot"]["s-weird"]["s-weird"].state  # type: ignore[attr-defined]

    # A ctx with no user_content at all (some ADK paths leave it unset).
    assert svc._chat_instruction_provider(_Ctx("s-weird", state, None)) == AGENT_INSTRUCTION
    # ...and one whose parts are not Parts.
    broken = SimpleNamespace(parts=[object()])
    assert svc._chat_instruction_provider(_Ctx("s-weird", state, broken)) == AGENT_INSTRUCTION


def test_the_media_signal_reads_the_mime_types_actually_sent_to_gemini() -> None:
    """The signal is derived from the same `types.Content` handle_turn builds,
    so it cannot drift from what the model was really given."""
    content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text="look"),
            types.Part.from_bytes(data=b"x", mime_type="image/png"),
            types.Part.from_bytes(data=b"y", mime_type="audio/wav"),
        ],
    )
    assert _media_kinds_in_user_content(SimpleNamespace(user_content=content)) == (True, False)

    video = types.Content(
        role="user",
        parts=[types.Part.from_bytes(data=b"z", mime_type="video/mp4")],
    )
    assert _media_kinds_in_user_content(SimpleNamespace(user_content=video)) == (False, True)
