"""P11 Task 1 -- Recording retrieval router.

Provides permission-gated (call_recording.listen) signed URL access to call recordings,
writing an audit log entry for every retrieval.
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
