"""POST /kb/feedback — record an agent's FAQ-quality feedback (auth'd)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.metrics.faq_feedback import FaqFeedback

if TYPE_CHECKING:
    from chatbot.features.metrics.faq_feedback import FaqFeedbackPort
    from chatbot.platform.config import Settings


class FaqFeedbackRequest(BaseModel):
    article_id: str
    session_id: str = ""
    helpful: bool
    score: int = Field(ge=1, le=5)


def build_faq_router(faq_port: FaqFeedbackPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["kb"])

    @router.post("/kb/feedback")
    async def feedback(
        payload: FaqFeedbackRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        key = settings.qa_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode("utf-8"), key.encode("utf-8"))
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        await faq_port.record_feedback(
            FaqFeedback(
                article_id=payload.article_id,
                session_id=payload.session_id,
                helpful=payload.helpful,
                score=payload.score,
                at=datetime.now(UTC),
            )
        )
        return {"status": "ok"}

    return router
