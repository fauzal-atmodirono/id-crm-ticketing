"""POST /kb/feedback — record an agent's FAQ-quality feedback (auth'd)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.authz.deps import require_session_identity
from chatbot.features.metrics.faq_feedback import FaqFeedback

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.chat.adapters.faq_ratings import FaqRatingPort
    from chatbot.features.chat.ports import LiveFaqPort
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
    *,
    faq_store: LiveFaqPort | None = None,
    validator: TokenValidator | None = None,
) -> APIRouter:
    router = APIRouter(tags=["kb"])
    session_identity = require_session_identity(validator=validator, settings=settings)

    @router.post("/kb/feedback")
    async def feedback(
        payload: FaqFeedbackRequest,
        x_api_key: str | None = Header(default=None),
        session_user: int | None = Depends(session_identity),
    ) -> dict[str, str]:
        # With RBAC on the session dependency has already 401'd a caller with no
        # valid Chatwoot session; the shared key alone is never enough there.
        # The QA key (existing callers) or the shared CRM backend key -- the
        # only credential the browser strip holds, same one `/kb/faq` accepts.
        supplied = (x_api_key or "").encode("utf-8")
        if not x_api_key or not any(
            k and hmac.compare_digest(supplied, k.encode("utf-8"))
            for k in (settings.qa_api_key, settings.proton_backend_key)
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        # Who rated: the resolved session when RBAC is on (body ignored -- every
        # agent's browser holds the shared key, so a body id proves nothing),
        # else the body's agent_id. A request carrying agent/conversation ids
        # (or any RBAC-on request) is a FAQ rating; legacy callers sending
        # neither stay feedback-only, as before.
        agent_id = str(session_user) if session_user is not None else payload.agent_id.strip()
        is_rating = (
            session_user is not None or bool(payload.agent_id) or bool(payload.conversation_id)
        )
        if is_rating and rating_store is not None:
            if not agent_id:
                raise HTTPException(status_code=400, detail="agent_id required")
            if faq_store is not None:
                known = {e.id for e in await faq_store.list_all()}
                if payload.article_id not in known:
                    raise HTTPException(status_code=404, detail="Unknown FAQ")
        await faq_port.record_feedback(
            FaqFeedback(
                article_id=payload.article_id,
                session_id=payload.session_id,
                helpful=payload.helpful,
                score=payload.score,
                at=datetime.now(UTC),
            )
        )
        if is_rating and rating_store is not None:
            await rating_store.rate(
                payload.article_id,
                agent_id,
                payload.conversation_id or payload.session_id,
                payload.score,
                payload.reason,
            )
        return {"status": "ok"}

    return router
