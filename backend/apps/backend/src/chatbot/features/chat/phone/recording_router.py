"""The in-CRM call-recording player: find, prove, sign, stream, audit, re-transcribe.

After a call `recording_transcriber.process_recording` archives the audio (and
transcript) to the tenant's bucket -- with the conversation id in each object's
GCS metadata -- and posts a private note with `gs://` links no staff browser
can open. This router makes those recordings playable inside the conversation
(fork patch 0094's sidebar panel):

- `GET  /calls/{id}/recording` -- a real PERSON with `call_recording.listen`
  (`require_permission_with_identity`: a Chatwoot session, never the shared
  key, so 401 with RBAC off) inside their data scope. Resolves the recording
  and proves it belongs to this conversation (`recording_access.
  resolve_recording`: object metadata, never a Chatwoot value alone), then
  returns `{status, play_url, expires_at, transcript_url?, transcript_token?,
  can_retranscribe}`. Status `available`, `empty` or `deleted`; an object
  bound to another conversation, or a legacy one that could not be bound, is
  404. `?message_id=<id>` resolves ONE archive note instead (the in-note
  player, fork patch 0095: a conversation can hold several calls): that
  message, read from this conversation's own list, must be a private archive
  note authored by the backend's identity, and its objects are proven exactly
  as above (`recording_access.resolve_note_recording`); anything else is 404.
  `play_url` is a backend path carrying a <=5-minute HMAC token. The token
  is signed, not encrypted: the object path inside it is readable, the bucket
  never appears.
- `GET|HEAD /calls/{id}/recording/audio?t=` -- streams the object with the
  backend's service account after re-checking its metadata against the URL's
  conversation. `Range` (206/416) for seeking; `Cache-Control: no-store`. The
  token is the credential because an `<audio>` element cannot send session
  headers.
- `GET  /calls/{id}/recording/transcript` -- the archived `.txt`. The token
  comes in the `X-Recording-Token` header (the panel fetches it and renders it
  inline), so it never sits in a URL; `?t=` is still accepted.
- `POST /calls/{id}/recording/retranscribe` -- same person/permission/scope.
  Re-runs the transcriber on the archived audio in the background and posts a
  `[Re-transcribed]` private note (naming the note when `?message_id=` picked
  one call). Limits: one per conversation (per note with `?message_id=`) per
  cooldown,
  plus per-user and tenant-wide hourly caps (429 + Retry-After).

**Audit, fail-closed** (review R45). One row per minted LINK: the first byte
request for a link writes `call_recording_play` (actor, conversation, object,
time); later Range requests on the same link do not, and re-minting -- the
panel does so when a link expires mid-listen -- creates a new link and so a new
row. If the audit store refuses the row, the link's slot is released and the
request is 503: no audio is served without its audit row. Requests that
arrive while a link's first audit write is still in flight wait for it
(`_audited_once`) and share its outcome, so concurrent Range requests cannot
stream ahead of the row. Same for transcript
views and re-transcription requests.

**Tokens never reach access logs** (R44 item 4): `install_access_log_redaction`
filters `uvicorn.access` records, replacing the `t=` value on these routes.
A proxy in front (Caddy) has its own access log; see the deploy notes.

The audited-link set, cooldowns and hourly caps are in-process. One backend
container per tenant makes that exact; with replicas a link played on two of
them is audited twice (over-, never under-recorded) and limits apply per
replica.

`CALL_RECORDING_RETRIEVAL_ENABLED` off -> every route 404s for everyone; the
flag check runs before authentication (`test_p11_wiring.py`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse

from chatbot.features.authz.data_scope import (
    Scope,
    build_scope_dependency,
    conversation_dimensions,
)
from chatbot.features.authz.deps import require_permission_with_identity
from chatbot.features.chat.audit_events import (
    EVENT_CALL_RECORDING_PLAY,
    EVENT_CALL_RECORDING_RETRANSCRIBE,
    EVENT_CALL_RECORDING_TRANSCRIPT_VIEW,
    record_event,
)
from chatbot.features.chat.phone.recording_access import (
    ArchivedRecording,
    GcsRecordingStorage,
    InvalidRecordingToken,
    RangeNotSatisfiable,
    RecordingStorage,
    TokenClaims,
    object_bound_to,
    parse_range,
    resolve_note_recording,
    resolve_recording,
    sign_token,
    signing_secret,
    valid_object_name,
    verify_token,
)

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.ports import AuditLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

RETRANSCRIBED_HEADER = "[Re-transcribed]"
_CHUNK_BYTES = 256 * 1024
_STATE_CAP = 5000
_HOUR = 3600.0
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
# Chatwoot conversation ids are integers; anything else is refused (422).
ConversationId = Annotated[str, Path(pattern=r"^\d{1,12}$")]
# The in-note player names ONE archive note; same digits-only rule.
NoteMessageId = Annotated[str | None, Query(pattern=r"^\d{1,12}$")]

# nonce -> True (audited) or the Future of the audit write in flight.
_audited_links: OrderedDict[str, Any] = OrderedDict()
_last_retranscribe: dict[str, float] = {}
_user_runs: dict[int, deque[float]] = {}
_global_runs: deque[float] = deque()


def reset_state() -> None:
    """Test hook: forget audited links, cooldowns and hourly caps."""
    _audited_links.clear()
    _last_retranscribe.clear()
    _user_runs.clear()
    _global_runs.clear()


# --- access-log redaction ------------------------------------------------------

_TOKEN_IN_PATH = re.compile(r"(/recording/(?:audio|transcript)\?(?:[^\s\"]*?&)?t=)[^&\s\"]+")


def redact_recording_tokens(text: str) -> str:
    return _TOKEN_IN_PATH.sub(r"\1REDACTED", text)


class RecordingTokenRedactor(logging.Filter):
    """Rewrites the `t=` value of player links in a log record's message and
    string args (uvicorn's access record carries the path in its args)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_recording_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_recording_tokens(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def install_access_log_redaction() -> None:
    """Idempotent. A filter on the `uvicorn.access` LOGGER (not a handler)
    survives uvicorn's dictConfig, which replaces handlers but keeps filters."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RecordingTokenRedactor) for f in logger.filters):
        logger.addFilter(RecordingTokenRedactor())


# --- helpers -------------------------------------------------------------------


async def _audited_once(nonce: str, write: Callable[[], Awaitable[bool]]) -> bool:
    """Audit a link exactly once, and make every request on it wait for that.

    The first request for a link writes the audit row; any request arriving
    while that write is in flight awaits the same Future instead of streaming
    (PDPA: no byte leaves before the row is confirmed). Success marks the link
    audited for good; failure forgets it, so every waiter answers 503 and the
    next request retries the write."""
    if not nonce:
        return await write()
    state = _audited_links.get(nonce)
    if state is True:
        return True
    if isinstance(state, asyncio.Future):
        return bool(await state)
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    _audited_links[nonce] = future
    while len(_audited_links) > _STATE_CAP:
        _audited_links.popitem(last=False)
    try:
        ok = bool(await write())
    except Exception:
        ok = False
    if ok:
        _audited_links[nonce] = True
    else:
        _audited_links.pop(nonce, None)
    future.set_result(ok)
    return ok


def _audio_media_type(name: str) -> str:
    return "audio/wav" if name.endswith(".wav") else "audio/mpeg"


def _transcribe_mime(name: str) -> str:
    return "audio/wav" if name.endswith(".wav") else "audio/mp3"


def _prune(window: deque[float], now: float) -> None:
    while window and now - window[0] >= _HOUR:
        window.popleft()


def build_recording_router(  # noqa: PLR0915 -- one router, five small handlers
    settings: Settings,
    *,
    authz_repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    audit: AuditLogPort | None = None,
    chatwoot_read: Callable[[str], Awaitable[Any]] | None = None,
    chatwoot_messages: Callable[..., Awaitable[Any]] | None = None,
    post_note: Callable[[str, str], Awaitable[Any]] | None = None,
    backend_sender: Callable[[], Awaitable[tuple[str, int] | None]] | None = None,
    storage: RecordingStorage | None = None,
    transcribe: Callable[..., Awaitable[str | None]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> APIRouter:
    router = APIRouter(prefix="/calls", tags=["voice-recording"])
    person = require_permission_with_identity(
        "call_recording.listen", repo=authz_repo, validator=validator, settings=settings
    )
    data_scope = build_scope_dependency(settings, repo=authz_repo, validator=validator)
    store: RecordingStorage = storage or GcsRecordingStorage(settings)
    install_access_log_redaction()

    if transcribe is None:
        from chatbot.features.chat.phone.recording_transcriber import (  # noqa: PLC0415
            transcribe_audio,
        )

        transcribe = transcribe_audio

    def _enabled() -> None:
        if not settings.call_recording_retrieval_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Call recording retrieval feature is disabled "
                    "(CALL_RECORDING_RETRIEVAL_ENABLED=false)"
                ),
            )

    enabled = [Depends(_enabled)]

    def _secret() -> str:
        secret = signing_secret(settings)
        if secret is None:
            _log.error("call_recording_signing_secret_missing")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Call recording player is not configured (CALL_RECORDING_SIGNING_SECRET)",
            )
        return secret

    def _bucket() -> str:
        return settings.phone_recording_archive_bucket

    def _prefix() -> str:
        return settings.phone_recording_archive_prefix

    def _name(user_id: int | None) -> str:
        display = getattr(validator, "display_name", None)
        name = display(user_id) if callable(display) and user_id is not None else None
        return name or f"user:{user_id}"

    async def _conversation_in_scope(cid: str, scope: Scope) -> dict[str, Any] | None:
        """The conversation, or 404 when outside the caller's data scope (404,
        not 403, so ids cannot be probed -- the case-fields rule)."""
        conversation = None
        if chatwoot_read is not None:
            try:
                conversation = await chatwoot_read(cid)
            except Exception as exc:
                _log.warning(
                    "call_recording_conversation_read_failed", conversation_id=cid, error=str(exc)
                )
        if not isinstance(conversation, dict):
            conversation = None
        if not scope.unrestricted and not (
            conversation and scope.permits(conversation_dimensions(conversation))
        ):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    async def _resolve(cid: str, scope: Scope, message_id: str | None = None):
        conversation = await _conversation_in_scope(cid, scope)
        try:
            if message_id is not None:
                res = await resolve_note_recording(
                    cid,
                    int(message_id),
                    conversation,
                    chatwoot_messages,
                    store,
                    backend_sender,
                    bucket=_bucket(),
                    prefix=_prefix(),
                )
            else:
                res = await resolve_recording(
                    cid,
                    conversation,
                    chatwoot_messages,
                    store,
                    backend_sender,
                    bucket=_bucket(),
                    prefix=_prefix(),
                )
        except Exception as exc:
            _log.error("call_recording_storage_failed", conversation_id=cid, error=str(exc))
            raise HTTPException(status_code=502, detail="Recording storage unavailable") from exc
        if res.status in ("mismatch", "unverifiable", "missing"):
            raise HTTPException(status_code=404, detail="Recording not found")
        return res

    async def _write_audit(
        event_type: str, cid: str, user_id: int | None, object_name: str, remark: str
    ) -> bool:
        stored = await record_event(
            audit,
            event_type=event_type,
            target=f"conversation:{cid}",
            actor_user_id=user_id,
            actor_name=_name(user_id),
            after={"object": object_name},
            remarks=remark,
            actor_source="call-recording-player",
        )
        if not stored:
            _log.error("call_recording_audit_failed", conversation_id=cid, event_type=event_type)
        return stored

    def _audit_refused() -> HTTPException:
        return HTTPException(
            status_code=503, detail="Audit log unavailable; recording access refused"
        )

    async def _audit_or_503(
        event_type: str,
        cid: str,
        user_id: int | None,
        object_name: str,
        remark: str,
        on_fail: Callable[[], None] | None = None,
    ) -> None:
        if not await _write_audit(event_type, cid, user_id, object_name, remark):
            if on_fail is not None:
                on_fail()
            raise _audit_refused()

    def _verify(cid: str, token: str | None, kind: str) -> TokenClaims:
        secret = _secret()
        try:
            claims = verify_token(secret, token or "", conversation_id=cid, kind=kind)
        except InvalidRecordingToken as exc:
            _log.info("call_recording_link_refused", conversation_id=cid, kind=kind, reason=str(exc))
            raise HTTPException(status_code=403, detail="Invalid or expired recording link") from exc
        if not valid_object_name(claims.object_name, _prefix(), kind=kind):
            raise HTTPException(status_code=403, detail="Invalid or expired recording link")
        return claims

    async def _bound(cid: str, name: str):
        """Per-request re-check: the object exists and its metadata names this
        conversation. 404 otherwise."""
        try:
            info = await object_bound_to(store, _bucket(), name, cid)
        except Exception as exc:
            _log.error("call_recording_storage_failed", conversation_id=cid, error=str(exc))
            raise HTTPException(status_code=502, detail="Recording storage unavailable") from exc
        if info is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return info

    # --- metadata ------------------------------------------------------------

    @router.get("/{conversation_id}/recording", dependencies=enabled)
    async def get_call_recording(
        conversation_id: ConversationId,
        message_id: NoteMessageId = None,
        user_id: int = Depends(person),
        scope: Scope = Depends(data_scope),
    ) -> JSONResponse:
        secret = _secret()
        res = await _resolve(conversation_id, scope, message_id)
        note = {"message_id": int(message_id)} if message_id is not None else {}
        if res.status != "available" or res.recording is None:
            message = {
                "deleted": "Call recording was deleted under the retention policy",
            }.get(res.status, "No call recording exists for this conversation")
            return JSONResponse(
                {
                    "status": res.status,
                    "conversation_id": conversation_id,
                    "message": message,
                    **note,
                },
                headers=_NO_STORE,
            )
        rec: ArchivedRecording = res.recording
        ttl = settings.call_recording_url_ttl_seconds
        path = f"/calls/{conversation_id}/recording"
        body: dict[str, Any] = {
            "status": "available",
            "conversation_id": conversation_id,
            **note,
            "size_bytes": res.size,
            "can_retranscribe": bool(
                rec.audio_object
                and settings.phone_recording_transcription_enabled
                and post_note is not None
            ),
        }
        # A transcript-only archive note (per-note path) has no audio link.
        if rec.audio_object:
            token, expires = sign_token(
                secret,
                conversation_id=conversation_id,
                object_name=rec.audio_object,
                kind="audio",
                user_id=user_id,
                ttl_seconds=ttl,
            )
            body["play_url"] = f"{path}/audio?t={token}"
            body["expires_at"] = datetime.fromtimestamp(expires, UTC).isoformat()
        if rec.transcript_object:
            t_token, t_expires = sign_token(
                secret,
                conversation_id=conversation_id,
                object_name=rec.transcript_object,
                kind="transcript",
                user_id=user_id,
                ttl_seconds=ttl,
            )
            body["transcript_url"] = f"{path}/transcript"
            body["transcript_token"] = t_token
            body.setdefault("expires_at", datetime.fromtimestamp(t_expires, UTC).isoformat())
        _log.info(
            "call_recording_link_minted",
            conversation_id=conversation_id,
            user_id=user_id,
            source=rec.source,
            message_id=message_id,
        )
        return JSONResponse(body, headers=_NO_STORE)

    # --- audio -----------------------------------------------------------------

    @router.api_route(
        "/{conversation_id}/recording/audio", methods=["GET", "HEAD"], dependencies=enabled
    )
    async def stream_call_recording(
        request: Request,
        conversation_id: ConversationId,
        t: str | None = Query(default=None),
        range_header: str | None = Header(default=None, alias="range"),
    ) -> Response:
        claims = _verify(conversation_id, t, "audio")
        bucket, name = _bucket(), claims.object_name
        size = (await _bound(conversation_id, name)).size

        headers = {**_NO_STORE, "Accept-Ranges": "bytes", "Content-Disposition": "inline"}
        try:
            span = parse_range(range_header, size)
        except RangeNotSatisfiable:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})

        first, last = span if span is not None else (0, size - 1)
        headers["Content-Length"] = str(max(0, last - first + 1))
        code = 200
        if span is not None:
            code = 206
            headers["Content-Range"] = f"bytes {first}-{last}/{size}"
        media_type = _audio_media_type(name)

        if request.method == "HEAD":  # metadata only: no bytes, so no play row
            return Response(status_code=code, headers=headers, media_type=media_type)

        played = await _audited_once(
            claims.nonce,
            lambda: _write_audit(
                EVENT_CALL_RECORDING_PLAY,
                conversation_id,
                claims.user_id,
                name,
                "Call recording played",
            ),
        )
        if not played:
            raise _audit_refused()

        async def _chunks() -> AsyncIterator[bytes]:
            pos = first
            while pos <= last:
                end = min(pos + _CHUNK_BYTES - 1, last)
                yield await asyncio.to_thread(store.read, bucket, name, pos, end)
                pos = end + 1

        return StreamingResponse(_chunks(), status_code=code, headers=headers, media_type=media_type)

    # --- transcript --------------------------------------------------------------

    @router.get("/{conversation_id}/recording/transcript", dependencies=enabled)
    async def get_call_transcript(
        conversation_id: ConversationId,
        t: str | None = Query(default=None),
        x_recording_token: str | None = Header(default=None),
    ) -> Response:
        claims = _verify(conversation_id, x_recording_token or t, "transcript")
        await _bound(conversation_id, claims.object_name)
        try:
            data = await asyncio.to_thread(store.read, _bucket(), claims.object_name, None, None)
        except Exception as exc:
            _log.error("call_transcript_read_failed", conversation_id=conversation_id, error=str(exc))
            raise HTTPException(status_code=404, detail="Transcript not available") from exc
        viewed = await _audited_once(
            claims.nonce,
            lambda: _write_audit(
                EVENT_CALL_RECORDING_TRANSCRIPT_VIEW,
                conversation_id,
                claims.user_id,
                claims.object_name,
                "Call transcript viewed",
            ),
        )
        if not viewed:
            raise _audit_refused()
        return Response(
            content=data,
            media_type="text/plain; charset=utf-8",
            headers={**_NO_STORE, "Content-Disposition": "inline"},
        )

    # --- re-transcribe -------------------------------------------------------------

    async def _run_retranscribe(
        conversation_id: str, rec: ArchivedRecording, message_id: str | None = None
    ) -> None:
        """Background task: never raises (the services invariant)."""
        try:
            audio = await asyncio.to_thread(store.read, _bucket(), rec.audio_object, None, None)
            text = await transcribe(
                settings, audio, mime_type=_transcribe_mime(rec.audio_object)
            )
        except Exception as exc:
            _log.error("call_retranscribe_failed", conversation_id=conversation_id, error=str(exc))
            return
        if not text:
            _log.warning("call_retranscribe_empty", conversation_id=conversation_id)
            return
        try:
            # Several calls can share a conversation: say which one this is.
            which = (
                f" (call archived in note #{message_id})" if message_id is not None else ""
            )
            await post_note(conversation_id, f"{RETRANSCRIBED_HEADER}{which}\n\n{text}")
            _log.info("call_retranscribed", conversation_id=conversation_id, chars=len(text))
        except Exception as exc:
            _log.error("call_retranscribe_note_failed", conversation_id=conversation_id, error=str(exc))

    def _too_many(message: str, retry_after: float) -> HTTPException:
        """429 with the wait in BOTH the Retry-After header and the body: the
        panel's adminRequest surfaces only the body (`err.detail`)."""
        wait = int(retry_after) + 1
        return HTTPException(
            status_code=429,
            detail={"message": message, "retry_after_seconds": wait},
            headers={"Retry-After": str(wait)},
        )

    @router.post("/{conversation_id}/recording/retranscribe", dependencies=enabled)
    async def retranscribe_call_recording(
        background_tasks: BackgroundTasks,
        conversation_id: ConversationId,
        message_id: NoteMessageId = None,
        user_id: int = Depends(person),
        scope: Scope = Depends(data_scope),
    ) -> JSONResponse:
        if not settings.phone_recording_transcription_enabled or post_note is None:
            raise HTTPException(
                status_code=409,
                detail="Transcription is disabled (PHONE_RECORDING_TRANSCRIPTION_ENABLED=false)",
            )
        res = await _resolve(conversation_id, scope, message_id)
        if res.status != "available" or res.recording is None or not res.recording.audio_object:
            raise HTTPException(status_code=404, detail="No call recording exists for this conversation")

        now = monotonic()
        cooldown = max(0, int(settings.call_recording_retranscribe_cooldown_seconds))
        # Per call: a second call's note in the same conversation is its own key.
        key = conversation_id if message_id is None else f"{conversation_id}#{message_id}"
        last = _last_retranscribe.get(key)
        if last is not None and now - last < cooldown:
            raise _too_many(
                "This call was re-transcribed recently; try again later", cooldown - (now - last)
            )
        user_cap = int(settings.call_recording_retranscribe_user_hourly_cap)
        global_cap = int(settings.call_recording_retranscribe_global_hourly_cap)
        mine = _user_runs.setdefault(user_id, deque())
        _prune(mine, now)
        _prune(_global_runs, now)
        if user_cap > 0 and len(mine) >= user_cap:
            raise _too_many("Hourly re-transcription limit reached", _HOUR - (now - mine[0]))
        if global_cap > 0 and len(_global_runs) >= global_cap:
            raise _too_many(
                "Re-transcription is busy for this tenant; try again later",
                _HOUR - (now - _global_runs[0]),
            )

        previous = last
        _last_retranscribe[key] = now
        mine.append(now)
        _global_runs.append(now)

        def _rollback() -> None:
            if previous is None:
                _last_retranscribe.pop(key, None)
            else:
                _last_retranscribe[key] = previous
            if mine and mine[-1] == now:
                mine.pop()
            if _global_runs and _global_runs[-1] == now:
                _global_runs.pop()

        if len(_last_retranscribe) > _STATE_CAP:
            for stale in [k for k, v in _last_retranscribe.items() if now - v >= cooldown]:
                del _last_retranscribe[stale]

        await _audit_or_503(
            EVENT_CALL_RECORDING_RETRANSCRIBE,
            conversation_id,
            user_id,
            res.recording.audio_object,
            "Call recording re-transcription requested",
            on_fail=_rollback,
        )
        background_tasks.add_task(_run_retranscribe, conversation_id, res.recording, message_id)
        return JSONResponse(
            {"status": "queued", "conversation_id": conversation_id, "retry_after_seconds": cooldown},
            status_code=202,
            headers=_NO_STORE,
        )

    return router
