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

The object name travels INSIDE the token: the claims are base64 JSON, signed
but not encrypted, so a link-holder can read the object path (never the bucket).
That is acceptable because the object is only served after its GCS metadata is
re-checked against the conversation, and only to a valid, unexpired link.

**Binding a recording to its conversation is server-side** (review R43).
`recording_archive.archive_call` writes `conversation_id` (and `call_sid`) into
each object's GCS metadata at upload, and `resolve_recording` will serve an
object for conversation N only when that metadata says N -- checked when the
link is minted AND again on every stream. Nothing Chatwoot holds is trusted on
its own: the `call_recording_object` custom attribute is a hint (agents can
edit custom attributes), and an object whose metadata names another
conversation is refused (404).

A hint that points at another conversation's object is ignored and resolution
falls through to the backend note, so a wrong attribute cannot hide the real
recording.

**Legacy objects** (archived before the metadata existed) are accepted only via
a private "[Call archived to Cloud Storage]" note that (a) was authored by the
backend's own Chatwoot API user -- the identity its `api_access_token`
resolves to via `/api/v1/profile` (`ApiTokenIdentity`) -- and (b) names an
object whose CallSid matches this conversation's phone session, read only from
fields agents cannot edit (`conversation_session_ids`).
On that first successful resolution the object's metadata is backfilled; if the
service account may not update metadata, the recording is refused rather than
served unbound. A backend identity that cannot be resolved disables the legacy
path (fail closed). Residue, stated plainly: if the backend's token is a human
admin's personal token, that admin's hand-typed notes look backend-authored --
the CallSid match is what still confines such a note to this call's own audio.
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


def parse_archive_note(
    text: str, bucket: str, prefix: str, *, allow_transcript_only: bool = False
) -> ArchivedRecording | None:
    """The `[Call archived to Cloud Storage]` note -> its objects, when they are
    in `bucket` under `prefix`. None when there is no playable audio -- unless
    `allow_transcript_only` (the per-note path), where a note with only a
    transcript yields `audio_object=""`."""
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
    if "audio" not in found and not (allow_transcript_only and "transcript" in found):
        return None
    return ArchivedRecording(
        audio_object=found.get("audio", ""),
        transcript_object=found.get("transcript", ""),
        source="note",
    )


MessagesReader = Callable[..., Awaitable[Any]]
SenderIdentity = Callable[[], Awaitable["tuple[str, int] | None"]]


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    metadata: dict[str, str]


@dataclass(frozen=True)
class Resolution:
    """`status`: available | empty | deleted | mismatch | unverifiable, plus
    `missing` from `resolve_note_recording` (no such backend archive note)."""

    status: str
    recording: ArchivedRecording | None = None
    size: int = 0


def _message_list(res: Any) -> list[dict[str, Any]]:
    if isinstance(res, dict):
        res = res.get("payload")
    return [m for m in res if isinstance(m, dict)] if isinstance(res, list) else []


_SENDER_TYPES = {"user": "user", "agentbot": "agent_bot", "agent_bot": "agent_bot"}


def message_sender(msg: dict[str, Any]) -> tuple[str, int] | None:
    """`("user"|"agent_bot"|..., id)` from either Chatwoot shape: the nested
    `sender: {type, id}` or the flat `sender_type` / `sender_id`."""
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    raw_type = sender.get("type") or msg.get("sender_type") or ""
    raw_id = sender.get("id") if sender.get("id") is not None else msg.get("sender_id")
    kind = _SENDER_TYPES.get(str(raw_type).replace(" ", "").lower(), str(raw_type).lower())
    try:
        return (kind, int(raw_id)) if kind and raw_id is not None else None
    except (TypeError, ValueError):
        return None


def call_sid_of(object_name: str) -> str:
    """`<prefix>/<day>/<CallSid>.<ext>` -> `<CallSid>`."""
    return object_name.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def conversation_session_ids(conversation: dict[str, Any] | None) -> set[str]:
    """The `phone-<CallSid>` session ids this conversation can be proven to
    belong to, from fields agents cannot edit in Chatwoot (ruling R46): the
    conversation's `additional_attributes.session_id`, stamped at create, and
    the call contact's `identifier`, which the adapter sets to the session id
    when it creates the contact. NOT `custom_attributes.external_id`: custom
    attributes are agent-editable. A caller attached to an existing contact by
    phone number carries that contact's older identifier, so the session id is
    the field that usually decides."""
    conv = conversation or {}
    sender = ((conv.get("meta") or {}).get("sender") or {}) if isinstance(conv.get("meta"), dict) else {}
    out = {
        str((conv.get("additional_attributes") or {}).get("session_id") or ""),
        str(sender.get("identifier") or ""),
    }
    return {s for s in out if s.startswith("phone-")}


