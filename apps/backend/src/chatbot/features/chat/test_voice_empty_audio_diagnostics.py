"""Diagnostics for the empty-audio voice reply path.

These reproduce the three backend conditions that make ``/voice/turn`` return an
empty MP3 body — which the frontend renders as
"(no audio reply — AI may be paused or the model returned nothing)" — and assert
the matching diagnostic log event fires so a real occurrence is unambiguous.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import pytest
from structlog.testing import capture_logs

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.ports import TextToSpeechPort
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    get_settings().session_store = "memory"


# --- Fake ADK runner replaying a single final event with a chosen reply text ---


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


class _FakeRunner:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        yield _FakeEvent(self._reply)


class _FunctionCallPart:
    """A part carrying a function call and NO text (text in parts[0] is None)."""

    text = None
    function_call = object()


class _MultiPartContent:
    def __init__(self, parts: list[Any]) -> None:
        self.parts = parts


class _MultiPartEvent:
    def __init__(self, parts: list[Any]) -> None:
        self.content = _MultiPartContent(parts)

    def is_final_response(self) -> bool:
        return True


class _MultiPartRunner:
    """Final response whose text lives AFTER a function-call part, not in parts[0]."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def run_async(self, **_: Any) -> AsyncIterator[_MultiPartEvent]:
        yield _MultiPartEvent([_FunctionCallPart(), _FakePart(self._reply)])


class _EmptyBytesVoiceAdapter(TextToSpeechPort):
    """TTS that succeeds but returns zero bytes (the silent edge of case 3)."""

    async def synthesize(self, text: str, language_code: str = "en-US") -> bytes:
        return b""


class _RaisingVoiceAdapter(TextToSpeechPort):
    """TTS that raises (case 3 — synthesis failure)."""

    async def synthesize(self, text: str, language_code: str = "en-US") -> bytes:
        raise RuntimeError("simulated Gemini TTS outage")


def _build_service(reply_text: str, tts_port: TextToSpeechPort) -> OrchestratorService:
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=tts_port,
        runner_factory=lambda _agent: _FakeRunner(reply=reply_text),
    )
    # Skip the real Gemini transcription call; return a fixed transcription.
    svc._transcribe_audio = _stub_transcribe  # type: ignore[method-assign,assignment]
    return svc


def _build_service_with_runner(runner: Any, tts_port: TextToSpeechPort) -> OrchestratorService:
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=tts_port,
        runner_factory=lambda _agent: runner,
    )
    svc._transcribe_audio = _stub_transcribe  # type: ignore[method-assign,assignment]
    return svc


async def _stub_transcribe(**_: Any) -> str:
    return "I have a question about my order."


def _events(captured: list[MutableMapping[str, Any]]) -> set[str]:
    return {entry["event"] for entry in captured}


@pytest.mark.asyncio
async def test_empty_reply_text_logs_diagnostic() -> None:
    """Case 2: model returns no text -> TTS skipped, empty audio, warning logged."""
    svc = _build_service(reply_text="", tts_port=MockVoiceAdapter())

    with capture_logs() as captured:
        audio_reply, result = await svc.handle_voice_turn(
            session_id="diag-empty-reply",
            audio_bytes=b"fake wav",
            audio_mime_type="audio/wav",
        )

    assert audio_reply == b""
    assert not result.reply
    assert result.forwarded_to_agent is False
    assert "voice_turn_empty_reply_text" in _events(captured)


@pytest.mark.asyncio
async def test_tts_returns_empty_bytes_logs_diagnostic() -> None:
    """Case 3 (silent edge): TTS succeeds but returns no bytes -> warning logged."""
    svc = _build_service(
        reply_text="Sure, here is your answer.", tts_port=_EmptyBytesVoiceAdapter()
    )

    with capture_logs() as captured:
        audio_reply, result = await svc.handle_voice_turn(
            session_id="diag-empty-bytes",
            audio_bytes=b"fake wav",
            audio_mime_type="audio/wav",
        )

    assert audio_reply == b""
    assert result.reply == "Sure, here is your answer."
    assert "voice_tts_returned_empty_audio" in _events(captured)


@pytest.mark.asyncio
async def test_tts_raises_logs_existing_diagnostic() -> None:
    """Case 3: TTS raises -> swallowed, empty audio, existing error logged."""
    svc = _build_service(reply_text="Sure, here is your answer.", tts_port=_RaisingVoiceAdapter())

    with capture_logs() as captured:
        audio_reply, result = await svc.handle_voice_turn(
            session_id="diag-tts-raise",
            audio_bytes=b"fake wav",
            audio_mime_type="audio/wav",
        )

    assert audio_reply == b""
    assert result.reply == "Sure, here is your answer."
    assert "voice_tts_synthesis_failed" in _events(captured)


@pytest.mark.asyncio
async def test_reply_text_recovered_from_non_first_part() -> None:
    """Regression: text after a function-call part must still be spoken.

    Observed on voice-579 — a real transcription but no assistant reply, because
    the loop read only parts[0].text. Scanning all parts recovers the reply so
    audio is synthesized instead of the empty-audio message.
    """
    runner = _MultiPartRunner(reply="Proton X50 is an SUV.")
    svc = _build_service_with_runner(runner, tts_port=MockVoiceAdapter())

    with capture_logs() as captured:
        audio_reply, result = await svc.handle_voice_turn(
            session_id="diag-multipart",
            audio_bytes=b"fake wav",
            audio_mime_type="audio/wav",
        )

    assert result.reply == "Proton X50 is an SUV."
    assert audio_reply != b""
    assert "voice_turn_empty_reply_text" not in _events(captured)
