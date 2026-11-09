"""POST /kb/feedback — record an agent's FAQ-quality feedback (auth'd)."""

from __future__ import annotations

import hmac
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.authz.deps import resolve_session_user_id
from chatbot.features.metrics.faq_feedback import FaqFeedback

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.chat.adapters.faq_ratings import FaqRatingPort
    from chatbot.features.chat.ports import LiveFaqPort
    from chatbot.features.metrics.faq_feedback import FaqFeedbackPort
    from chatbot.platform.config import Settings


_KNOWN_IDS_TTL_SECONDS = 45.0


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
    known_ids: set[str] = set()
    known_at = 0.0

    async def _faq_exists(article_id: str) -> bool:
        """Live-FAQ id check backed by a short-lived id set, so a rating does
        not list the whole store each time. An id missing from the cache is
        re-checked against a fresh listing (a FAQ created seconds ago must not
        404), which only unknown ids pay for."""
        nonlocal known_ids, known_at
        if faq_store is None:
            return True
        if article_id in known_ids and time.monotonic() - known_at < _KNOWN_IDS_TTL_SECONDS:
            return True
        known_ids = {e.id for e in await faq_store.list_all()}
        known_at = time.monotonic()
        return article_id in known_ids

    @router.post("/kb/feedback")
    async def feedback(
        payload: FaqFeedbackRequest,
        x_api_key: str | None = Header(default=None),
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict[str, str]:
        # Step 1, every caller: the QA key (legacy callers such as the
        # chatwoot-agent-app) or the shared CRM backend key (the strip).
        supplied = (x_api_key or "").encode("utf-8")
        if not x_api_key or not any(
            k and hmac.compare_digest(supplied, k.encode("utf-8"))
            for k in (settings.qa_api_key, settings.proton_backend_key)
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        # Step 2: is this a per-agent FAQ rating? Rating-shaped requests carry
        # an agent/conversation id (the strip) or a Chatwoot session. Legacy
        # callers send neither and stay feedback-only: the BigQuery row is
        # written, the ratings store is NOT (no agent to attribute it to).
        is_rating = bool(payload.agent_id or payload.conversation_id or x_chatwoot_access_token)
        agent_id = payload.agent_id.strip()
        if is_rating:
            # RBAC on: session mandatory (the shared key is in every agent's
            # browser, so it cannot say who rated) and the rater is the session
            # user, the body ignored. RBAC off: shared-key path, body agent_id.
            session_user = await resolve_session_user_id(
                settings, validator, x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
            )
            if session_user is not None:
                agent_id = str(session_user)
            if rating_store is not None:
                if not agent_id:
                    raise HTTPException(status_code=400, detail="agent_id required")
                if not await _faq_exists(payload.article_id):
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
