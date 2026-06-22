from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings

# --- Fake ADK runner classes to replay pre-recorded events ---


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeEvent:
    """Fake ADK Event containing content/parts."""

    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)

    def is_final_response(self) -> bool:
        return True


class _FakeRunner:
    """Fake ADK Runner that replays predefined events and optionally mutates state."""

    def __init__(
        self, reply: str, session_service: Any, session_id: str, trigger_handoff: bool = False
    ) -> None:
        self._reply = reply
        self._session_service = session_service
        self._session_id = session_id
        self._trigger_handoff = trigger_handoff

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        # Mutate the session state just like ADK tools do
        if self._trigger_handoff:
            session = self._session_service.sessions["chatbot"][self._session_id][self._session_id]
            session.state["handoff_triggered"] = True
            session.state["handoff_reason"] = "help_request"

        yield _FakeEvent(self._reply)


@pytest.mark.asyncio
async def test_handle_turn_happy_path() -> None:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    reply_text = "Here is the support answer you requested."

    # Factory that builds a FakeRunner replaying the happy path reply
    def fake_runner_factory(_agent: Any) -> _FakeRunner:
        return _FakeRunner(reply=reply_text, session_service=svc._adk_sessions, session_id="s1")

    svc = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=voice_client,
        runner_factory=fake_runner_factory,
    )

    result = await svc.handle_turn(session_id="s1", text="I need support")

    assert result.reply == reply_text
    assert result.handoff is None
    assert len(svc._history["s1"]) == 2
    assert svc._history["s1"][0].text == "I need support"
    assert svc._history["s1"][1].text == reply_text


@pytest.mark.asyncio
async def test_handle_turn_ai_paused() -> None:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    svc = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=voice_client,
    )

    # Pause the AI for this session
    await ticketing_port.pause_ai_for_session("s2")

    result = await svc.handle_turn(session_id="s2", text="Hello")

    # Verify the AI does not respond when paused (human took over)
    assert result.reply is None
    assert "s2" not in svc._history


@pytest.mark.asyncio
async def test_handle_turn_triggers_escalation() -> None:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    # Replays for the support turn AND the summarizer turn
    summarizer_output = json.dumps(
        {
            "summary": "Customer needs account billing assistance.",
            "urgency": "high",
            "language": "en",
        }
    )

    runner_calls = 0

    def fake_runner_factory(_agent: Any) -> _FakeRunner:
        nonlocal runner_calls
        runner_calls += 1
        if runner_calls == 1:
            # First call is the support agent turn (triggers handoff)
            return _FakeRunner(
                reply="Connecting you now.",
                session_service=svc._adk_sessions,
                session_id="s3",
                trigger_handoff=True,
            )
        # Second call is the summarizer agent
        return _FakeRunner(
            reply=summarizer_output, session_service=svc._adk_sessions, session_id="sum-s3"
        )

    svc = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=voice_client,
        runner_factory=fake_runner_factory,
    )

    result = await svc.handle_turn(session_id="s3", text="Let me speak to a human")

    # Verify handoff results
    assert result.reply is None  # Replied text is cleared on handoff
    assert result.handoff is not None
    assert result.handoff.reason == "help_request"
    assert result.handoff.urgency == "high"
    assert result.handoff.summary is not None
    assert "billing assistance" in result.handoff.summary

    # Verify Ticketing adapter actions
    assert len(ticketing_port.tickets) == 1
    tkt_id = next(iter(ticketing_port.tickets.keys()))
    assert ticketing_port.tickets[tkt_id]["urgency"] == "high"
    assert ticketing_port.notes[tkt_id][0].startswith("⚠️ AI ASSISTANT SUMMARY:")
    assert await ticketing_port.is_ai_paused("s3") is True


@pytest.mark.asyncio
async def test_handle_voice_turn_happy_path() -> None:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    reply_text = "This is a voice reply."

    def fake_runner_factory(_agent: Any) -> _FakeRunner:
        return _FakeRunner(reply=reply_text, session_service=svc._adk_sessions, session_id="v1")

    svc = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=voice_client,
        runner_factory=fake_runner_factory,
    )

    # Ingest mock audio bytes
    audio_reply, result = await svc.handle_voice_turn(session_id="v1", audio_bytes=b"mock_audio")

    # Verify text outputs
    assert result.reply == reply_text
    # Verify voice synthesis output (MockVoiceAdapter echoes code + text)
    assert audio_reply == b"mock_voice_audio:en-US:This is a voice reply."


class _FakeLeadRunner:
    """Fake ADK Runner that sets lead capture and ticket classification states."""

    def __init__(self, reply: str, session_service: Any, session_id: str) -> None:
        self._reply = reply
        self._session_service = session_service
        self._session_id = session_id

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        session = self._session_service.sessions["chatbot"][self._session_id][self._session_id]
        session.state["handoff_triggered"] = True
        session.state["handoff_reason"] = "sales_lead"
        session.state["lead_captured"] = True
        session.state["lead_details"] = {
            "customer_name": "Ahmad Ali",
            "customer_phone": "60123456789",
            "customer_email": "ahmad@example.com",
            "preferred_model": "Proton X50",
            "preferred_dealer": "Proton Edar Glenmarie",
        }
        session.state["category"] = "Sales"
        session.state["subcategory"] = "Test Drive Booking"
        session.state["priority"] = "HIGH"
        session.state["sla_minutes"] = 120

        yield _FakeEvent(self._reply)


@pytest.mark.asyncio
async def test_handle_turn_triggers_escalation_with_lead_and_classification() -> None:
    settings = get_settings()
    chat_port = InMemoryChatAdapter()
    ticketing_port = InMemoryTicketingAdapter()
    knowledge_port = InMemoryKnowledgeAdapter()
    voice_client = MockVoiceAdapter()

    summarizer_output = json.dumps(
        {
            "summary": "Customer wishes to book a test drive for a Proton X50.",
            "urgency": "high",
            "language": "ms",
        }
    )

    runner_calls = 0

    def fake_runner_factory(_agent: Any) -> Any:
        nonlocal runner_calls
        runner_calls += 1
        if runner_calls == 1:
            return _FakeLeadRunner(
                reply="Registering your interest now.",
                session_service=svc._adk_sessions,
                session_id="s4",
            )
        return _FakeRunner(
            reply=summarizer_output, session_service=svc._adk_sessions, session_id="sum-s4"
        )

    svc = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=voice_client,
        runner_factory=fake_runner_factory,
    )

    result = await svc.handle_turn(session_id="s4", text="I want to test drive the X50")

    handoff = result.handoff
    assert handoff is not None
    assert handoff.reason == "sales_lead"
    assert handoff.urgency == "high"
    assert handoff.language == "ms"
    
    lead = handoff.lead_details
    assert lead is not None
    assert lead["customer_name"] == "Ahmad Ali"
    
    cls_details = handoff.classification
    assert cls_details is not None
    assert cls_details["category"] == "Sales"
    assert cls_details["priority"] == "HIGH"

    # Verify Ticketing adapter actions
    assert len(ticketing_port.tickets) == 1
    tkt_id = next(iter(ticketing_port.tickets.keys()))
    tkt = ticketing_port.tickets[tkt_id]
    assert tkt["customer_name"] == "Ahmad Ali"
    assert tkt["customer_email"] == "ahmad@example.com"
    assert tkt["customer_phone"] == "60123456789"
    assert "Ahmad Ali" in tkt["body"]
    assert "Glenmarie" in tkt["body"]
    assert "Category: Sales" in ticketing_port.notes[tkt_id][0]
