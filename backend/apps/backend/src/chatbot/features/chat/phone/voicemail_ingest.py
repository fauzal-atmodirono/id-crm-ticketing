"""P11 Task 4 / Task 31 -- Voicemail ingestion.

`process_voicemail_webhook` deduplicates Twilio voicemail payloads by
`RecordingSid` and builds the record a voicemail should become (including
`attend_after`). `ingest_voicemail` is its caller: Task 31's IVR posts each
`<Record>` result to `/voice/phone/ivr/voicemail` (see `ivr_router.py`), and
`ingest_voicemail` writes it to Chatwoot through the existing
`ConversationLogPort` -- find-or-create the call's conversation (session id
`phone-<CallSid>`, the same key the bridge and recording-status use), add a
private note with the recording link, the menu choice and `attend_after`, store
the recording as the conversation's call-recording attributes, and tag it
`voicemail`. Transcription reuses `recording_transcriber.process_recording` and
is the caller's background job.

The `From` number is used only when Twilio sent one: the old fixture fallback
(`+60120000000`) is gone now that this has a caller that writes contacts.

`attend_after` **is** now P1's `next_working_instant` (it was `now + 12 hours`,
which promised a Saturday-morning callback for a Friday-evening voicemail --
the opposite of what the after-hours message commits to, and this is the single
field the plan named as making that promise true). It reads the working-hours
rows the caller passes in; with none, `next_working_instant` returns `now`,
i.e. "attend immediately". That is the deliberate fail-open direction: a
voicemail surfacing sooner than policy is a nuisance, a voicemail stamped for a
day nobody is rostered ages against SLA over a weekend unnoticed.

The dedupe set is in-process, so it does not survive a restart or span
replicas; `ingest_voicemail` backs it with the conversation's `voicemail` tag,
so a redelivery to a different process still adds no second note.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.metrics.business_hours import next_working_instant

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Processed recording SIDs for deduplication
_PROCESSED_RECORDINGS: set[str] = set()

_MASK = "\u2022\u2022\u2022"
_MASK_KEEP_HEAD = 3
_MASK_KEEP_TAIL = 4


def mask_phone(number: str | None) -> str:
    """Caller numbers are personal data and logs are not access-controlled
    like Chatwoot is: keep the country prefix and last four digits only
    (`+60\u2022\u2022\u20221234`), enough to match a complaint to a log line."""
    if not number:
        return "unknown"
    if len(number) <= _MASK_KEEP_HEAD + _MASK_KEEP_TAIL:
        return _MASK
    return f"{number[:_MASK_KEEP_HEAD]}{_MASK}{number[-_MASK_KEEP_TAIL:]}"


def reset_processed_voicemails() -> None:
    _PROCESSED_RECORDINGS.clear()


async def process_voicemail_webhook(
    payload: dict[str, Any],
    settings: Settings,
    transcriber_func: Any = None,
    inbox_working_hours: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process incoming Twilio voicemail webhook idempotently.

    `inbox_working_hours` is the Chatwoot `GET /inboxes/{id}` body (the same row
    shape `features/metrics/business_hours.py` parses everywhere else in this
    codebase) and is what makes `attend_after` mean the next business day. It is
    optional because there is no webhook route yet to fetch it -- see the module
    docstring; omitting it means "attend now", never a later fabricated time.
    """
    if not settings.phone_voicemail_ingest_enabled:
        return {"status": "skipped", "reason": "phone_voicemail_ingest_disabled"}

    recording_sid = payload.get("RecordingSid")
    recording_url = payload.get("RecordingUrl")
    from_number = payload.get("From") or None

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

    # The after-hours message promises "our team will reach out on the next
    # business day", so this field has to be the next instant the team is
    # actually rostered -- not a flat offset. `next_working_instant` is P1's
    # helper and the one the rest of the codebase uses for exactly this; it
    # returns `now` unchanged when no hours are configured, and it fails open
    # the same way rather than walking forever on a pathological calendar.
    now = datetime.now(UTC)
    attend_after = next_working_instant(now, inbox_working_hours or {}).isoformat()

    conversation = {
        "id": f"conv_vm_{recording_sid}",
        "inbox_id": "phone_inbox",
        "contact_phone": from_number,
        "audio_url": recording_url,
        "transcript": transcript_text,
        "attend_after": attend_after,
        "created_at": now.isoformat(),
    }

    _log.info("voicemail_ingested", conversation_id=conversation["id"], from_number=mask_phone(from_number))
    return {"status": "created", "conversation": conversation}


