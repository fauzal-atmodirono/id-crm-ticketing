"""Thin wrapper over the google-genai Live API exposing a testable Protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

import structlog
from google.genai import Client, live, types

from chatbot.features.chat.phone.live_events import LiveEvent, normalize_server_message

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class LiveSession(Protocol):
    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None: ...
    def events(self) -> AsyncIterator[LiveEvent]: ...


class _GeminiLiveSession:
    def __init__(self, session: live.AsyncSession) -> None:
        self._session = session

    async def send_audio(self, pcm16k: bytes) -> None:
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm16k, mime_type="audio/pcm;rate=16000")
        )

    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None:
        await self._session.send_tool_response(
            function_responses=[types.FunctionResponse(id=call_id, name=name, response=response)]
        )

    async def events(self) -> AsyncIterator[LiveEvent]:
        async for msg in self._session.receive():
            for event in normalize_server_message(msg):
                yield event


@asynccontextmanager
async def connect_live(
    settings: Settings,
    system_instruction: str,
    tools: list[types.Tool],
) -> AsyncIterator[LiveSession]:
    """Open a Gemini Live session configured for telephony audio + KB tools."""
    if settings.google_genai_use_vertexai:
        client = Client(
            vertexai=True,
            project=settings.vertex_project_id,
            location=settings.vertex_location,
        )
    else:
        client = Client()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system_instruction,
        tools=tools,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.gemini_live_voice
                )
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    async with client.aio.live.connect(model=settings.gemini_live_model, config=config) as session:
        yield _GeminiLiveSession(session)
