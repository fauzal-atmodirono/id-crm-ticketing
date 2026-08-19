"""Thin wrapper over the google-genai Live API exposing a testable Protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

import structlog
from google.genai import live, types

from chatbot.features.chat.phone.live_events import LiveEvent, normalize_server_message
from chatbot.platform.metered_genai import SURFACE_PHONE_LIVE, build_metered_genai_client

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class LiveSession(Protocol):
    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None: ...
    async def send_text_hint(self, text: str) -> None: ...
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

    async def send_text_hint(self, text: str) -> None:
        """Send a short text-only input alongside the audio stream -- used
        for IVR-4's per-turn language reminder. Not spoken aloud by the
        caller and does not itself count as a caller turn."""
        await self._session.send_realtime_input(text=text)

    async def events(self) -> AsyncIterator[LiveEvent]:
        # The SDK's receive() yields one complete turn then returns; re-enter it
        # for each subsequent turn so a multi-turn call keeps streaming instead of
        # ending after the first AI reply. The loop is torn down by the caller
        # (phone_stream cancels this task on hangup) or by receive() raising when
        # the live connection closes.
        while True:
            async for msg in self._session.receive():
                for event in normalize_server_message(msg):
                    yield event


def _build_live_config(
    settings: Settings,
    system_instruction: str,
    tools: list[types.Tool],
) -> types.LiveConnectConfig:
    """Build the LiveConnectConfig. Sets an output language_code only when
    configured (e.g. ms-MY for a Bahasa Melayu demo); left unset, the model
    auto-detects language. Extracted so it is unit-testable without a live SDK
    connection."""
    speech_config = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.gemini_live_voice)
        ),
    )
    if settings.gemini_live_language:
        speech_config.language_code = settings.gemini_live_language
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system_instruction,
        tools=tools,
        speech_config=speech_config,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    if settings.gemini_live_vad_enabled:
        config.realtime_input_config = _build_vad_config(settings)
    return config


def _build_vad_config(settings: Settings) -> types.RealtimeInputConfig:
    """Barge-in tuning for telephony.

    Left unset, the Live API applies its own defaults, which assume a clean
    microphone. This bridge only ever carries mu-law 8 kHz phone audio, where
    line noise and room sound sit much closer to the speech threshold -- on
    proton that meant "a little sound will be breaking the AI agent": the
    assistant was cut off mid-sentence by noise that was never a caller trying
    to speak.

    LOW start sensitivity raises the bar for what counts as the caller starting
    to talk. LOW end sensitivity plus a longer silence window stops the model
    treating a mid-sentence pause as the caller's turn ending, which is what
    made it talk over people who were still thinking.

    Unknown sensitivity strings fall back to LOW rather than raising: this runs
    while a caller is connecting, and a typo in a tenant env must not fail the
    call. LOW is also the safer wrong answer -- it under-triggers rather than
    interrupting.
    """
    start = (
        types.StartSensitivity.START_SENSITIVITY_HIGH
        if settings.gemini_live_vad_start_sensitivity.upper() == "HIGH"
        else types.StartSensitivity.START_SENSITIVITY_LOW
    )
    end = (
        types.EndSensitivity.END_SENSITIVITY_HIGH
        if settings.gemini_live_vad_end_sensitivity.upper() == "HIGH"
        else types.EndSensitivity.END_SENSITIVITY_LOW
    )
    return types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            start_of_speech_sensitivity=start,
            end_of_speech_sensitivity=end,
            prefix_padding_ms=settings.gemini_live_vad_prefix_padding_ms,
            silence_duration_ms=settings.gemini_live_vad_silence_duration_ms,
        )
    )


@asynccontextmanager
async def connect_live(
    settings: Settings,
    system_instruction: str,
    tools: list[types.Tool],
) -> AsyncIterator[LiveSession]:
    """Open a Gemini Live session configured for telephony audio + KB tools.

    P8: the client comes from ``build_metered_genai_client`` so this call site
    is metered by construction rather than by remembering. The Live API's own
    token accounting arrives in server messages rather than on a response
    object, so nothing is recorded here *yet* -- what routing through the
    wrapper buys is that when Live usage is captured there is exactly one
    place to add it, and that the architectural guard test keeps this file from
    growing a direct ``Client(...)`` again. With ``token_metering_enabled``
    off (the default) the wrapper hands back the raw SDK client unwrapped, so
    the live audio path is byte-identical to pre-P8.
    """
    client = build_metered_genai_client(settings, surface=SURFACE_PHONE_LIVE)
    if client is None:
        # Previously a construction failure raised out of connect_live; keep
        # that contract rather than degrading to a None-typed client that
        # fails later with an opaque AttributeError.
        raise RuntimeError("google-genai client unavailable for the phone Live session")
    config = _build_live_config(settings, system_instruction, tools)
    async with client.aio.live.connect(model=settings.gemini_live_model, config=config) as session:
        _log.info("phone_live_session_connected", model=settings.gemini_live_model)
        yield _GeminiLiveSession(session)