VOICEMAIL_TAG = "voicemail"

_CATEGORY_LABEL = {
    "rsa": "Roadside Assistance",
    "inquiry": "Inquiry",
    "complaint": "Complaint",
    "unselected": "No menu choice",
}
_REASON_LABEL = {"after_hours": "After hours", "no_answer": "No agent answered"}
_LANGUAGE_LABEL = {"en": "English", "ms": "Bahasa Melayu"}


async def ingest_voicemail(
    payload: dict[str, Any],
    settings: Settings,
    log_port: Any,
    *,
    language: str,
    category: str,
    reason: str,
    inbox_working_hours: dict[str, Any] | None = None,
) -> str | None:
    """Turn one IVR voicemail into a Chatwoot conversation. Returns the
    ticket id when a note was written, else None. Never raises: this runs as
    a background task after the caller has already heard goodbye."""
    try:
        result = await process_voicemail_webhook(
            payload, settings, inbox_working_hours=inbox_working_hours
        )
        if result.get("status") != "created":
            _log.info("voicemail_ingest_skipped", **{k: v for k, v in result.items() if k != "conversation"})
            return None
        conv = result["conversation"]
        call_sid = str(payload.get("CallSid") or "").strip()
        session_id = f"phone-{call_sid}" if call_sid else f"phone-vm-{payload['RecordingSid']}"
        from_number = conv["contact_phone"]
        cat = _CATEGORY_LABEL.get(category, "No menu choice")
        why = _REASON_LABEL.get(reason, "Voicemail")
        ticket_id = await log_port.ensure_conversation_ticket(
            session_id,
            f"[Voicemail] {cat} - {from_number or 'unknown caller'}",
            None,
            from_number,
        )
        if await log_port.has_ticket_tag(ticket_id, VOICEMAIL_TAG):
            _log.info("voicemail_already_on_conversation", ticket_id=ticket_id)
            return None
        recording_url = str(payload["RecordingUrl"])
        # Twilio serves the audio at `<RecordingUrl>.mp3` (same as
        # recording_transcriber). The link needs Twilio account auth unless
        # the account has public media URLs on; it is the same URL the
        # recording-status path stores in the call-recording attributes.
        listen_url = recording_url if recording_url.endswith(".mp3") else f"{recording_url}.mp3"
        note = (
            f"[Voicemail] {why}\n"
            f"Menu choice: {cat}\n"
            f"Language: {_LANGUAGE_LABEL.get(language, language)}\n"
            f"Caller: {from_number or 'unknown'}\n"
            f"Duration: {payload.get('RecordingDuration') or '?'} s\n"
            f"Recording: {listen_url} (playing the recording needs a Twilio console login)\n"
            f"Attend after: {conv['attend_after']}"
        )
        await log_port.append_conversation_comment(ticket_id, note, "open")
        await log_port.set_call_recording(
            ticket_id,
            recording_sid=str(payload.get("RecordingSid", "")),
            recording_duration=str(payload.get("RecordingDuration", "")),
            recording_url=recording_url,
        )
        await log_port.add_ticket_tag(ticket_id, VOICEMAIL_TAG)
        _log.info("voicemail_conversation_written", ticket_id=ticket_id, category=category, reason=reason)
        return str(ticket_id)
    except Exception as e:
        _log.error("voicemail_ingest_failed", error=str(e))
        return None
