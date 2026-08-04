"""Inline media must reach Gemini on the turn that carried it and never again.

ADK appends the user ``Content`` — inline blob included — to the session before
the agent runs, and rebuilds every later request from those session events. On
the long-lived ``crm-{conversation_id}`` sessions this feature uses, that means
a single video would ride along on every subsequent turn: with
``SESSION_STORE=firestore`` the whole session is rewritten as one document per
appended event, so a base64-inflated video blows the 1 MiB document limit and
the feature stops working outright; with the memory store the blob is pinned
for the process lifetime inside a 768 MB container.

These tests drive the REAL ADK ``Runner`` (via ``_default_runner_factory``)
against a recording fake model, so they assert on the actual ``LlmRequest``
contents rather than on an injected stub.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types
from pydantic import Field

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
    # get_settings() is lru_cached; pin session_store back to the "memory"
    # default in case an earlier test in the run mutated the shared Settings.
    get_settings().session_store = "memory"


class _RecordingLlm(BaseLlm):
    """A BaseLlm that records every LlmRequest and replies with fixed text."""

    requests: list[Any] = Field(default_factory=list)

    async def generate_content_async(
        self,
        llm_request: Any,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text="ok")])
        )


def _inline_blobs(llm_request: Any) -> list[bytes]:
    """Every inline_data payload carried by a recorded request."""
    blobs: list[bytes] = []
    for content in llm_request.contents or []:
        for part in content.parts or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and inline.data:
                blobs.append(inline.data)
    return blobs


@pytest.fixture
def recording_llm() -> _RecordingLlm:
    return _RecordingLlm(model="fake-model")


@pytest.fixture
def service(recording_llm: _RecordingLlm) -> OrchestratorService:
    """A real OrchestratorService whose support agent is a recording fake model.

    The runner factory is deliberately left at the default so the real ADK
    Runner (and whatever session wiring it is given) is exercised.
    """
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
    )
    svc._support_agent = LlmAgent(
        name="recording_agent", model=recording_llm, instruction="You are a test agent."
    )
    return svc


@pytest.mark.parametrize(
    ("kind", "payload", "mime_type"),
    [
        ("video", b"MP4BYTES", "video/mp4"),
        ("image", b"JPEGBYTES", "image/jpeg"),
        ("audio", b"OGGBYTES", "audio/ogg"),
    ],
)
async def test_media_is_not_replayed_on_the_next_turn(
    service: OrchestratorService,
    recording_llm: _RecordingLlm,
    kind: str,
    payload: bytes,
    mime_type: str,
) -> None:
    session_id = f"crm-{kind}"
    media_kwargs: dict[str, Any] = {
        f"{kind}_base64": base64.b64encode(payload).decode(),
        f"{kind}_mime_type": mime_type,
    }
    await service.handle_turn(
        session_id=session_id,
        text="what is wrong with my car",
        **media_kwargs,
    )
    # Turn 1 must actually carry the media to the model.
    assert payload in _inline_blobs(recording_llm.requests[0])

    await service.handle_turn(session_id=session_id, text="any update?")

    # Turn 2 is plain text: nothing from turn 1's media may ride along.
    assert _inline_blobs(recording_llm.requests[-1]) == []
    # ...and the turn-1 text is still there, so history itself is intact.
    turn_two_text = " ".join(
        part.text or ""
        for content in recording_llm.requests[-1].contents or []
        for part in content.parts or []
    )
    assert "what is wrong with my car" in turn_two_text


def echo_tool() -> str:
    """A no-op tool used to force a second model call in one invocation."""
    return "done"


class _ToolThenTextLlm(BaseLlm):
    """Calls a tool on the first model turn, answers on the second — so one
    invocation issues two LlmRequests, both rebuilt from the session events."""

    requests: list[Any] = Field(default_factory=list)

    async def generate_content_async(
        self,
        llm_request: Any,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        if len(self.requests) == 1:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(id="call-1", name="echo_tool", args={})
                        )
                    ],
                )
            )
        else:
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text="ok")])
            )


async def test_media_survives_every_model_call_within_the_same_turn() -> None:
    """Stripping is about LATER turns. Inside the turn that carried the media,
    a tool round-trip means a second model call — the media must still be on
    it, or a tool-using agent would lose the video halfway through answering."""
    llm = _ToolThenTextLlm(model="fake-model")
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
    )
    svc._support_agent = LlmAgent(
        name="tool_agent", model=llm, instruction="You are a test agent.", tools=[echo_tool]
    )

    await svc.handle_turn(
        session_id="crm-tools",
        text="look at this",
        video_base64=base64.b64encode(b"MP4BYTES").decode(),
        video_mime_type="video/mp4",
    )

    assert len(llm.requests) == 2, "expected a tool round-trip (two model calls)"
    for request in llm.requests:
        assert b"MP4BYTES" in _inline_blobs(request)


class _WholeSessionRewriteStore(InMemorySessionService):
    """Stands in for FirestoreSessionService, which serializes the ENTIRE
    session on every appended event. Records each serialized snapshot so a test
    can assert no snapshot ever carried a blob — the failure mode there is a
    hard ``InvalidArgument`` once the document passes 1 MiB."""

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.snapshots: list[str] = []

    async def append_event(self, session: Session, event: Event) -> Event:
        result = await super().append_event(session=session, event=event)
        self.snapshots.append(str(session.model_dump(mode="json")))
        return result


async def test_whole_session_rewrites_never_serialize_a_blob(
    service: OrchestratorService,
) -> None:
    """A single turn appends more than one event (user message, then the model
    response). The second append must not re-serialize the first event's blob."""
    store = _WholeSessionRewriteStore()
    service._adk_sessions = store

    await service.handle_turn(
        session_id="crm-rewrite",
        text="look at this",
        video_base64=base64.b64encode(b"MP4BYTES").decode(),
        video_mime_type="video/mp4",
    )

    assert len(store.snapshots) >= 2, "expected at least a user and a model event append"
    encoded = base64.b64encode(b"MP4BYTES").decode()
    for snapshot in store.snapshots:
        assert encoded not in snapshot
        assert "MP4BYTES" not in snapshot


