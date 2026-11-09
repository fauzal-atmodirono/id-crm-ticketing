"""Signed player links, locating a call's archived audio, Range, and GCS reads.

The router (`recording_router.py`) is thin; the decisions live here so each
one is tested on its own.

**The link is a bearer credential, deliberately short.** An `<audio src>`
element cannot send the Chatwoot session headers every other admin call
carries, so the permission check happens when the link is MINTED
(`GET /calls/{id}/recording`) and the link itself is what authorises the
byte fetch. It is an HMAC-SHA256 over a small JSON claim set -- conversation
id, object name, kind (audio/transcript), the minting user's id, expiry
(<= 5 minutes) and a random nonce -- with `CALL_RECORDING_SIGNING_SECRET`.
Binding the conversation id means a link cannot be replayed against another
conversation's path; binding the object means the stream endpoint never has
to re-read Chatwoot (and a forged object name fails the MAC); the user id is
for the audit row, not for authentication.

**Locating.** New calls carry `call_recording_object` (written by
`recording_transcriber._stamp_archive_attributes`). Calls archived before that
existed only have the private note `recording_archive.note_archive_locations`
posted, so the note is parsed as a fallback. Either way the object must sit in
the configured archive bucket under the configured prefix with an archive-shaped
name: both sources are text in Chatwoot that staff can edit, and a stored value
must never be able to point the player at an arbitrary object the backend's
service account can read. Only PRIVATE notes count -- a customer can type the
note's exact text into a public message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

MAX_TTL_SECONDS = 300
MIN_SECRET_LENGTH = 32
ARCHIVE_NOTE_HEADER = "[Call archived to Cloud Storage]"

ATTR_AUDIO = "call_recording_object"
ATTR_TRANSCRIPT = "call_recording_transcript_object"

_AUDIO_SUFFIXES = (".mp3", ".wav")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._/-]+$")
_NOTE_LINE = re.compile(r"^\s*-\s*(audio|transcript)\s*:\s*gs://([^/\s]+)/(\S+)\s*$")
# How far back to look for an archive note: Chatwoot pages messages 20 at a
# time; a call's archive note lands minutes after the call, so it is almost
# always on the first page. Five pages bounds the cost on a long thread.
_MAX_NOTE_PAGES = 5


class InvalidRecordingToken(Exception):
    """Malformed, forged, expired, or bound to something else."""


class RangeNotSatisfiable(Exception):
    """A syntactically valid Range that lies wholly outside the object."""


@dataclass(frozen=True)
class TokenClaims:
    conversation_id: str
    object_name: str
    kind: str
    user_id: int | None
    expires_at: float
    nonce: str


@dataclass(frozen=True)
class ArchivedRecording:
    audio_object: str
    transcript_object: str = ""
    source: str = "attribute"  # attribute | note


def signing_secret(settings: Settings) -> str | None:
    """The configured key, or None when it is unset/too short to trust."""
    secret = (getattr(settings, "call_recording_signing_secret", "") or "").strip()
    return secret if len(secret) >= MIN_SECRET_LENGTH else None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def sign_token(
    secret: str,
    *,
    conversation_id: str,
    object_name: str,
    kind: str,
    user_id: int | None,
    ttl_seconds: int,
    now: float | None = None,
    nonce: str | None = None,
) -> tuple[str, float]:
    """`(token, expires_at_epoch)`. The TTL is capped at five minutes whatever
    the caller asks for."""
    issued = time.time() if now is None else now
    expires = issued + max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    claims = {
        "v": 1,
        "c": str(conversation_id),
        "o": object_name,
        "k": kind,
        "u": user_id,
        "e": int(expires),
        "n": nonce or secrets.token_urlsafe(9),
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_mac(secret, body)}", float(int(expires))


def verify_token(
    secret: str,
    token: str,
    *,
    conversation_id: str,
    kind: str,
    now: float | None = None,
) -> TokenClaims:
    """Claims of a valid token for this conversation and kind, else
    `InvalidRecordingToken` -- never any other exception."""
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, _mac(secret, body)):
            raise InvalidRecordingToken("bad signature")
        claims = json.loads(_unb64(body))
        if not isinstance(claims, dict) or claims.get("v") != 1:
            raise InvalidRecordingToken("bad version")
        expires = float(claims["e"])
        current = time.time() if now is None else now
        if current >= expires:
            raise InvalidRecordingToken("expired")
        if str(claims["c"]) != str(conversation_id):
            raise InvalidRecordingToken("wrong conversation")
        if claims["k"] != kind:
            raise InvalidRecordingToken("wrong kind")
        uid = claims.get("u")
        return TokenClaims(
            conversation_id=str(claims["c"]),
            object_name=str(claims["o"]),
            kind=str(claims["k"]),
            user_id=int(uid) if uid is not None else None,
            expires_at=expires,
            nonce=str(claims.get("n") or ""),
        )
    except InvalidRecordingToken:
        raise
    except Exception as exc:  # malformed base64/JSON/types -- all just "invalid"
        raise InvalidRecordingToken("malformed") from exc


def valid_object_name(name: str, prefix: str, *, kind: str) -> bool:
    """Archive-shaped: under `<prefix>/`, no traversal, no odd characters, and
    an audio or `.txt` suffix matching `kind`."""
    if not name or not _SAFE_NAME.match(name):
        return False
    if name.startswith("/") or ".." in name.split("/") or "//" in name:
        return False
    head = prefix.strip("/") + "/"
    if not name.startswith(head):
        return False
    if kind == "audio":
        return name.endswith(_AUDIO_SUFFIXES)
    return name.endswith(".txt")


def parse_archive_note(text: str, bucket: str, prefix: str) -> ArchivedRecording | None:
    """The `[Call archived to Cloud Storage]` note -> its objects, when they are
    in `bucket` under `prefix`. None when there is no playable audio."""
    if not text or not text.lstrip().startswith(ARCHIVE_NOTE_HEADER):
        return None
    found: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        m = _NOTE_LINE.match(line)
        if not m or m.group(2) != bucket:
            continue
        kind = "audio" if m.group(1) == "audio" else "transcript"
        if valid_object_name(m.group(3), prefix, kind=kind):
            found[kind] = m.group(3)
    if "audio" not in found:
        return None
    return ArchivedRecording(
        audio_object=found["audio"], transcript_object=found.get("transcript", ""), source="note"
    )


MessagesReader = Callable[..., Awaitable[Any]]


def _message_list(res: Any) -> list[dict[str, Any]]:
    if isinstance(res, dict):
        res = res.get("payload")
    return [m for m in res if isinstance(m, dict)] if isinstance(res, list) else []


async def find_archived_recording(  # noqa: PLR0911 -- each return is one documented fallback
    conversation_id: str,
    conversation: dict[str, Any] | None,
    read_messages: MessagesReader | None,
    *,
    bucket: str,
    prefix: str,
) -> ArchivedRecording | None:
    """Attribute first, newest private archive note second. Never raises."""
    if not bucket:
        return None
    attrs = (conversation or {}).get("custom_attributes") or {}
    audio = str(attrs.get(ATTR_AUDIO) or "")
    if audio:
        if valid_object_name(audio, prefix, kind="audio"):
            transcript = str(attrs.get(ATTR_TRANSCRIPT) or "")
            if not valid_object_name(transcript, prefix, kind="transcript"):
                transcript = ""
            return ArchivedRecording(audio_object=audio, transcript_object=transcript)
        _log.warning("call_recording_attribute_rejected", conversation_id=conversation_id)
    if read_messages is None:
        return None
    before: Any = None
    try:
        for _ in range(_MAX_NOTE_PAGES):
            page = _message_list(await read_messages(conversation_id, before))
            if not page:
                return None
            for msg in sorted(page, key=lambda m: m.get("id") or 0, reverse=True):
                if not msg.get("private"):
                    continue
                rec = parse_archive_note(str(msg.get("content") or ""), bucket, prefix)
                if rec is not None:
                    return rec
            oldest = min((m.get("id") for m in page if m.get("id") is not None), default=None)
            if oldest is None or oldest == before:
                return None
            before = oldest
    except Exception as exc:
        _log.warning(
            "call_recording_note_search_failed", conversation_id=conversation_id, error=str(exc)
        )
    return None


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:  # noqa: PLR0911
    """`(first, last)` inclusive for a single satisfiable byte range; None to
    serve the whole object (no header, a multi-range, a unit other than bytes,
    or a malformed spec -- all of which RFC 9110 lets a server ignore).
    `RangeNotSatisfiable` for a well-formed range wholly past the end."""
    if not header:
        return None
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        return None
    first_s, dash, last_s = spec.strip().partition("-")
    if not dash:
        return None
    first_s, last_s = first_s.strip(), last_s.strip()
    if (first_s and not first_s.isdigit()) or (last_s and not last_s.isdigit()):
        return None
    if not first_s:
        if not last_s:
            return None
        suffix = int(last_s)
        if suffix == 0 or size == 0:
            raise RangeNotSatisfiable
        return max(0, size - suffix), size - 1
    first = int(first_s)
    if last_s and int(last_s) < first:
        return None
    if first >= size:
        raise RangeNotSatisfiable
    last = int(last_s) if last_s else size - 1
    return first, min(last, size - 1)


class RecordingStorage(Protocol):
    """Blocking object-store reads; callers run them via asyncio.to_thread."""

    def size(self, bucket: str, name: str) -> int | None:
        """Bytes, or None when the object does not exist. Raises on any other
        failure, so "deleted" and "storage is down" stay distinguishable."""
        ...

    def read(self, bucket: str, name: str, start: int | None = None, end: int | None = None) -> bytes:
        """Bytes `start..end` inclusive (whole object when both are None)."""
        ...


class GcsRecordingStorage:
    """google-cloud-storage with the backend's own service account (ADC).
    Needs `storage.objects.get` on the archive bucket -- the archiver already
    has `storage.objects.create`, which does NOT include get."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    def _bucket(self, bucket: str) -> Any:
        if self._client is None:
            from google.cloud import storage  # noqa: PLC0415 -- deferred like recording_archive

            self._client = storage.Client(project=self._settings.firestore_project_id or None)
        return self._client.bucket(bucket)

    def size(self, bucket: str, name: str) -> int | None:
        blob = self._bucket(bucket).get_blob(name)
        return None if blob is None else int(blob.size or 0)

    def read(self, bucket: str, name: str, start: int | None = None, end: int | None = None) -> bytes:
        blob = self._bucket(bucket).blob(name)
        if start is None and end is None:
            return bytes(blob.download_as_bytes())
        # Ranged reads cannot be checksummed against the whole-object hash.
        return bytes(blob.download_as_bytes(start=start, end=end, checksum=None))
