"""HTTP surface for operator-authored knowledge documents (pgvector store).

Separate from ``kb_documents_router.py`` (the read-only Vertex corpus listing at
``GET /kb/documents``). This router serves the ``/kb/knowledge`` CRUD for
operator-authored documents that are chunked+embedded into pgvector.

Mirrors the FAQ-admin auth (x-api-key vs faq_admin_api_key / proton_backend_key).
Create endpoints return immediately with a ``pending`` id and dispatch the
extract→chunk→embed pipeline to a background task, matching the platform's
"return 200 fast, work in the background" webhook pattern.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from chatbot.features.chat.kb_ingest import ingest_file_document, ingest_text_document

_CHARS_PER_TOKEN = 4  # coarse token→char factor for the chunker


class _TextDocRequest(BaseModel):
    title: str
    body: str


def build_kb_knowledge_router(repo, embedder, settings) -> APIRouter:
    router = APIRouter()
    max_chars = settings.kb_chunk_size_tokens * _CHARS_PER_TOKEN
    overlap_chars = settings.kb_chunk_overlap_tokens * _CHARS_PER_TOKEN

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        supplied = x_api_key.encode("utf-8")
        for key in (settings.faq_admin_api_key, settings.proton_backend_key):
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")

    def _doc_dict(row) -> dict[str, Any]:
        return {
            "id": row.id, "title": row.title, "source_type": row.source_type,
            "status": row.status, "error": row.error, "char_count": row.char_count,
            "chunk_count": row.chunk_count, "created_at": row.created_at.isoformat(),
        }

    @router.post("/kb/knowledge/text")
    async def create_text(
        payload: _TextDocRequest, background: BackgroundTasks,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        doc_id = await repo.create_document(
            title=payload.title, source_type="text",
            original_filename=None, mime_type=None, char_count=len(payload.body),
        )
        background.add_task(
            ingest_text_document, repo, embedder, doc_id, payload.body,
            max_chars=max_chars, overlap_chars=overlap_chars,
        )
        return {"id": doc_id, "status": "pending"}

    @router.post("/kb/knowledge/file")
    async def create_file(
        background: BackgroundTasks,
        file: UploadFile = File(...),
        title: str | None = Form(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        data = await file.read()
        doc_id = await repo.create_document(
            title=title or file.filename or "Untitled", source_type="file",
            original_filename=file.filename, mime_type=file.content_type,
            char_count=len(data),
        )
        background.add_task(
            ingest_file_document, repo, embedder, doc_id,
            file.filename, file.content_type, data,
            max_chars=max_chars, overlap_chars=overlap_chars,
        )
        return {"id": doc_id, "status": "pending"}

    @router.get("/kb/knowledge")
    async def list_documents(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        rows = await repo.list_documents()
        return {"documents": [_doc_dict(r) for r in rows]}

    @router.get("/kb/knowledge/{document_id}")
    async def get_document(
        document_id: str, x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        row = await repo.get_document(document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _doc_dict(row)

    @router.delete("/kb/knowledge/{document_id}")
    async def delete_document(
        document_id: str, x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        if not await repo.delete_document(document_id):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": document_id, "status": "deleted"}

    return router