async def test_media_is_not_retained_in_the_persisted_session(
    service: OrchestratorService,
) -> None:
    """The stored session must hold no inline blob once the turn is over —
    that is what keeps a Firestore session document under 1 MiB and stops the
    in-memory store from pinning every clip for the process lifetime."""
    await service.handle_turn(
        session_id="crm-persist",
        text="look at this",
        video_base64=base64.b64encode(b"MP4BYTES").decode(),
        video_mime_type="video/mp4",
    )
    session = await service._adk_sessions.get_session(
        app_name="chatbot", user_id="crm-persist", session_id="crm-persist"
    )
    assert session is not None
    stored = [
        part
        for event in session.events
        for part in (event.content.parts or [] if event.content else [])
        if getattr(part, "inline_data", None) is not None
    ]
    assert stored == []


async def test_voice_turn_keeps_the_transcription_in_history(
    service: OrchestratorService,
    recording_llm: _RecordingLlm,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A voice turn carries only audio, and that audio is stripped from the
    persisted session. Without the transcription riding along as text, every
    later turn would see nothing but a placeholder and the conversation would
    forget what the caller actually said."""

    async def _fake_transcribe(**_kwargs: Any) -> str:
        return "my car will not start"

    monkeypatch.setattr(service, "_transcribe_audio", _fake_transcribe)

    session_id = "phone-voice-history"
    await service.handle_voice_turn(session_id=session_id, audio_bytes=b"OGGBYTES")

    # Turn 1 must still send the real audio to the model — the transcription
    # supplements the audio, it does not replace it.
    assert b"OGGBYTES" in _inline_blobs(recording_llm.requests[0])

    await service.handle_turn(session_id=session_id, text="any update?")

    turn_two_text = " ".join(
        part.text or ""
        for content in recording_llm.requests[-1].contents or []
        for part in content.parts or []
    )
    assert "my car will not start" in turn_two_text
    assert _inline_blobs(recording_llm.requests[-1]) == []