async def _backend_note(
    conversation_id: str,
    read_messages: MessagesReader,
    backend: tuple[str, int],
    *,
    bucket: str,
    prefix: str,
) -> ArchivedRecording | None:
    """Newest private archive note authored by the backend itself."""
    before: Any = None
    for _ in range(_MAX_NOTE_PAGES):
        page = _message_list(await read_messages(conversation_id, before))
        if not page:
            return None
        for msg in sorted(page, key=lambda m: m.get("id") or 0, reverse=True):
            if not msg.get("private") or message_sender(msg) != backend:
                continue
            rec = parse_archive_note(str(msg.get("content") or ""), bucket, prefix)
            if rec is not None:
                return rec
        oldest = min((m.get("id") for m in page if m.get("id") is not None), default=None)
        if oldest is None or oldest == before:
            return None
        before = oldest
    return None


async def resolve_recording(  # noqa: PLR0912 -- each return is one documented outcome
    conversation_id: str,
    conversation: dict[str, Any] | None,
    read_messages: MessagesReader | None,
    storage: RecordingStorage,
    backend_sender: SenderIdentity | None,
    *,
    bucket: str,
    prefix: str,
) -> Resolution:
    """Find this conversation's recording and PROVE it belongs here (module
    docstring). Blocking storage calls go through asyncio.to_thread. Storage
    errors propagate (the router answers 502); Chatwoot read errors do not."""
    import asyncio  # noqa: PLC0415

    if not bucket:
        return Resolution("empty")
    cid = str(conversation_id)

    async def stat(name: str) -> ObjectInfo | None:
        return await asyncio.to_thread(storage.stat, bucket, name)

    def bound(info: ObjectInfo) -> str | None:
        return (info.metadata or {}).get("conversation_id")

    saw_deleted = False
    # 1. The attribute: a hint, trusted only through the object's metadata.
    attrs = (conversation or {}).get("custom_attributes") or {}
    audio = str(attrs.get(ATTR_AUDIO) or "")
    if audio and valid_object_name(audio, prefix, kind="audio"):
        info = await stat(audio)
        if info is None:
            saw_deleted = True
        elif bound(info) == cid:
            transcript = str(attrs.get(ATTR_TRANSCRIPT) or "")
            if transcript and not valid_object_name(transcript, prefix, kind="transcript"):
                transcript = ""
            if transcript:
                t_info = await stat(transcript)
                if t_info is None or bound(t_info) != cid:
                    transcript = ""
            return Resolution(
                "available", ArchivedRecording(audio, transcript, "attribute"), info.size
            )
        elif bound(info):
            # A hint naming another conversation's object proves nothing about
            # THIS conversation's recording: fall through to the backend note.
            _log.warning(
                "call_recording_hint_bound_elsewhere",
                conversation_id=cid,
                object_conversation=bound(info),
            )
    elif audio:
        _log.warning("call_recording_attribute_rejected", conversation_id=cid)

    # 2. The backend's own archive note (covers legacy objects).
    if read_messages is None or backend_sender is None:
        return Resolution("deleted" if saw_deleted else "empty")
    rec: ArchivedRecording | None = None
    try:
        backend = await backend_sender()
        if backend is None:
            _log.warning("call_recording_backend_identity_unknown", conversation_id=cid)
        else:
            rec = await _backend_note(cid, read_messages, backend, bucket=bucket, prefix=prefix)
    except Exception as exc:
        _log.warning("call_recording_note_search_failed", conversation_id=cid, error=str(exc))
    if rec is None:
        return Resolution("deleted" if saw_deleted else "empty")

    return await _prove_note_recording(cid, conversation, rec, storage, bucket)


