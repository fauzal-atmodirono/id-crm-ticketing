"""POST /qa/label — authenticated manual QA-label entry endpoint (Phase 4)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.metrics.qa import QaLabel

if TYPE_CHECKING:
    from chatbot.features.metrics.qa import QaLabelPort
    from chatbot.platform.config import Settings


class QaLabelRequest(BaseModel):
    conversation_id: str
    accuracy: int = Field(ge=0, le=100)
    quality: int = Field(ge=0, le=100)
    reviewer: str
    notes: str = ""


def build_qa_router(qa_port: QaLabelPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["qa"])

    @router.post("/qa/label")
    async def label(
        payload: QaLabelRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        key = settings.qa_api_key
        if not key or x_api_key is None or not hmac.compare_digest(x_api_key, key):
            raise HTTPException(status_code=401, detail="Unauthorized")
        await qa_port.record_label(
            QaLabel(
                conversation_id=payload.conversation_id,
                accuracy=payload.accuracy,
                quality=payload.quality,
                reviewer=payload.reviewer,
                notes=payload.notes,
                labeled_at=datetime.now(UTC),
            )
        )
        return {"status": "ok", "message": "QA label recorded."}

    return router
