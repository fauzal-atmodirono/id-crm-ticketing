"""Archive a finished call recording and its transcript to Cloud Storage.

**Why.** Twilio is where recordings live by default, and only a URL reaches the
CRM. That leaves three problems this module exists to close: the audio sits in
Twilio's region rather than one the tenant chose, `PHONE_RECORDING_RETENTION_DAYS`
is unenforceable because there is no delete adapter, and Twilio bills storage
for every recording forever. Copying to a bucket the tenant owns makes
residency, lifecycle and cost the tenant's decisions.

**Layout** (operator-specified):

    gs://<bucket>/<prefix>/<YYYY-MM-DD>/<CallSid>.mp3
    gs://<bucket>/<prefix>/<YYYY-MM-DD>/<CallSid>.txt

Date-partitioned so a day's calls are one prefix -- cheap to list, and the unit
a GCS lifecycle rule operates on, which is how retention becomes real rather
than documentary.

Named by **CallSid**, not a fixed `call-recording.mp3`: a constant filename
would have every call in a day overwrite the previous one, and the SID is the
same identifier Twilio, the Chatwoot conversation and the logs already use, so
a file traces back without a lookup table.

Fail-open, like everything on this path: a failed upload logs and returns None,
leaving the Twilio copy and the CRM note exactly as they were. This runs as a
background task after the call has ended, so nothing here can affect a caller.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def object_path(prefix: str, call_sid: str, when: datetime, suffix: str) -> str:
    """`<prefix>/<YYYY-MM-DD>/<CallSid>.<suffix>`, with no leading slash.

    `when` is passed in rather than read from the clock so the audio and the
    transcript of one call can never land in different date folders -- a call
    archived either side of midnight UTC would otherwise be split across two
    prefixes and look like two calls.
    """
    day = when.astimezone(UTC).strftime("%Y-%m-%d")
    return f"{prefix.strip('/')}/{day}/{call_sid}.{suffix}"


def _upload_sync(settings: Settings, path: str, data: bytes, content_type: str) -> str | None:
    """Blocking GCS upload; callers run it via asyncio.to_thread. Returns the
    gs:// URI."""
    try:
        from google.cloud import storage  # noqa: PLC0415

        client = storage.Client(project=settings.firestore_project_id or None)
        bucket = client.bucket(settings.phone_recording_archive_bucket)
        blob = bucket.blob(path)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{settings.phone_recording_archive_bucket}/{path}"
    except Exception as e:
        _log.error("recording_archive_upload_failed", path=path, error=str(e))
        return None


async def archive_call(
    settings: Settings,
    call_sid: str,
    audio: bytes | None,
    transcript: str | None,
    when: datetime | None = None,
) -> dict[str, str]:
    """Upload whatever we have. Returns {"audio": uri, "transcript": uri} for
    the parts that succeeded -- a partial result is deliberate: a transcript
    that lands without its audio is still worth having, and vice versa.
    """
    if not settings.phone_recording_archive_enabled:
        return {}
    if not settings.phone_recording_archive_bucket:
        _log.warning("recording_archive_no_bucket_configured")
        return {}
    if not call_sid:
        # Without the SID there is no stable name, and a generated one would
        # be unreachable from the conversation it belongs to.
        _log.warning("recording_archive_no_call_sid")
        return {}

    stamp = when or datetime.now(UTC)
    prefix = settings.phone_recording_archive_prefix
    fmt = settings.phone_recording_archive_format
    out: dict[str, str] = {}

    if audio:
        uri = await asyncio.to_thread(
            _upload_sync,
            settings,
            object_path(prefix, call_sid, stamp, fmt),
            audio,
            f"audio/{'mpeg' if fmt == 'mp3' else fmt}",
        )
        if uri:
            out["audio"] = uri
    if transcript:
        uri = await asyncio.to_thread(
            _upload_sync,
            settings,
            object_path(prefix, call_sid, stamp, "txt"),
            transcript.encode("utf-8"),
            "text/plain; charset=utf-8",
        )
        if uri:
            out["transcript"] = uri
    if out:
        _log.info("recording_archived", call_sid=call_sid, **out)
    return out


async def note_archive_locations(log_port: Any, ticket_id: str, uris: dict[str, str]) -> bool:
    """Record the gs:// locations on the conversation so an operator can find
    the audio without going through Twilio."""
    if not uris:
        return False
    lines = [f"- {k}: {v}" for k, v in sorted(uris.items())]
    try:
        await log_port.append_conversation_comment(
            ticket_id, "[Call archived to Cloud Storage]\n" + "\n".join(lines)
        )
    except Exception as e:
        _log.error("recording_archive_note_failed", ticket_id=ticket_id, error=str(e))
        return False
    return True
