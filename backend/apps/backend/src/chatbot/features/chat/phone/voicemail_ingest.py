"""P11 Task 4 -- Voicemail ingestion processor.

Deduplicates Twilio `RecordingUrl` webhook payloads by `RecordingSid` and builds
the conversation record a voicemail should become.

**It does not yet create anything.** Stated up front because the previous version
of this docstring claimed it did, and because nothing calls this function: there is
no webhook route, no Chatwoot client, and no contact lookup here. Specifically, and
tracked in `docs/analysis/2026-08-09-blocked-work-register.md`:

- The returned `conversation` dict is **not** posted to Chatwoot. No conversation
  is created, no audio is attached, no contact is matched or created.
- `attend_after` is `now + 12 hours`, **not** P1's `next_working_instant`. A
  voicemail left on a Friday evening therefore promises a Saturday-morning
  callback, which is the opposite of what the after-hours message commits to. This
  is the single field the plan called out as making that promise true, so treat
  the current value as a placeholder rather than a policy.
- `from_number` falls back to a hardcoded `+60120000000` when the payload has no
  `From`. That is a fixture value, not a real caller, and it must not survive into
  anything that writes a contact.

The dedupe set is in-process, so it does not survive a restart or span replicas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Processed recording SIDs for deduplication
_PROCESSED_RECORDINGS: set[str] = set()


def reset_processed_voicemails() -> None:
    _PROCESSED_RECORDINGS.clear()


async def process_voicemail_webhook(
    payload: dict[str, Any], settings: Settings, transcriber_func: Any = None
) -> dict[str, Any]:
    """Process incoming Twilio voicemail webhook idempotently."""
    if not settings.phone_voicemail_ingest_enabled:
        return {"status": "skipped", "reason": "phone_voicemail_ingest_disabled"}

    recording_sid = payload.get("RecordingSid")
    recording_url = payload.get("RecordingUrl")
    from_number = payload.get("From", "+60120000000")

    if not recording_sid or not recording_url:
        return {"status": "ignored", "reason": "missing_recording_data"}

    # Deduplication check
    if recording_sid in _PROCESSED_RECORDINGS:
        _log.info("voicemail_duplicate_ignored", recording_sid=recording_sid)
        return {"status": "duplicate", "recording_sid": recording_sid}

    _PROCESSED_RECORDINGS.add(recording_sid)

    # Attempt transcription (fail open -- audio creation must proceed even if transcript fails)
    transcript_text = ""
    if transcriber_func is not None:
        try:
            transcript_text = await transcriber_func(recording_url)
        except Exception as exc:
            _log.warning("voicemail_transcription_failed", recording_sid=recording_sid, error=str(exc))
            transcript_text = "[Transcription unavailable]"
    else:
        transcript_text = "Voicemail recording attached."

    # Calculate next working instant
    now = datetime.now(UTC)
    attend_after = (now + timedelta(hours=12)).isoformat()

    conversation = {
        "id": f"conv_vm_{recording_sid}",
        "inbox_id": "phone_inbox",
        "contact_phone": from_number,
        "audio_url": recording_url,
        "transcript": transcript_text,
        "attend_after": attend_after,
        "created_at": now.isoformat(),
    }

    _log.info("voicemail_ingested", conversation_id=conversation["id"], from_number=from_number)
    return {"status": "created", "conversation": conversation}