async def _prove_note_recording(
    cid: str,
    conversation: dict[str, Any] | None,
    rec: ArchivedRecording,
    storage: RecordingStorage,
    bucket: str,
) -> Resolution:
    """A backend archive note names `rec`; prove its objects belong to `cid`.

    Bound objects must name `cid` in their metadata (else `mismatch`). An
    unbound (legacy) audio object is accepted only when its CallSid is one of
    this conversation's phone sessions, and is then backfilled -- or refused as
    `unverifiable` when the backfill is not permitted. The transcript follows
    the same rule but is dropped, never fatal."""
    import asyncio  # noqa: PLC0415

    def bound(info: ObjectInfo) -> str | None:
        return (info.metadata or {}).get("conversation_id")

    if not rec.audio_object:
        return await _prove_transcript_only(cid, conversation, rec, storage, bucket)

    info = await asyncio.to_thread(storage.stat, bucket, rec.audio_object)
    if info is None:
        return Resolution("deleted")
    owner = bound(info)
    if owner is not None and owner != cid:
        _log.warning("call_recording_bound_elsewhere", conversation_id=cid, object_conversation=owner)
        return Resolution("mismatch")
    if owner is None:
        sid = call_sid_of(rec.audio_object)
        if f"phone-{sid}" not in conversation_session_ids(conversation):
            _log.warning("call_recording_legacy_sid_mismatch", conversation_id=cid)
            return Resolution("mismatch")
        meta = {"conversation_id": cid, "call_sid": sid}
        if not await asyncio.to_thread(storage.set_metadata, bucket, rec.audio_object, meta):
            _log.error("call_recording_legacy_backfill_refused", conversation_id=cid)
            return Resolution("unverifiable")
        _log.info("call_recording_legacy_backfilled", conversation_id=cid)
    transcript = rec.transcript_object
    if transcript:
        t_info = await asyncio.to_thread(storage.stat, bucket, transcript)
        t_owner = bound(t_info) if t_info else None
        if t_info is None or (t_owner is not None and t_owner != cid) or (t_owner is None and not await asyncio.to_thread(
            storage.set_metadata,
            bucket,
            transcript,
            {"conversation_id": cid, "call_sid": call_sid_of(transcript)},
        )):
            transcript = ""
    return Resolution(
        "available", ArchivedRecording(rec.audio_object, transcript, "note"), info.size
    )


async def _prove_transcript_only(
    cid: str,
    conversation: dict[str, Any] | None,
    rec: ArchivedRecording,
    storage: RecordingStorage,
    bucket: str,
) -> Resolution:
    """A note with a transcript and no audio (per-note path only). With no
    audio to vouch for it, the transcript itself must pass the audio rule:
    bound to `cid`, or legacy with a matching CallSid and a permitted backfill."""
    import asyncio  # noqa: PLC0415

    name = rec.transcript_object
    info = await asyncio.to_thread(storage.stat, bucket, name)
    if info is None:
        return Resolution("deleted")
    owner = (info.metadata or {}).get("conversation_id")
    if owner is not None and owner != cid:
        _log.warning("call_transcript_bound_elsewhere", conversation_id=cid, object_conversation=owner)
        return Resolution("mismatch")
    if owner is None:
        sid = call_sid_of(name)
        if f"phone-{sid}" not in conversation_session_ids(conversation):
            _log.warning("call_transcript_legacy_sid_mismatch", conversation_id=cid)
            return Resolution("mismatch")
        meta = {"conversation_id": cid, "call_sid": sid}
        if not await asyncio.to_thread(storage.set_metadata, bucket, name, meta):
            _log.error("call_transcript_legacy_backfill_refused", conversation_id=cid)
            return Resolution("unverifiable")
    return Resolution("available", ArchivedRecording("", name, "note"), 0)


async def resolve_note_recording(  # noqa: PLR0911 -- each return is one documented outcome
    conversation_id: str,
    message_id: int,
    conversation: dict[str, Any] | None,
    read_messages: MessagesReader | None,
    storage: RecordingStorage,
    backend_sender: SenderIdentity | None,
    *,
    bucket: str,
    prefix: str,
) -> Resolution:
    """The recording named by ONE archive note (`message_id`) -- the in-note
    player, for conversations that hold more than one call.

    The message is read from this conversation's own message list (Chatwoot's
    `before=<id+1>` page ends at it), so a message id belonging to another
    conversation is simply not found. It must be a private note authored by the
    backend's own identity (`ApiTokenIdentity`; unknown identity -> refused)
    that parses as an archive note in this bucket/prefix. Its objects are then
    proven exactly as the newest-note path proves them
    (`_prove_note_recording`). Status `empty` when no archive bucket is
    configured, `missing` (no such backend archive note here), else as
    `resolve_recording`. The attribute hint is never consulted:
    the caller asked for this note's call, not the latest one."""
    cid = str(conversation_id)
    if not bucket:
        return Resolution("empty")  # archiving not configured: as resolve_recording
    if read_messages is None or backend_sender is None:
        return Resolution("missing")
    try:
        backend = await backend_sender()
        if backend is None:
            _log.warning("call_recording_backend_identity_unknown", conversation_id=cid)
            return Resolution("missing")
        page = _message_list(await read_messages(cid, int(message_id) + 1))
    except Exception as exc:
        _log.warning("call_recording_note_read_failed", conversation_id=cid, error=str(exc))
        return Resolution("missing")
    msg = next((m for m in page if str(m.get("id")) == str(message_id)), None)
    if (
        msg is None
        or not msg.get("private")
        or message_sender(msg) != backend
        or (msg.get("conversation_id") is not None and str(msg.get("conversation_id")) != cid)
    ):
        return Resolution("missing")
    rec = parse_archive_note(
        str(msg.get("content") or ""), bucket, prefix, allow_transcript_only=True
    )
    if rec is None:
        return Resolution("missing")
    return await _prove_note_recording(cid, conversation, rec, storage, bucket)


