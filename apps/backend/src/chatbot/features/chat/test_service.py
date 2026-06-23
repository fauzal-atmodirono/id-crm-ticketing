from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.sessions.session import Event

from chatbot.features.chat.adapters.firestore_session_service import FirestoreSessionService
from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    get_settings().session_store = "memory"

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

    # Mock transcription call
    mock_response = MagicMock()
    mock_response.text = "Hello, I need help with my car."
    svc._genai_client = MagicMock()
    svc._genai_client.aio = MagicMock()
    svc._genai_client.aio.models = MagicMock()
    svc._genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    # Ingest mock audio bytes
    audio_reply, result = await svc.handle_voice_turn(session_id="v1", audio_bytes=b"mock_audio")

    # Verify text outputs
    assert result.reply == reply_text
    assert result.user_transcription == "Hello, I need help with my car."
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


@pytest.mark.asyncio
async def test_firestore_session_service() -> None:
    mock_settings = MagicMock()
    mock_settings.firestore_project_id = "test-project"
    mock_settings.firestore_database_id = "test-db"

    # We store the documents mock dict
    stored_docs = {}

    class MockDocumentRef:
        def __init__(self, doc_id: str) -> None:
            self.id = doc_id

        def set(self, data: dict[str, Any]) -> None:
            stored_docs[self.id] = data

        def get(self) -> MagicMock:
            mock_snap = MagicMock()
            if self.id in stored_docs:
                mock_snap.exists = True
                mock_snap.to_dict.return_value = stored_docs[self.id]
            else:
                mock_snap.exists = False
            return mock_snap

        def delete(self) -> None:
            stored_docs.pop(self.id, None)

    class MockCollectionRef:
        def __init__(self, name: str) -> None:
            self.name = name

        def document(self, doc_id: str) -> MockDocumentRef:
            return MockDocumentRef(doc_id)

        def where(self, *args: Any, **kwargs: Any) -> MockCollectionRef:
            return self

        def stream(self) -> list[MagicMock]:
            mock_docs = []
            for doc_id, data in stored_docs.items():
                mock_doc = MagicMock()
                mock_doc.id = doc_id
                mock_doc.to_dict.return_value = data
                mock_docs.append(mock_doc)
            return mock_docs

    mock_client = MagicMock()
    mock_client.collection.side_effect = MockCollectionRef

    with patch("google.cloud.firestore.Client", return_value=mock_client):
        service = FirestoreSessionService(mock_settings)

        # 1. Test create_session
        session = await service.create_session(
            app_name="test-app",
            user_id="user-123",
            session_id="session-123",
            state={"foo": "bar"},
        )
        assert session.id == "session-123"
        assert session.state == {"foo": "bar"}
        assert "session-123" in stored_docs

        # 2. Test get_session
        loaded = await service.get_session(
            app_name="test-app",
            user_id="user-123",
            session_id="session-123",
        )
        assert loaded is not None
        assert loaded.id == "session-123"
        assert loaded.state == {"foo": "bar"}

        # 3. Test append_event
        event = Event(id="event-1", invocation_id="inv-1", author="model")
        await service.append_event(session, event)
        assert len(session.events) == 1
        assert session.events[0].id == "event-1"

        # Check updated firestore document has the event
        updated_data = stored_docs["session-123"]
        assert len(updated_data["events"]) == 1
        assert updated_data["events"][0]["id"] == "event-1"

        # 4. Test list_sessions
        resp = await service.list_sessions(app_name="test-app", user_id="user-123")
        assert len(resp.sessions) == 1
        assert resp.sessions[0].id == "session-123"

        # 5. Test delete_session
        await service.delete_session(
            app_name="test-app",
            user_id="user-123",
            session_id="session-123",
        )
        assert "session-123" not in stored_docs

        # 6. Test get_user_state
        user_state = await service.get_user_state(app_name="test-app", user_id="user-123")
        assert user_state == {}
