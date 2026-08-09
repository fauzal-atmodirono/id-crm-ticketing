"""POST /qa/label — authenticated manual QA-label entry endpoint (Phase 4).

P8 task 7: the same endpoint now optionally accepts a `channel` and the
five-criterion call rubric, but ONLY persists them when `call_qa_enabled`
is on -- off, a request carrying these fields is recorded exactly as
before (channel-agnostic accuracy/quality only), so a tenant that hasn't
opted in sees byte-identical behaviour even if a caller starts sending the
new fields early.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.metrics.qa import CallQaRubric, QaLabel

if TYPE_CHECKING:
    from chatbot.features.metrics.qa import QaLabelPort
    from chatbot.platform.config import Settings


class QaLabelRequest(BaseModel):
    conversation_id: str
    accuracy: int = Field(ge=0, le=100)
    quality: int = Field(ge=0, le=100)
    reviewer: str
    notes: str = ""
    # P8 task 7 -- see the module docstring for the call_qa_enabled gate.
    channel: str | None = None
    rubric_greeting: bool | None = None
    rubric_identification: bool | None = None
    rubric_resolution: bool | None = None
    rubric_closing: bool | None = None
    rubric_compliance: bool | None = None


def _call_rubric_from(payload: QaLabelRequest) -> CallQaRubric | None:
    """None when the caller sent no rubric fields at all -- a channel-only
    label (e.g. tagging a WhatsApp transcript's channel without scoring
    anything) must not manufacture a rubric of five Nones, which
    `CallQaRubric.percentage()` would correctly report as `incomplete` but
    which is a different thing from "not reviewed with a rubric at all"."""
    fields = (
        payload.rubric_greeting,
        payload.rubric_identification,
        payload.rubric_resolution,
        payload.rubric_closing,
        payload.rubric_compliance,
    )
    if all(f is None for f in fields):
        return None
    return CallQaRubric(
        greeting=payload.rubric_greeting,
        identification=payload.rubric_identification,
        resolution=payload.rubric_resolution,
        closing=payload.rubric_closing,
        compliance=payload.rubric_compliance,
    )


def build_qa_router(qa_port: QaLabelPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["qa"])

    @router.post("/qa/label")
    async def label(
        payload: QaLabelRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        key = settings.qa_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode("utf-8"), key.encode("utf-8"))
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        channel = payload.channel if settings.call_qa_enabled else None
        call_rubric = _call_rubric_from(payload) if settings.call_qa_enabled else None
        await qa_port.record_label(
            QaLabel(
                conversation_id=payload.conversation_id,
                accuracy=payload.accuracy,
                quality=payload.quality,
                reviewer=payload.reviewer,
                notes=payload.notes,
                labeled_at=datetime.now(UTC),
                channel=channel,
                call_rubric=call_rubric,
            )
        )
        return {"status": "ok", "message": "QA label recorded."}

    return router