async def object_bound_to(
    storage: RecordingStorage, bucket: str, name: str, conversation_id: str
) -> ObjectInfo | None:
    """The object's info when it exists AND its metadata names this
    conversation; None otherwise. The per-stream re-check."""
    import asyncio  # noqa: PLC0415

    info = await asyncio.to_thread(storage.stat, bucket, name)
    if info is None or (info.metadata or {}).get("conversation_id") != str(conversation_id):
        return None
    return info


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
    """Blocking object-store calls; callers run them via asyncio.to_thread."""

    def stat(self, bucket: str, name: str) -> ObjectInfo | None:
        """Size + custom metadata, or None when the object does not exist.
        Raises on any other failure, so "deleted" and "storage is down" stay
        distinguishable."""
        ...

    def read(
        self, bucket: str, name: str, start: int | None = None, end: int | None = None
    ) -> bytes:
        """Bytes `start..end` inclusive (whole object when both are None)."""
        ...

    def set_metadata(self, bucket: str, name: str, metadata: dict[str, str]) -> bool:
        """Merge custom metadata onto the object. False when not permitted or
        on any failure -- never raises."""
        ...


class GcsRecordingStorage:
    """google-cloud-storage with the backend's own service account (ADC).
    Needs `storage.objects.get` on the archive bucket to play, and
    `storage.objects.update` only to backfill legacy objects' metadata."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    def _bucket(self, bucket: str) -> Any:
        if self._client is None:
            from google.cloud import storage  # noqa: PLC0415 -- deferred like recording_archive

            self._client = storage.Client(project=self._settings.firestore_project_id or None)
        return self._client.bucket(bucket)

    def stat(self, bucket: str, name: str) -> ObjectInfo | None:
        blob = self._bucket(bucket).get_blob(name)
        if blob is None:
            return None
        return ObjectInfo(size=int(blob.size or 0), metadata=dict(blob.metadata or {}))

    def read(
        self, bucket: str, name: str, start: int | None = None, end: int | None = None
    ) -> bytes:
        blob = self._bucket(bucket).blob(name)
        if start is None and end is None:
            return bytes(blob.download_as_bytes())
        # Ranged reads cannot be checksummed against the whole-object hash.
        return bytes(blob.download_as_bytes(start=start, end=end, checksum=None))

    def set_metadata(self, bucket: str, name: str, metadata: dict[str, str]) -> bool:
        try:
            blob = self._bucket(bucket).get_blob(name)
            if blob is None:
                return False
            blob.metadata = {**(blob.metadata or {}), **metadata}
            blob.patch()
            return True
        except Exception as exc:
            _log.error("call_recording_metadata_backfill_failed", error=str(exc))
            return False


class ApiTokenIdentity:
    """Who the backend's own Chatwoot `api_access_token` is: `("user", id)`
    from `/api/v1/profile`, cached. None when it cannot be resolved (an agent
    bot token has no profile) -- which disables the legacy note path rather
    than guessing."""

    _OK_TTL = 3600.0
    _FAIL_TTL = 60.0

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: tuple[float, tuple[str, int] | None] | None = None

    async def __call__(self) -> tuple[str, int] | None:
        now = time.monotonic()
        if self._cached is not None and self._cached[0] > now:
            return self._cached[1]
        identity: tuple[str, int] | None = None
        token = self._settings.chatwoot_api_token
        if token and self._settings.chatwoot_enabled:
            try:
                import httpx  # noqa: PLC0415

                url = f"{self._settings.chatwoot_api_url.rstrip('/')}/api/v1/profile"
                async with httpx.AsyncClient() as client:
                    res = await client.get(
                        url,
                        headers={"api_access_token": token, "Api-Access-Token": token},
                        timeout=5.0,
                    )
                    res.raise_for_status()
                    identity = ("user", int(res.json()["id"]))
            except Exception as exc:
                _log.warning("call_recording_api_identity_failed", error=str(exc))
        self._cached = (now + (self._OK_TTL if identity else self._FAIL_TTL), identity)
        return identity
