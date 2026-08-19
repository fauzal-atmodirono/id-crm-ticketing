"""Post-call transcription of a finished Twilio call recording.

**Why this exists.** The live transcript stops at the handoff. `<Connect>
<Stream>` is what feeds Gemini Live, and `_attempt_transfer` REPLACES it with
`<Dial>` the moment a human is reached -- so the conversation between the human
agent and the customer is never transcribed, only the AI portion is. Recording
captures the whole call (it records the PARENT leg, which survives the bridge),
so transcribing the finished recording is how the human half gets into the CRM
without touching the live call path at all.

**Deliberately post-call, not live.** Nothing here runs while a caller is on the
line: it is invoked from the recording-status webhook, after Twilio has finished
writing the file, as a background task. That is the whole safety argument -- a
slow or failed transcription cannot add latency to a call, cannot break TwiML,
and cannot drop anybody. The cost is that the transcript lands seconds to
minutes after the call ends rather than during it. Conference mode (see the
agent-softphone design doc's "Future" section) is the live alternative, and a
much larger job.

Fail-open throughout: every failure logs and returns None, leaving the recording
URL attached to the conversation exactly as it was. A missing transcript is a
degraded record; a raised exception here would surface as an unretrieved
background-task error and change nothing for the better.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Twilio serves the recording at `<RecordingUrl>.mp3`. MP3 rather than WAV on
# purpose: an 8 kHz call is small either way, but MP3 keeps a long call
# comfortably inside the model's inline-audio limit, and the codec loss is
# irrelevant against telephony audio that is already mu-law 8 kHz.
_RECORDING_FORMAT = ".mp3"

_TRANSCRIBE_PROMPT = (
    "This is a recording of a customer support phone call. It has two channels: "
    "the customer and the support side (which may be an AI assistant for part of "
    "the call and a human agent for the rest). Transcribe the entire call "
    "verbatim in the language actually spoken -- the callers code-switch between "
    "English, Bahasa Melayu and Chinese, so do NOT translate. Label every turn "
    "as CUSTOMER: or AGENT:. If a passage is genuinely unintelligible write "
    "[inaudible] rather than guessing at words."
)


async def fetch_recording(settings: Settings, recording_url: str) -> bytes | None:
    """Download the recording from Twilio. Returns None on any failure.

    Twilio recording URLs require account auth even though they look public,
    which is also why this cannot be handed to the model as a URL.
    """
    if not recording_url:
        return None
    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    if not sid or not token:
        _log.warning("recording_transcribe_no_twilio_credentials")
        return None
    url = (
        recording_url
        if recording_url.endswith(_RECORDING_FORMAT)
        else recording_url + _RECORDING_FORMAT
    )
    try:
        import httpx  # noqa: PLC0415 -- deferred, matches this package's style

        async with httpx.AsyncClient() as client:
            res = await client.get(url, auth=(sid, token), timeout=60.0)
            res.raise_for_status()
            return bytes(res.content)
    except Exception as e:
        _log.error("recording_fetch_failed", error=str(e))
        return None


def _transcribe_sync(settings: Settings, audio: bytes) -> str | None:
    """Blocking Gemini call. Run via asyncio.to_thread by the caller -- the
    google-genai SDK is synchronous and this package never blocks the loop."""
    try:
        from google.genai import types  # noqa: PLC0415

        from chatbot.platform.metered_genai import (  # noqa: PLC0415
            SURFACE_PHONE_TRANSCRIBE,
            build_metered_genai_client,
        )

        # Never `genai.Client()` directly: build_metered_genai_client is the
        # ONLY place in this backend that constructs a Gemini client, so a new
        # call site is metered by construction rather than by remembering.
        # test_metered_genai.py scans the tree and fails on a direct
        # construction -- which is exactly how this call site got caught.
        client = build_metered_genai_client(settings, surface=SURFACE_PHONE_TRANSCRIBE)
        if client is None:
            return None
        res = client.models.generate_content(
            model=settings.recording_transcription_model,
            contents=[
                _TRANSCRIBE_PROMPT,
                types.Part.from_bytes(data=audio, mime_type="audio/mp3"),
            ],
        )
        text = (res.text or "").strip()
        return text or None
    except Exception as e:
        _log.error("recording_transcribe_failed", error=str(e))
        return None


async def transcribe_recording(settings: Settings, recording_url: str) -> str | None:
    """Download and transcribe. None means "no transcript" for any reason --
    callers must treat it as "leave the conversation as it is", never as an
    error worth surfacing to a customer."""
    if not settings.phone_recording_transcription_enabled:
        return None
    audio = await fetch_recording(settings, recording_url)
    if not audio:
        return None
    _log.info("recording_transcribe_started", bytes=len(audio))
    text = await asyncio.to_thread(_transcribe_sync, settings, audio)
    if text:
        _log.info("recording_transcribe_succeeded", chars=len(text))
    return text


async def transcribe_and_attach(
    settings: Settings,
    log_port: Any,
    ticket_id: str,
    recording_url: str,
) -> bool:
    """Transcribe the recording and append it to the conversation as a note.

    Idempotency is the caller's problem, not this function's: Twilio redelivers
    a recording-status callback on any non-2xx, and `append_conversation_comment`
    is an APPEND. The webhook guards on the recording-attached state before
    dispatching this. Returns True only when a note was actually written.
    """
    text = await transcribe_recording(settings, recording_url)
    if not text:
        return False
    try:
        await log_port.append_conversation_comment(
            ticket_id,
            "[Full call transcript — includes the human agent portion, which the "
            "live AI transcript cannot cover]\n\n" + text,
        )
    except Exception as e:
        _log.error("recording_transcript_attach_failed", ticket_id=ticket_id, error=str(e))
        return False
    _log.info("recording_transcript_attached", ticket_id=ticket_id)
    return True
