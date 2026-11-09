from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from google.cloud import texttospeech
from starlette.concurrency import run_in_threadpool

from chatbot.features.chat.ports import TextToSpeechPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class GeminiTextToSpeechAdapter(TextToSpeechPort):
    """Text-to-Speech adapter using Gemini TTS via the Cloud Text-to-Speech API.

    Uses the new `model_name` field on VoiceSelectionParams (e.g. ``gemini-2.5-flash-tts``)
    introduced for Gemini-backed synthesis. ADC handles authentication.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = texttospeech.TextToSpeechClient()
        self._model = settings.gemini_tts_model
        self._voice_name = settings.gemini_tts_voice

    async def synthesize(self, text: str, language_code: str = "en-US") -> bytes:
        _log.info(
            "synthesizing_text_via_gemini_tts",
            text_preview=text[:100],
            lang=language_code,
            model=self._model,
            voice=self._voice_name,
        )

        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=self._voice_name,
            model_name=self._model,
        )
        audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

        try:
            response = await run_in_threadpool(
                self._client.synthesize_speech,
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )
            _log.info("gemini_tts_synthesis_completed", size_bytes=len(response.audio_content))
            audio_bytes: bytes = response.audio_content
            return audio_bytes
        except Exception as e:
            _log.error("gemini_tts_failed", error=str(e))
            raise

    async def synthesize_mulaw8k(self, text: str, language_code: str = "en-US") -> bytes:
        """Synthesise telephony audio: mu-law 8 kHz mono, as Twilio Media
        Streams plays it. Used once per process for the phone recording
        notice (phone_fast_start_enabled); asks for LINEAR16 (a WAV) and
        converts locally, so it does not depend on which encodings a given
        TTS model offers."""
        from chatbot.features.chat.phone.audio_codec import wav_to_mulaw8k  # noqa: PLC0415

        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code, name=self._voice_name, model_name=self._model
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16, sample_rate_hertz=8000
        )
        response = await run_in_threadpool(
            self._client.synthesize_speech,
            input=texttospeech.SynthesisInput(text=text),
            voice=voice,
            audio_config=audio_config,
        )
        return wav_to_mulaw8k(response.audio_content)
