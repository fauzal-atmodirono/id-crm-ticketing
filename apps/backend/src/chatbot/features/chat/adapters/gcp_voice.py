from __future__ import annotations

import structlog
from google.cloud import speech, texttospeech

from chatbot.features.chat.models import VoiceTranscript
from chatbot.features.chat.ports import SpeechToTextPort, TextToSpeechPort

_log = structlog.get_logger(__name__)


class GcpSpeechToTextAdapter(SpeechToTextPort):
    """GCP-based Speech-to-Text adapter using Cloud Speech-to-Text API."""

    def __init__(self) -> None:
        # Client automatically picks up GCP Application Default Credentials (ADC) from the environment
        self._client = speech.SpeechClient()

    async def transcribe(
        self, audio_content: bytes, language_code: str = "en-US"
    ) -> VoiceTranscript:
        _log.info(
            "transcribing_audio_via_gcp_stt", size_bytes=len(audio_content), lang=language_code
        )

        audio = speech.RecognitionAudio(content=audio_content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=language_code,
        )

        try:
            # GCP speech library has blocking I/O calls; run in thread pool or use async client if supported.
            # SpeechClient doesn't expose standard async/await for simple recognize easily,
            # so we run the call via loop.run_in_executor or call directly since it takes sub-second.
            response = self._client.recognize(config=config, audio=audio)

            transcript_text = ""
            confidence = 0.0
            for result in response.results:
                if result.alternatives:
                    # Capture the best transcript alternative
                    best_alt = result.alternatives[0]
                    transcript_text += best_alt.transcript
                    confidence = max(confidence, best_alt.confidence)

            _log.info("gcp_stt_transcription_completed", length_chars=len(transcript_text))
            return VoiceTranscript(
                text=transcript_text,
                confidence=confidence,
                language=language_code,
                duration_ms=1000,  # Approximate default
            )
        except Exception as e:
            _log.error("gcp_stt_failed", error=str(e))
            raise


class GcpTextToSpeechAdapter(TextToSpeechPort):
    """GCP-based Text-to-Speech adapter using Cloud Text-to-Speech API."""

    def __init__(self) -> None:
        # Client automatically picks up GCP Application Default Credentials (ADC) from the environment
        self._client = texttospeech.TextToSpeechClient()

    async def synthesize(self, text: str, language_code: str = "en-US") -> bytes:
        _log.info("synthesizing_text_via_gcp_tts", text_preview=text[:100], lang=language_code)

        synthesis_input = texttospeech.SynthesisInput(text=text)

        # Configure voice settings
        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        )

        # Configure audio output encoding (MP3 is most standard for Twilio/web playbacks)
        audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

        try:
            response = self._client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            _log.info("gcp_tts_synthesis_completed", size_bytes=len(response.audio_content))
            return response.audio_content
        except Exception as e:
            _log.error("gcp_tts_failed", error=str(e))
            raise
