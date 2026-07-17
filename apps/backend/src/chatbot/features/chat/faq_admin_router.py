"""Admin CRUD for the real-time, CRM-editable FAQ knowledge base.

`POST /kb/faq`, `PUT /kb/faq/{id}`, `DELETE /kb/faq/{id}`, `GET /kb/faq`.
Writes (and the listing) are guarded by an `x-api-key` header compared
constant-time against `Settings.faq_admin_api_key` (mirrors how the feedback
router guards `/kb/feedback`). An empty configured key 401s everything.
Embeddings are (re)computed inside the store on create/update, so the CRM edit
reflects in `/kb/suggest` immediately.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.chat.ports import LiveFaqEntry

if TYPE_CHECKING:
    from chatbot.features.chat.ports import LiveFaqPort
    from chatbot.platform.config import Settings


class FaqCreateRequest(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    active: bool = True


class FaqUpdateRequest(BaseModel):
    question: str | None = None
    answer: str | None = None
    keywords: list[str] | None = None
    tags: list[str] | None = None
    active: bool | None = None


def _entry_dict(entry: LiveFaqEntry) -> dict[str, Any]:
    """Serialize an entry for the admin listing (embedding vector omitted)."""
    return {
        "id": entry.id,
        "question": entry.question,
        "answer": entry.answer,
        "keywords": entry.keywords,
        "tags": entry.tags,
        "active": entry.active,
        "updated_at": entry.updated_at,
    }


def build_faq_admin_router(store: LiveFaqPort | None, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["kb"])

    def _authorize(x_api_key: str | None) -> None:
        key = settings.faq_admin_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode("utf-8"), key.encode("utf-8"))
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def _require_store() -> LiveFaqPort:
        if store is None:
            raise HTTPException(status_code=503, detail="Live FAQ store unavailable")
        return store

    @router.post("/kb/faq")
    async def create_faq(
        payload: FaqCreateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        entry_id = await _require_store().create(
            LiveFaqEntry(
                id="",
                question=payload.question,
                answer=payload.answer,
                keywords=payload.keywords,
                tags=payload.tags,
                active=payload.active,
            )
        )
        return {"id": entry_id, "status": "ok"}

    @router.put("/kb/faq/{entry_id}")
    async def update_faq(
        entry_id: str,
        payload: FaqUpdateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        fields = payload.model_dump(exclude_none=True)
        await _require_store().update(entry_id, fields)
        return {"id": entry_id, "status": "ok"}

    @router.delete("/kb/faq/{entry_id}")
    async def delete_faq(
        entry_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        await _require_store().delete(entry_id)
        return {"id": entry_id, "status": "ok"}

    @router.get("/kb/faq")
    async def list_faq(
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        entries = await _require_store().list_all()
        return {"entries": [_entry_dict(e) for e in entries]}

    return router
