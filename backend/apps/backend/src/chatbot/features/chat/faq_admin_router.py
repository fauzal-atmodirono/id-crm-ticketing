"""Admin CRUD for the real-time, CRM-editable FAQ knowledge base.

`POST /kb/faq`, `PUT /kb/faq/{id}`, `DELETE /kb/faq/{id}`, `GET /kb/faq`,
`POST /kb/faq/bulk` (CSV; all-or-nothing, `?partial=true` imports valid rows only).
Writes (and the listing) are guarded by an `x-api-key` header compared
constant-time against `Settings.faq_admin_api_key` (mirrors how the feedback
router guards `/kb/feedback`). An empty configured key 401s everything.
Embeddings are (re)computed inside the store on create/update, so the CRM edit
reflects in `/kb/suggest` immediately.
"""

from __future__ import annotations

import csv
import hmac
import io
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from chatbot.features.chat.ports import LiveFaqEntry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.faq_ratings import FaqRatingPort
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


_SCORES_TTL_SECONDS = 45.0


class _ScoresCache:
    """Short TTL cache of the ratings aggregate: `summaries()` scans the whole
    collection, and the admin page refreshes often. A rating shows up on the
    list within the TTL, which is fine for a quality metric."""

    def __init__(self, store: FaqRatingPort | None) -> None:
        self._store = store
        self._at = 0.0
        self._value: dict[str, tuple[float, int]] = {}

    async def get(self) -> dict[str, tuple[float, int]]:
        if self._store is None:
            return {}
        now = time.monotonic()
        if now - self._at >= _SCORES_TTL_SECONDS:
            self._value = await self._store.summaries()
            self._at = now
        return self._value


async def _with_scores(entries: list[LiveFaqEntry], cache: _ScoresCache) -> list[dict[str, Any]]:
    """FIX-23: average agent rating + count per FAQ (None / 0 when unrated)."""
    scores = await cache.get()
    rows: list[dict[str, Any]] = []
    for e in entries:
        avg, count = scores.get(e.id, (None, 0))
        rows.append(
            {
                **_entry_dict(e),
                "avg_score": round(avg, 2) if avg is not None else None,
                "rating_count": count,
            }
        )
    return rows


def build_faq_admin_router(
    store: LiveFaqPort | None,
    settings: Settings,
    rating_store: FaqRatingPort | None = None,
) -> APIRouter:
    router = APIRouter(tags=["kb"])
    scores_cache = _ScoresCache(rating_store)

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        candidates = [settings.faq_admin_api_key, settings.proton_backend_key]
        supplied = x_api_key.encode("utf-8")
        for key in candidates:
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
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
        return {"entries": await _with_scores(entries, scores_cache)}

    @router.post("/kb/faq/bulk")
    async def bulk_create_faq(
        file: UploadFile = File(...),
        partial: bool = False,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        raw = await file.read()
        if len(raw) > settings.kb_max_upload_bytes:
            raise HTTPException(status_code=413, detail="File too large")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="File must be UTF-8 encoded CSV") from exc

        # FIX-53: all-or-nothing by default. Validate every row before touching
        # the store, so a CSV with one bad row imports nothing and the operator
        # fixes the file once instead of hunting for what half-landed.
        # ?partial=true keeps the old row-by-row behaviour (the CRM's "Import valid
        # rows only" button).
        reader = csv.DictReader(io.StringIO(text))
        errors: list[dict[str, Any]] = []
        valid: list[tuple[int, LiveFaqEntry]] = []
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()
            if not question or not answer:
                errors.append({"row": i, "reason": "question and answer are required"})
                continue
            keywords = [k.strip() for k in (row.get("keywords") or "").split(";") if k.strip()]
            tags = [t.strip() for t in (row.get("tags") or "").split(";") if t.strip()]
            valid.append(
                (
                    i,
                    LiveFaqEntry(
                        id="",
                        question=question,
                        answer=answer,
                        keywords=keywords,
                        tags=tags,
                        active=True,
                    ),
                )
            )

        if errors and not partial:
            return {"created": 0, "errors": errors, "imported": False}

        created_ids: list[str] = []
        for i, entry in valid:
            try:
                created_ids.append(await _require_store().create(entry))
            except Exception as exc:
                errors.append({"row": i, "reason": str(exc)})
                if not partial:
                    # A store failure mid-import: undo what landed so the
                    # all-or-nothing promise holds. Rollback can itself fail,
                    # so say which ids are still in the store.
                    rollback_failed: list[str] = []
                    for entry_id in created_ids:
                        try:
                            await _require_store().delete(entry_id)
                        except Exception:
                            rollback_failed.append(entry_id)
                    if rollback_failed:
                        logger.error(
                            "faq bulk import rollback incomplete: %d of %d created entries "
                            "could not be deleted: %s",
                            len(rollback_failed),
                            len(created_ids),
                            rollback_failed,
                        )
                    result: dict[str, Any] = {
                        "created": len(rollback_failed),
                        "errors": errors,
                        "imported": False,
                    }
                    if rollback_failed:
                        result["rollback_failed_ids"] = rollback_failed
                    return result
        return {"created": len(created_ids), "errors": errors, "imported": True}

    return router
