"""POST /kb/feedback — record an agent's FAQ-quality feedback (auth'd)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.metrics.faq_feedback import FaqFeedback

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.faq_ratings import FaqRatingPort
    from chatbot.features.metrics.faq_feedback import FaqFeedbackPort
    from chatbot.platform.config import Settings


class FaqFeedbackRequest(BaseModel):
    article_id: str
    session_id: str = ""
    helpful: bool
    score: int = Field(ge=1, le=5)
    # FIX-23: set by the CRM strip's thumbs buttons. Thumbs up = 5, down = 1.
    agent_id: str = ""
    conversation_id: str = ""
    reason: str = Field(default="", max_length=500)


def build_faq_router(
    faq_port: FaqFeedbackPort,
    settings: Settings,
    rating_store: FaqRatingPort | None = None,
) -> APIRouter:
    router = APIRouter(tags=["kb"])

    @router.post("/kb/feedback")
    async def feedback(
        payload: FaqFeedbackRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        # The QA key (existing callers) or the shared CRM backend key -- the
        # only credential the browser strip holds, same one `/kb/faq` accepts.
        supplied = (x_api_key or "").encode("utf-8")
        if not x_api_key or not any(
            k and hmac.compare_digest(supplied, k.encode("utf-8"))
            for k in (settings.qa_api_key, settings.proton_backend_key)
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
        if rating_store is not None:
            await rating_store.rate(
                payload.article_id,
                payload.agent_id,
                payload.conversation_id or payload.session_id,
                payload.score,
                payload.reason,
            )
        return {"status": "ok"}

    return router
