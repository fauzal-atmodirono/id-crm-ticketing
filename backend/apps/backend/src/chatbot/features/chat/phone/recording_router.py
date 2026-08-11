"""P11 Task 1 -- Recording retrieval router.

`GET /calls/{conversation_id}/recording`, gated on `call_recording.listen` and on
`CALL_RECORDING_RETRIEVAL_ENABLED`. Mounted in `main.py`; `test_p11_wiring.py`
asserts the route answers 401-rather-than-404 through the real app.

What this does **not** do yet, stated plainly because the plan asked for both and a
reader would otherwise assume them (tracked in
`docs/analysis/2026-08-09-blocked-work-register.md`):

1. **It does not read Chatwoot.** The plan's interface is the stored
   `recording_sid` / `recording_url` custom attributes; this module instead reads
   `_RECORDING_RETENTIONS`, an in-process dict that only `register_recording()`
   writes and which nothing in production calls. Against a real conversation the
   endpoint therefore answers the "no recording exists" state.
2. **It does not write an audit-log record.** Every retrieval emits a structured
   `call_recording_accessed` log line naming the caller, which is not the same
   thing as a row in the audit log `features/chat/audit_router.py` serves. The
   plan's `call_recording.listen` comment in `authz/seed.py` asks for the audit
   trail; that remains owed.
3. **The URL is not cryptographically signed.** `?signature=signed_token` is a
   fixed literal with an `expires` timestamp appended. It carries no secret and
   nothing validates it, so it is a placeholder for a signing step, not a
   short-lived credential. Do not describe this endpoint to a client as returning
   signed URLs until a real signer is wired in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Header, status
import structlog

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_RECORDING_RETENTIONS: dict[str, dict[str, Any]] = {}


def register_recording(conversation_id: str, recording_url: str, is_deleted: bool = False) -> None:
    _RECORDING_RETENTIONS[conversation_id] = {
        "url": recording_url,
        "is_deleted": is_deleted,
        "created_at": datetime.now(UTC),
    }


def reset_recordings() -> None:
    _RECORDING_RETENTIONS.clear()


def build_recording_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/calls", tags=["voice-recording"])

    def _check_enabled() -> None:
        if not settings.call_recording_retrieval_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Call recording retrieval feature is disabled (CALL_RECORDING_RETRIEVAL_ENABLED=false)",
            )

    @router.get(
        "/{conversation_id}/recording",
        dependencies=[Depends(require_permission("call_recording.listen", settings=settings))],
    )
    async def get_call_recording(
        conversation_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_enabled()

        rec = _RECORDING_RETENTIONS.get(conversation_id)
        if rec is None:
            return {
                "status": "empty",
                "conversation_id": conversation_id,
                "message": "No call recording exists for this conversation",
            }

        if rec.get("is_deleted"):
            return {
                "status": "deleted",
                "conversation_id": conversation_id,
                "message": "Call recording was deleted under the retention policy",
            }

        # Audited retrieval
        listener = x_api_key or "authenticated_user"
        _log.info(
            "call_recording_accessed",
            conversation_id=conversation_id,
            accessed_by=listener,
        )

        expires_at = datetime.now(UTC) + timedelta(minutes=15)
        signed_url = f"{rec['url']}?signature=signed_token&expires={int(expires_at.timestamp())}"

        return {
            "status": "available",
            "conversation_id": conversation_id,
            "recording_url": signed_url,
            "expires_at": expires_at.isoformat(),
        }

    return router
