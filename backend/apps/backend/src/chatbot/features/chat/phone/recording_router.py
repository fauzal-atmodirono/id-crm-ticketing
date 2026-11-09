"""The in-CRM call-recording player: find, sign, stream, audit, re-transcribe.

After a call `recording_transcriber.process_recording` archives the audio (and
transcript) to the tenant's bucket and posts a private note with `gs://`
links -- which no staff browser can open. This router is what makes those
recordings playable inside the conversation (fork patch 0094's sidebar panel):

- `GET  /calls/{id}/recording` -- permission `call_recording.listen` + data
  scope. Finds the archived object (`recording_access.find_archived_recording`:
  the `call_recording_object` attribute, else the archive note), checks it
  still exists, and returns `{status, play_url, expires_at, transcript_url?,
  can_retranscribe}`. `play_url` is a BACKEND path carrying a <=5-minute
  HMAC token -- never a bucket URL, and no object name reaches the browser.
  Status is `available`, `empty` (no recording) or `deleted` (the object is
  gone: a retention lifecycle rule pruned it).
- `GET  /calls/{id}/recording/audio?t=` -- streams the object from GCS with
  the backend's service account. Honours `Range` (206/416) so the player can
  seek; `Cache-Control: no-store`. Authorised by the token alone, because an
  `<audio>` element cannot send the Chatwoot session headers (see
  recording_access.py for what the token binds).
- `GET  /calls/{id}/recording/transcript?t=` -- the archived `.txt`, same
  token scheme (a separate `kind`, so an audio link cannot open it).
- `POST /calls/{id}/recording/retranscribe` -- same permission + scope; re-runs
  the (glossary-prompted) transcriber on the archived audio in the background
  and posts a `[Re-transcribed]` private note. One run per conversation per
  `CALL_RECORDING_RETRANSCRIBE_COOLDOWN_SECONDS` (429 inside the window).

**Audit.** A play is one `call_recording_play` row in the audit store (the one
`/admin/audit` serves), naming the user the link was minted for, the
conversation and the time -- written on the FIRST byte request for a link, not
per Range request (a single listen issues several), and not on mint (opening
the conversation is not listening). Transcript views and re-transcriptions get
their own event types. Rows use `target="conversation:<id>"` and a synthetic
ticket id, so they appear in the global audit view without being mistaken for
case status transitions on the case timeline.

The audited-link set and the cooldown table are in-process. One backend
container per tenant makes that exact; with replicas a link played on two of
them would be audited twice (over-, never under-recorded) and the cooldown
would be per replica.

`CALL_RECORDING_RETRIEVAL_ENABLED` off -> every route 404s, even for a
permitted caller (`test_p11_wiring.py` drives this through the real app).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from chatbot.features.authz.data_scope import (
    Scope,
    build_scope_dependency,
    conversation_dimensions,
)
from chatbot.features.authz.deps import Actor, require_permission, resolve_actor
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
    find_archived_recording,
    parse_range,
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
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

_audited_links: OrderedDict[str, None] = OrderedDict()
_last_retranscribe: dict[str, float] = {}


def reset_state() -> None:
    """Test hook: forget audited links and cooldowns."""
    _audited_links.clear()
    _last_retranscribe.clear()


def _first_use(nonce: str) -> bool:
    """True exactly once per link, so one listen is one audit row."""
    if not nonce or nonce in _audited_links:
        return False
    _audited_links[nonce] = None
    while len(_audited_links) > _STATE_CAP:
        _audited_links.popitem(last=False)
    return True


def _audio_media_type(name: str) -> str:
    return "audio/wav" if name.endswith(".wav") else "audio/mpeg"


def build_recording_router(  # noqa: PLR0915 -- one router, four small handlers
    settings: Settings,
    *,
    authz_repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    audit: AuditLogPort | None = None,
    chatwoot_read: Callable[[str], Awaitable[Any]] | None = None,
    chatwoot_messages: Callable[..., Awaitable[Any]] | None = None,
    post_note: Callable[[str, str], Awaitable[Any]] | None = None,
    storage: RecordingStorage | None = None,
    transcribe: Callable[[Any, bytes], Awaitable[str | None]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> APIRouter:
    router = APIRouter(prefix="/calls", tags=["voice-recording"])
    listen = require_permission(
        "call_recording.listen", repo=authz_repo, validator=validator, settings=settings
    )
    data_scope = build_scope_dependency(settings, repo=authz_repo, validator=validator)
    actor_dep = resolve_actor(validator=validator, settings=settings)
    store: RecordingStorage = storage or GcsRecordingStorage(settings)

    if transcribe is None:
        from chatbot.features.chat.phone.recording_transcriber import (  # noqa: PLC0415
            transcribe_audio,
        )

        transcribe = transcribe_audio

    def _check_enabled() -> None:
        if not settings.call_recording_retrieval_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Call recording retrieval feature is disabled "
                    "(CALL_RECORDING_RETRIEVAL_ENABLED=false)"
                ),
            )

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

    async def _conversation_in_scope(cid: str, scope: Scope) -> dict[str, Any] | None:
        """The conversation, or 404 when it is outside the caller's data scope
        (404 rather than 403 so ids cannot be probed -- same rule as the case
        fields panel). Unrestricted callers keep a fail-open read."""
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

    async def _locate(cid: str, scope: Scope) -> ArchivedRecording | None:
        conversation = await _conversation_in_scope(cid, scope)
        return await find_archived_recording(
            cid, conversation, chatwoot_messages, bucket=_bucket(), prefix=_prefix()
        )

    def _actor_name(user_id: int | None) -> str:
        if user_id is None:
            return "admin-api-key"
        display = getattr(validator, "display_name", None)
        name = display(user_id) if callable(display) else None
        return name or f"user:{user_id}"

    async def _audit(
        event_type: str, cid: str, user_id: int | None, name: str, remark: str
    ) -> None:
        await record_event(
            audit,
            event_type=event_type,
            target=f"conversation:{cid}",
            actor_user_id=user_id,
            actor_name=name,
            remarks=remark,
            actor_source="call-recording-player",
        )

    def _verify(cid: str, token: str | None, kind: str) -> TokenClaims:
        secret = _secret()
        try:
            claims = verify_token(secret, token or "", conversation_id=cid, kind=kind)
        except InvalidRecordingToken as exc:
            _log.info(
                "call_recording_link_refused", conversation_id=cid, kind=kind, reason=str(exc)
            )
            raise HTTPException(
                status_code=403, detail="Invalid or expired recording link"
            ) from exc
        if not valid_object_name(claims.object_name, _prefix(), kind=kind):
            raise HTTPException(status_code=403, detail="Invalid or expired recording link")
        return claims

    async def _size(cid: str, name: str) -> int | None:
        try:
            return await asyncio.to_thread(store.size, _bucket(), name)
        except Exception as exc:
            _log.error("call_recording_storage_failed", conversation_id=cid, error=str(exc))
            raise HTTPException(status_code=502, detail="Recording storage unavailable") from exc

    # --- metadata ------------------------------------------------------------

    @router.get("/{conversation_id}/recording", dependencies=[Depends(listen)])
    async def get_call_recording(
        conversation_id: str,
        scope: Scope = Depends(data_scope),
        actor: Actor = Depends(actor_dep),
    ) -> JSONResponse:
        _check_enabled()
        secret = _secret()
        rec = await _locate(conversation_id, scope)
        if rec is None:
            return JSONResponse(
                {
                    "status": "empty",
                    "conversation_id": conversation_id,
                    "message": "No call recording exists for this conversation",
                },
                headers=_NO_STORE,
            )
        size = await _size(conversation_id, rec.audio_object)
        if size is None:
            return JSONResponse(
                {
                    "status": "deleted",
                    "conversation_id": conversation_id,
                    "message": "Call recording was deleted under the retention policy",
                },
                headers=_NO_STORE,
            )

        ttl = settings.call_recording_url_ttl_seconds
        path = f"/calls/{quote(conversation_id, safe='')}/recording"
        token, expires = sign_token(
            secret,
            conversation_id=conversation_id,
            object_name=rec.audio_object,
            kind="audio",
            user_id=actor.user_id,
            ttl_seconds=ttl,
        )
        body: dict[str, Any] = {
            "status": "available",
            "conversation_id": conversation_id,
            "play_url": f"{path}/audio?t={token}",
            "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
            "size_bytes": size,
            "can_retranscribe": bool(
                settings.phone_recording_transcription_enabled and post_note is not None
            ),
        }
        if rec.transcript_object:
            t_token, _ = sign_token(
                secret,
                conversation_id=conversation_id,
                object_name=rec.transcript_object,
                kind="transcript",
                user_id=actor.user_id,
                ttl_seconds=ttl,
            )
            body["transcript_url"] = f"{path}/transcript?t={t_token}"
        _log.info(
            "call_recording_link_minted",
            conversation_id=conversation_id,
            user_id=actor.user_id,
            source=rec.source,
        )
        return JSONResponse(body, headers=_NO_STORE)

    # --- audio -----------------------------------------------------------------

    @router.get("/{conversation_id}/recording/audio")
    async def stream_call_recording(
        conversation_id: str,
        t: str | None = Query(default=None),
        range_header: str | None = Header(default=None, alias="range"),
    ) -> Response:
        _check_enabled()
        claims = _verify(conversation_id, t, "audio")
        bucket, name = _bucket(), claims.object_name
        size = await _size(conversation_id, name)
        if size is None:
            raise HTTPException(status_code=404, detail="Recording no longer exists")

        headers = {**_NO_STORE, "Accept-Ranges": "bytes", "Content-Disposition": "inline"}
        try:
            span = parse_range(range_header, size)
        except RangeNotSatisfiable:
            return Response(
                status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
            )

        if _first_use(claims.nonce):
            await _audit(
                EVENT_CALL_RECORDING_PLAY,
                conversation_id,
                claims.user_id,
                _actor_name(claims.user_id),
                "Call recording played",
            )
            _log.info(
                "call_recording_played", conversation_id=conversation_id, user_id=claims.user_id
            )

        first, last = span if span is not None else (0, size - 1)
        headers["Content-Length"] = str(max(0, last - first + 1))
        code = 200
        if span is not None:
            code = 206
            headers["Content-Range"] = f"bytes {first}-{last}/{size}"

        async def _chunks() -> AsyncIterator[bytes]:
            pos = first
            while pos <= last:
                end = min(pos + _CHUNK_BYTES - 1, last)
                yield await asyncio.to_thread(store.read, bucket, name, pos, end)
                pos = end + 1

        return StreamingResponse(
            _chunks(), status_code=code, headers=headers, media_type=_audio_media_type(name)
        )

    # --- transcript --------------------------------------------------------------

    @router.get("/{conversation_id}/recording/transcript")
    async def get_call_transcript(
        conversation_id: str, t: str | None = Query(default=None)
    ) -> Response:
        _check_enabled()
        claims = _verify(conversation_id, t, "transcript")
        try:
            data = await asyncio.to_thread(store.read, _bucket(), claims.object_name, None, None)
        except Exception as exc:
            _log.error(
                "call_transcript_read_failed", conversation_id=conversation_id, error=str(exc)
            )
            raise HTTPException(status_code=404, detail="Transcript not available") from exc
        if _first_use(claims.nonce):
            await _audit(
                EVENT_CALL_RECORDING_TRANSCRIPT_VIEW,
                conversation_id,
                claims.user_id,
                _actor_name(claims.user_id),
                "Call transcript viewed",
            )
        return Response(
            content=data,
            media_type="text/plain; charset=utf-8",
            headers={**_NO_STORE, "Content-Disposition": "inline"},
        )

    # --- re-transcribe -------------------------------------------------------------

    async def _run_retranscribe(conversation_id: str, rec: ArchivedRecording) -> None:
        """Background task: never raises (the services invariant)."""
        try:
            audio = await asyncio.to_thread(store.read, _bucket(), rec.audio_object, None, None)
            text = await transcribe(settings, audio)
        except Exception as exc:
            _log.error("call_retranscribe_failed", conversation_id=conversation_id, error=str(exc))
            return
        if not text:
            _log.warning("call_retranscribe_empty", conversation_id=conversation_id)
            return
        try:
            await post_note(conversation_id, f"{RETRANSCRIBED_HEADER}\n\n{text}")
            _log.info("call_retranscribed", conversation_id=conversation_id, chars=len(text))
        except Exception as exc:
            _log.error(
                "call_retranscribe_note_failed", conversation_id=conversation_id, error=str(exc)
            )

    @router.post("/{conversation_id}/recording/retranscribe", dependencies=[Depends(listen)])
    async def retranscribe_call_recording(
        conversation_id: str,
        background_tasks: BackgroundTasks,
        scope: Scope = Depends(data_scope),
        actor: Actor = Depends(actor_dep),
    ) -> JSONResponse:
        _check_enabled()
        if not settings.phone_recording_transcription_enabled or post_note is None:
            raise HTTPException(
                status_code=409,
                detail="Transcription is disabled (PHONE_RECORDING_TRANSCRIPTION_ENABLED=false)",
            )
        rec = await _locate(conversation_id, scope)
        if rec is None:
            raise HTTPException(
                status_code=404, detail="No call recording exists for this conversation"
            )

        cooldown = max(0, int(settings.call_recording_retranscribe_cooldown_seconds))
        now = monotonic()
        last = _last_retranscribe.get(conversation_id)
        if last is not None and now - last < cooldown:
            raise HTTPException(
                status_code=429,
                detail="This call was re-transcribed recently; try again later",
                headers={"Retry-After": str(int(cooldown - (now - last)) + 1)},
            )
        _last_retranscribe[conversation_id] = now
        if len(_last_retranscribe) > _STATE_CAP:
            for key in [k for k, v in _last_retranscribe.items() if now - v >= cooldown]:
                del _last_retranscribe[key]

        await _audit(
            EVENT_CALL_RECORDING_RETRANSCRIBE,
            conversation_id,
            actor.user_id,
            actor.name,
            "Call recording re-transcription requested",
        )
        background_tasks.add_task(_run_retranscribe, conversation_id, rec)
        return JSONResponse(
            {
                "status": "queued",
                "conversation_id": conversation_id,
                "retry_after_seconds": cooldown,
            },
            status_code=202,
            headers=_NO_STORE,
        )

    return router
