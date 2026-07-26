"""Persistence port for the pgvector knowledge base.

``KbRepository`` is the interface the ingestion pipeline and the knowledge
adapter depend on. ``InMemoryKbRepository`` is the hermetic test double
(cosine computed in Python); ``PgKbRepository`` (added in Task 5) is the real
pgvector-backed implementation.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from chatbot.features.chat.kb_db import KbChunk, KbDocument


@dataclass(frozen=True)
class ChunkHit:
    doc_title: str
    content: str
    score: float  # cosine similarity in [-1, 1]; higher is closer


@dataclass(frozen=True)
class DocumentRow:
    id: str
    title: str
    source_type: str
    status: str
    error: str | None
    char_count: int
    chunk_count: int
    created_at: datetime


class KbRepository(Protocol):
    async def create_document(
        self, *, title: str, source_type: str,
        original_filename: str | None, mime_type: str | None, char_count: int,
    ) -> str: ...
    async def add_chunks(
        self, document_id: str, chunks: list[tuple[int, str, list[float], int]],
    ) -> None: ...
    async def set_status(
        self, document_id: str, status: str, error: str | None = None,
    ) -> None: ...
    async def list_documents(self) -> list[DocumentRow]: ...
    async def get_document(self, document_id: str) -> DocumentRow | None: ...
    async def delete_document(self, document_id: str) -> bool: ...
    async def search_chunks(self, embedding: list[float], limit: int) -> list[ChunkHit]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


@dataclass
class _Doc:
    row: DocumentRow
    chunks: list[tuple[int, str, list[float], int]] = field(default_factory=list)


class InMemoryKbRepository:
    """Hermetic test double for KbRepository."""

    def __init__(self) -> None:
        self._docs: dict[str, _Doc] = {}

    async def create_document(
        self, *, title, source_type, original_filename, mime_type, char_count,
    ) -> str:
        doc_id = uuid.uuid4().hex
        self._docs[doc_id] = _Doc(
            row=DocumentRow(
                id=doc_id, title=title, source_type=source_type, status="pending",
                error=None, char_count=char_count, chunk_count=0,
                created_at=datetime.now(timezone.utc),
            )
        )
        return doc_id

    async def add_chunks(self, document_id, chunks) -> None:
        doc = self._docs[document_id]
        doc.chunks.extend(chunks)
        doc.row = DocumentRow(**{**doc.row.__dict__, "chunk_count": len(doc.chunks)})

    async def set_status(self, document_id, status, error=None) -> None:
        doc = self._docs[document_id]
        doc.row = DocumentRow(**{**doc.row.__dict__, "status": status, "error": error})

    async def list_documents(self) -> list[DocumentRow]:
        return [d.row for d in self._docs.values()]

    async def get_document(self, document_id) -> DocumentRow | None:
        doc = self._docs.get(document_id)
        return doc.row if doc else None

    async def delete_document(self, document_id) -> bool:
        return self._docs.pop(document_id, None) is not None

    async def search_chunks(self, embedding, limit) -> list[ChunkHit]:
        hits: list[ChunkHit] = []
        for doc in self._docs.values():
            if doc.row.status != "indexed":
                continue
            for _idx, content, emb, _cc in doc.chunks:
                hits.append(ChunkHit(doc.row.title, content, _cosine(embedding, emb)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]


class PgKbRepository:
    """pgvector-backed KbRepository. Opens a short-lived session per call."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._sm = session_maker

    async def create_document(
        self, *, title, source_type, original_filename, mime_type, char_count,
    ) -> str:
        doc_id = uuid.uuid4().hex
        async with self._sm() as s:
            s.add(KbDocument(
                id=doc_id, title=title, source_type=source_type,
                original_filename=original_filename, mime_type=mime_type,
                char_count=char_count, status="pending",
            ))
            await s.commit()
        return doc_id

    async def add_chunks(self, document_id, chunks) -> None:
        async with self._sm() as s:
            for idx, content, emb, cc in chunks:
                s.add(KbChunk(
                    document_id=document_id, chunk_index=idx,
                    content=content, embedding=emb, char_count=cc,
                ))
            await s.commit()

    async def set_status(self, document_id, status, error=None) -> None:
        async with self._sm() as s:
            doc = await s.get(KbDocument, document_id)
            if doc is not None:
                doc.status = status
                doc.error = error
                await s.commit()

    async def list_documents(self) -> list[DocumentRow]:
        async with self._sm() as s:
            counts = (
                select(KbChunk.document_id, func.count().label("n"))
                .group_by(KbChunk.document_id)
                .subquery()
            )
            rows = (
                await s.execute(
                    select(KbDocument, func.coalesce(counts.c.n, 0))
                    .outerjoin(counts, counts.c.document_id == KbDocument.id)
                    .order_by(KbDocument.created_at.desc())
                )
            ).all()
        return [_row(d, int(n)) for d, n in rows]

    async def get_document(self, document_id) -> DocumentRow | None:
        async with self._sm() as s:
            doc = await s.get(KbDocument, document_id)
            if doc is None:
                return None
            n = (await s.execute(
                select(func.count()).select_from(KbChunk)
                .where(KbChunk.document_id == document_id)
            )).scalar_one()
        return _row(doc, int(n))

    async def delete_document(self, document_id) -> bool:
        async with self._sm() as s:
            doc = await s.get(KbDocument, document_id)
            if doc is None:
                return False
            await s.delete(doc)  # ORM relationship cascade (all, delete-orphan) removes chunks; DB-level ON DELETE CASCADE is the safety net (passive_deletes=True)
            await s.commit()
        return True

    async def search_chunks(self, embedding, limit) -> list[ChunkHit]:
        async with self._sm() as s:
            dist = KbChunk.embedding.cosine_distance(embedding).label("dist")
            rows = (
                await s.execute(
                    select(KbDocument.title, KbChunk.content, dist)
                    .join(KbDocument, KbChunk.document_id == KbDocument.id)
                    .where(KbDocument.status == "indexed")
                    .order_by(dist)
                    .limit(limit)
                )
            ).all()
        return [ChunkHit(title, content, 1.0 - float(d)) for title, content, d in rows]


def _row(doc: "KbDocument", chunk_count: int) -> DocumentRow:
    return DocumentRow(
        id=doc.id, title=doc.title, source_type=doc.source_type, status=doc.status,
        error=doc.error, char_count=doc.char_count, chunk_count=chunk_count,
        created_at=doc.created_at,
    )
