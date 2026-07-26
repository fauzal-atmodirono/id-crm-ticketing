# pgvector Knowledge Base + No-Code Ingestion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a non-technical operator add knowledge (paste text or upload a file) from a Chatwoot dashboard app and have the chatbot ground its answers on it, served from the backend's own Postgres via `pgvector`.

**Architecture:** Add an async-SQLAlchemy Postgres layer to the backend (patterned on `agent/app/db/`) holding `kb_documents` + `kb_chunks` (with a `vector(768)` column). Ingestion extracts → chunks → embeds (Vertex `text-embedding-004`, reusing the existing `VertexEmbedder`) → persists. A new `PgVectorKnowledgeAdapter(KnowledgePort)` is merged into the existing `MergedKnowledgeAdapter` next to the Firestore Live FAQ store, so the copilot's `search_knowledge_base` tool sees pgvector results. Everything is behind a default-off `knowledge_pg_enabled` flag; initial VM rollout is proton-only.

**Tech Stack:** FastAPI, pydantic-settings, SQLAlchemy 2.0 async, psycopg3, pgvector, google-genai (embeddings), pypdf + python-docx (extraction), pytest (`asyncio_mode=auto`) + reportlab (PDF test fixtures).

## Global Constraints

- **Design spec:** `docs/superpowers/specs/2026-07-26-pgvector-knowledge-base-design.md`. Follow it; deferred items in `docs/superpowers/specs/2026-07-26-no-code-config-roadmap.md` are OUT OF SCOPE.
- **Settings:** `pydantic_settings.BaseSettings`, `SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")`, **no env prefix** (`KNOWLEDGE_PG_ENABLED` → `knowledge_pg_enabled`). New fields must match this style.
- **Embedding model:** Vertex `text-embedding-004`, **768 dimensions** → `vector(768)`. Reuse the existing `embedding_model` setting and `VertexEmbedder`.
- **`KbArticle`** (frozen dataclass, `features/chat/models.py`): fields `title: str`, `content: str`, `url: str | None = None`, `source_type: str | None = None`, plus optional `price`/`image_urls`/`brochure_url`. pgvector results use `source_type="pgvector"`.
- **Fail-open:** background tasks and retrieval NEVER raise for expected failures — log and degrade (mark a document `failed`; return `[]` from a failed source). Matches the repo's background-task invariant.
- **Auth:** reuse the FAQ-admin pattern — `x_api_key: str | None = Header(default=None)`, constant-time `hmac.compare_digest` against `settings.faq_admin_api_key` OR `settings.proton_backend_key`, else `HTTPException(401, "Unauthorized")`.
- **Tests:** hermetic. Construct `Settings(...)` in-test (never from `.env`); `conftest.py` already disables `.env`. Run from `backend/apps/backend/`: `uv run pytest src/chatbot/features/chat/ -v`. Co-locate `test_*.py` with the module under `src/chatbot/features/chat/` (matching existing `test_faq_admin_auth.py`).
- **No Alembic:** schema via `Base.metadata.create_all` + explicit `CREATE EXTENSION` / `CREATE INDEX`, matching the agent service.
- **Default-off:** `knowledge_pg_enabled` defaults `false`; when false, none of this wiring is constructed and behavior is identical to today.

---

## File Structure

**Create (backend, under `backend/apps/backend/src/chatbot/features/chat/`):**
- `kb_db.py` — SQLAlchemy `Base`, `KbDocument`, `KbChunk`, async engine/session factories, `init_kb_db`.
- `kb_repository.py` — `DocumentRow`/`ChunkHit` dataclasses, `KbRepository` Protocol, `PgKbRepository`, `InMemoryKbRepository`.
- `kb_ingest.py` — `extract_text`, `chunk_text`, `ingest_text_document`, `ingest_file_document`, `UnsupportedFileType`.
- `kb_documents_router.py` — `build_kb_documents_router(repo, embedder, settings)`.
- `adapters/pgvector_knowledge.py` — `PgVectorKnowledgeAdapter`.
- Tests: `test_kb_chunk.py`, `test_kb_extract.py`, `test_kb_repository_inmemory.py`, `test_pgvector_knowledge.py`, `test_kb_ingest.py`, `test_kb_documents_router.py`, `test_kb_repository_pg.py` (integration).

**Create (UI):** `backend/apps/chatwoot-knowledge/index.html`, `backend/apps/chatwoot-knowledge/README.md`.

**Modify:**
- `backend/apps/backend/pyproject.toml` — add deps.
- `backend/apps/backend/src/chatbot/platform/config.py` — new settings fields.
- `backend/apps/backend/src/chatbot/features/chat/adapters/merged_knowledge.py` — accept optional `pg_port`.
- `backend/apps/backend/src/chatbot/main.py` — wire engine/repo/adapter/router when enabled.
- `deploy/tenants/example.env`, `deploy/scripts/add-tenant.sh`, `deploy/docker-compose.infra.yml` — Postgres image + provisioning + env.

---

## Task 1: Dependencies + config fields

**Files:**
- Modify: `backend/apps/backend/pyproject.toml`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_config.py`

**Interfaces:**
- Produces: `Settings.knowledge_pg_enabled: bool`, `knowledge_database_url: str`, `kb_chunk_size_tokens: int`, `kb_chunk_overlap_tokens: int`, `kb_score_floor: float`.

- [ ] **Step 1: Write the failing test**

```python
# test_kb_config.py
from chatbot.platform.config import Settings


def test_knowledge_pg_defaults_off() -> None:
    s = Settings()
    assert s.knowledge_pg_enabled is False
    assert s.knowledge_database_url == ""
    assert s.kb_chunk_size_tokens == 800
    assert s.kb_chunk_overlap_tokens == 100
    assert s.kb_score_floor == 0.55


def test_knowledge_pg_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_PG_ENABLED", "true")
    monkeypatch.setenv("KNOWLEDGE_DATABASE_URL", "postgresql://x/y")
    s = Settings()
    assert s.knowledge_pg_enabled is True
    assert s.knowledge_database_url == "postgresql://x/y"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'knowledge_pg_enabled'`.

- [ ] **Step 3: Add the settings fields**

In `config.py`, next to the existing `faq_admin_api_key` / `embedding_model` block, add:

```python
    # --- pgvector knowledge base (subsystems A+B; default-off) ---
    knowledge_pg_enabled: bool = False
    knowledge_database_url: str = ""
    kb_chunk_size_tokens: int = 800
    kb_chunk_overlap_tokens: int = 100
    kb_score_floor: float = 0.55
```

- [ ] **Step 4: Add dependencies to `pyproject.toml`**

Add to the `dependencies` list:

```
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.2",
  "pgvector>=0.3",
  "python-docx>=1.1",
```

Add to `[dependency-groups] dev`:

```
  "aiosqlite>=0.20",
```

Then install: `cd backend/apps/backend && uv sync`

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/pyproject.toml backend/apps/backend/uv.lock backend/apps/backend/src/chatbot/platform/config.py backend/apps/backend/src/chatbot/features/chat/test_kb_config.py
git commit -m "feat(backend): pgvector KB config fields + deps"
```

---

## Task 2: Chunking

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/kb_ingest.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_chunk.py`

**Interfaces:**
- Produces: `chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]` — word-boundary-aware chunks; empty text → `[]`.

- [ ] **Step 1: Write the failing test**

```python
# test_kb_chunk.py
from chatbot.features.chat.kb_ingest import chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("", 100, 10) == []
    assert chunk_text("   ", 100, 10) == []


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("hello world", 100, 10) == ["hello world"]


def test_long_text_splits_on_word_boundaries_with_overlap() -> None:
    text = " ".join(f"w{i}" for i in range(20))  # each token ~3 chars
    chunks = chunk_text(text, max_chars=20, overlap_chars=6)
    assert len(chunks) > 1
    # never splits mid-word
    for c in chunks:
        assert "  " not in c
        assert len(c) <= 20
    # consecutive chunks share overlap words
    assert chunks[0].split()[-1] in chunks[1].split()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_chunk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chatbot.features.chat.kb_ingest'`.

- [ ] **Step 3: Implement `chunk_text` (start `kb_ingest.py`)**

```python
"""Knowledge ingestion: text extraction, chunking, and the embed pipeline.

All functions are fail-open where they touch external services: an embedding
failure marks the document ``failed`` rather than raising, matching the
background-task invariant.
"""

from __future__ import annotations


def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split ``text`` into word-boundary chunks of at most ``max_chars``,
    carrying ``overlap_chars`` of trailing words into the next chunk."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        add = len(word) + (1 if current else 0)
        if length + add > max_chars and current:
            chunks.append(" ".join(current))
            overlap: list[str] = []
            olen = 0
            for w in reversed(current):
                wl = len(w) + (1 if overlap else 0)
                if olen + wl > overlap_chars:
                    break
                overlap.insert(0, w)
                olen += wl
            current = overlap
            length = olen
            add = len(word) + (1 if current else 0)
        current.append(word)
        length += add
    if current:
        chunks.append(" ".join(current))
    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_chunk.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/kb_ingest.py backend/apps/backend/src/chatbot/features/chat/test_kb_chunk.py
git commit -m "feat(backend): word-boundary chunker for KB ingestion"
```

---

## Task 3: Text extraction

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/kb_ingest.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_extract.py`

**Interfaces:**
- Produces: `extract_text(filename: str | None, mime_type: str | None, data: bytes) -> str`; `class UnsupportedFileType(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# test_kb_extract.py
import io

import pytest
from reportlab.pdfgen import canvas

from chatbot.features.chat.kb_ingest import UnsupportedFileType, extract_text


def _make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def test_extract_plain_text() -> None:
    assert extract_text("notes.txt", "text/plain", b"hello world") == "hello world"


def test_extract_markdown() -> None:
    assert "Heading" in extract_text("doc.md", None, b"# Heading\nbody")


def test_extract_pdf() -> None:
    out = extract_text("brochure.pdf", "application/pdf", _make_pdf("PriceRM50000"))
    assert "PriceRM50000" in out


def test_unsupported_type_raises() -> None:
    with pytest.raises(UnsupportedFileType):
        extract_text("image.png", "image/png", b"\x89PNG")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_extract.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_text'`.

- [ ] **Step 3: Add extraction to `kb_ingest.py`**

Add these imports at the top and the functions below `chunk_text`:

```python
import io

import docx
from pypdf import PdfReader

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UnsupportedFileType(Exception):
    """Raised when an uploaded file's type cannot be extracted."""


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


def extract_text(filename: str | None, mime_type: str | None, data: bytes) -> str:
    name = (filename or "").lower()
    mime = mime_type or ""
    if name.endswith(".pdf") or mime == "application/pdf":
        return _extract_pdf(data)
    if name.endswith(".docx") or mime == _DOCX_MIME:
        return _extract_docx(data)
    if name.endswith((".md", ".markdown", ".txt")) or mime.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    raise UnsupportedFileType(f"Unsupported file type: {filename or mime_type}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_extract.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/kb_ingest.py backend/apps/backend/src/chatbot/features/chat/test_kb_extract.py
git commit -m "feat(backend): PDF/DOCX/text extraction for KB ingestion"
```

---

## Task 4: Repository — dataclasses, Protocol, in-memory fake

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/kb_repository.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_repository_inmemory.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ChunkHit{doc_title: str, content: str, score: float}`
  - `@dataclass(frozen=True) DocumentRow{id: str, title: str, source_type: str, status: str, error: str | None, char_count: int, chunk_count: int, created_at: datetime}`
  - `KbRepository` Protocol: `create_document(*, title, source_type, original_filename, mime_type, char_count) -> str`; `add_chunks(document_id, chunks: list[tuple[int, str, list[float], int]]) -> None`; `set_status(document_id, status, error=None) -> None`; `list_documents() -> list[DocumentRow]`; `get_document(document_id) -> DocumentRow | None`; `delete_document(document_id) -> bool`; `search_chunks(embedding: list[float], limit: int) -> list[ChunkHit]`.
  - `InMemoryKbRepository()` implementing it (cosine computed in Python).

- [ ] **Step 1: Write the failing test**

```python
# test_kb_repository_inmemory.py
from chatbot.features.chat.kb_repository import InMemoryKbRepository


async def test_create_list_delete_roundtrip() -> None:
    repo = InMemoryKbRepository()
    doc_id = await repo.create_document(
        title="Warranty", source_type="text",
        original_filename=None, mime_type=None, char_count=10,
    )
    docs = await repo.list_documents()
    assert len(docs) == 1 and docs[0].status == "pending" and docs[0].chunk_count == 0

    await repo.add_chunks(doc_id, [(0, "warranty is 5 years", [1.0, 0.0], 19)])
    await repo.set_status(doc_id, "indexed")
    docs = await repo.list_documents()
    assert docs[0].status == "indexed" and docs[0].chunk_count == 1

    assert await repo.delete_document(doc_id) is True
    assert await repo.list_documents() == []


async def test_search_ranks_by_cosine() -> None:
    repo = InMemoryKbRepository()
    d = await repo.create_document(
        title="Doc", source_type="text",
        original_filename=None, mime_type=None, char_count=0,
    )
    await repo.add_chunks(d, [
        (0, "near", [1.0, 0.0], 4),
        (1, "far", [0.0, 1.0], 3),
    ])
    await repo.set_status(d, "indexed")
    hits = await repo.search_chunks([1.0, 0.0], limit=2)
    assert hits[0].content == "near"
    assert hits[0].score > hits[1].score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_repository_inmemory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chatbot.features.chat.kb_repository'`.

- [ ] **Step 3: Implement dataclasses, Protocol, and `InMemoryKbRepository`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_repository_inmemory.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/kb_repository.py backend/apps/backend/src/chatbot/features/chat/test_kb_repository_inmemory.py
git commit -m "feat(backend): KbRepository port + in-memory test double"
```

---

## Task 5: DB models + pgvector repository

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/kb_db.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/kb_repository.py` (add `PgKbRepository`)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_db.py` (unit — URL helper only)

**Interfaces:**
- Consumes: `DocumentRow`, `ChunkHit` from Task 4.
- Produces:
  - `kb_db.py`: `Base`, `KbDocument`, `KbChunk`, `_to_async_url(url) -> str`, `build_engine(url) -> AsyncEngine`, `build_session_maker(engine) -> async_sessionmaker`, `async init_kb_db(engine) -> None`.
  - `kb_repository.py`: `PgKbRepository(session_maker)` implementing `KbRepository`.

- [ ] **Step 1: Write the failing test (URL helper)**

```python
# test_kb_db.py
from chatbot.features.chat.kb_db import _to_async_url


def test_postgres_url_upgraded() -> None:
    assert _to_async_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_sqlite_url_upgraded() -> None:
    assert _to_async_url("sqlite:///x.db") == "sqlite+aiosqlite:///x.db"


def test_already_async_untouched() -> None:
    assert _to_async_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chatbot.features.chat.kb_db'`.

- [ ] **Step 3: Implement `kb_db.py`**

```python
"""Async SQLAlchemy + pgvector layer for the knowledge base.

Patterned on ``agent/app/db/session.py``: ``_to_async_url`` upgrades bare
``postgresql://`` / ``sqlite://`` URLs to their async driver form. The engine
is built lazily (only when ``knowledge_pg_enabled``) so the backend boots
without a DB when the feature is off. ``init_kb_db`` ensures the ``vector``
extension, tables, and the HNSW index exist (no Alembic, matching the repo).
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 768  # text-embedding-004


class Base(DeclarativeBase):
    pass


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def build_engine(url: str) -> AsyncEngine:
    return create_async_engine(_to_async_url(url))


def build_session_maker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_kb_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx "
                "ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
```

- [ ] **Step 4: Run URL test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_db.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Add `PgKbRepository` to `kb_repository.py`**

Add these imports and class (the ORM query uses pgvector's `cosine_distance`; similarity = `1 - distance`):

```python
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from chatbot.features.chat.kb_db import KbChunk, KbDocument


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
            await s.delete(doc)  # ORM cascade removes chunks
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
```

- [ ] **Step 6: Run the full chat test suite to verify nothing broke**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/ -v`
Expected: PASS (all prior tests still green; `PgKbRepository` has no unit test yet — its integration test is Task 10).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/kb_db.py backend/apps/backend/src/chatbot/features/chat/kb_repository.py backend/apps/backend/src/chatbot/features/chat/test_kb_db.py
git commit -m "feat(backend): pgvector DB models + PgKbRepository"
```

---

## Task 6: PgVectorKnowledgeAdapter

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/adapters/pgvector_knowledge.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_pgvector_knowledge.py`

**Interfaces:**
- Consumes: `KbRepository`/`ChunkHit` (Task 4), `Embedder` (`live_faq.py`), `KbArticle` (`models.py`).
- Produces: `PgVectorKnowledgeAdapter(repo, embedder, score_floor: float)` implementing `search_kb(query, limit=2) -> list[KbArticle]`. Applies the score floor, returns the best chunk per document, `source_type="pgvector"`. Fail-open → `[]`.

- [ ] **Step 1: Write the failing test**

```python
# test_pgvector_knowledge.py
from chatbot.features.chat.adapters.pgvector_knowledge import PgVectorKnowledgeAdapter
from chatbot.features.chat.kb_repository import ChunkHit


class _Embedder:
    def __init__(self, vec): self._vec = vec
    async def embed(self, text): return self._vec


class _Repo:
    def __init__(self, hits): self._hits = hits
    async def search_chunks(self, embedding, limit): return self._hits[:limit]


class _FailingEmbedder:
    async def embed(self, text): return []


async def test_returns_best_chunk_per_document_above_floor() -> None:
    repo = _Repo([
        ChunkHit("Warranty", "chunk-a", 0.90),
        ChunkHit("Warranty", "chunk-b", 0.70),   # same doc, lower score -> dropped
        ChunkHit("Pricing", "chunk-c", 0.60),
        ChunkHit("Noise", "chunk-d", 0.40),      # below floor -> dropped
    ])
    adapter = PgVectorKnowledgeAdapter(repo, _Embedder([1.0]), score_floor=0.55)
    out = await adapter.search_kb("q", limit=5)
    titles = [a.title for a in out]
    assert titles == ["Warranty", "Pricing"]
    assert out[0].content == "chunk-a"
    assert out[0].source_type == "pgvector"


async def test_empty_embedding_returns_empty() -> None:
    adapter = PgVectorKnowledgeAdapter(_Repo([]), _FailingEmbedder(), score_floor=0.55)
    assert await adapter.search_kb("q") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_pgvector_knowledge.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the adapter**

```python
"""pgvector KnowledgePort adapter.

Embeds the query, retrieves nearest chunks from the repository, drops anything
below the cosine-similarity floor, and returns the single best chunk per source
document as a ``KbArticle``. Fail-open: any error yields no results so the
merged knowledge layer degrades to its other sources.
"""

from __future__ import annotations

import structlog

from chatbot.features.chat.models import KbArticle

_log = structlog.get_logger(__name__)


class PgVectorKnowledgeAdapter:
    def __init__(self, repo, embedder, score_floor: float) -> None:
        self._repo = repo
        self._embedder = embedder
        self._floor = score_floor

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        try:
            emb = await self._embedder.embed(query)
            if not emb:
                return []
            hits = await self._repo.search_chunks(emb, limit * 4)
        except Exception as e:  # never raise into grounding
            _log.error("pgvector_search_failed", error=str(e))
            return []
        best: dict[str, object] = {}
        for h in hits:
            if h.score < self._floor:
                continue
            cur = best.get(h.doc_title)
            if cur is None or h.score > cur.score:  # type: ignore[union-attr]
                best[h.doc_title] = h
        ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)[:limit]  # type: ignore[attr-defined]
        return [
            KbArticle(title=h.doc_title, content=h.content, url=None, source_type="pgvector")  # type: ignore[attr-defined]
            for h in ranked
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_pgvector_knowledge.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/adapters/pgvector_knowledge.py backend/apps/backend/src/chatbot/features/chat/test_pgvector_knowledge.py
git commit -m "feat(backend): PgVectorKnowledgeAdapter (best-chunk-per-doc, fail-open)"
```

---

## Task 7: Ingestion pipeline functions

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/kb_ingest.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_ingest.py`

**Interfaces:**
- Consumes: `chunk_text`, `extract_text`, `UnsupportedFileType` (this module); `KbRepository` (Task 4); `Embedder` (`live_faq.py`).
- Produces:
  - `async ingest_text_document(repo, embedder, document_id, text, *, max_chars, overlap_chars) -> None`
  - `async ingest_file_document(repo, embedder, document_id, filename, mime_type, data, *, max_chars, overlap_chars) -> None`
  - Both fail-open: set the document `failed` with an error message instead of raising.

- [ ] **Step 1: Write the failing test**

```python
# test_kb_ingest.py
from chatbot.features.chat.kb_ingest import ingest_file_document, ingest_text_document
from chatbot.features.chat.kb_repository import InMemoryKbRepository


class _Embedder:
    async def embed(self, text): return [float(len(text)), 1.0]


class _FailEmbedder:
    async def embed(self, text): return []


async def _new_doc(repo):
    return await repo.create_document(
        title="T", source_type="text",
        original_filename=None, mime_type=None, char_count=0,
    )


async def test_ingest_text_marks_indexed_with_chunks() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_text_document(repo, _Embedder(), doc_id, "hello world " * 50,
                               max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "indexed" and row.chunk_count > 1


async def test_ingest_empty_text_marks_failed() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_text_document(repo, _Embedder(), doc_id, "   ",
                               max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "failed"


async def test_ingest_embedding_failure_marks_failed() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_text_document(repo, _FailEmbedder(), doc_id, "some real text",
                               max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "failed"


async def test_ingest_unsupported_file_marks_failed() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_file_document(repo, _Embedder(), doc_id, "x.png", "image/png",
                               b"\x89PNG", max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_ingest.py -v`
Expected: FAIL — `ImportError: cannot import name 'ingest_text_document'`.

- [ ] **Step 3: Add the pipeline functions to `kb_ingest.py`**

```python
import structlog

_log = structlog.get_logger(__name__)


async def _embed_and_store(repo, embedder, document_id, text, max_chars, overlap_chars) -> None:
    chunks = chunk_text(text, max_chars, overlap_chars)
    if not chunks:
        await repo.set_status(document_id, "failed", "No extractable text")
        return
    rows: list[tuple[int, str, list[float], int]] = []
    for i, chunk in enumerate(chunks):
        emb = await embedder.embed(chunk)
        if not emb:
            await repo.set_status(document_id, "failed", "Embedding failed")
            return
        rows.append((i, chunk, emb, len(chunk)))
    await repo.add_chunks(document_id, rows)
    await repo.set_status(document_id, "indexed")


async def ingest_text_document(
    repo, embedder, document_id, text, *, max_chars, overlap_chars,
) -> None:
    try:
        await _embed_and_store(repo, embedder, document_id, text, max_chars, overlap_chars)
    except Exception as e:  # background task must not raise
        _log.error("kb_ingest_text_failed", document_id=document_id, error=str(e))
        await repo.set_status(document_id, "failed", str(e))


async def ingest_file_document(
    repo, embedder, document_id, filename, mime_type, data, *, max_chars, overlap_chars,
) -> None:
    try:
        text = extract_text(filename, mime_type, data)
        await _embed_and_store(repo, embedder, document_id, text, max_chars, overlap_chars)
    except UnsupportedFileType as e:
        await repo.set_status(document_id, "failed", str(e))
    except Exception as e:  # background task must not raise
        _log.error("kb_ingest_file_failed", document_id=document_id, error=str(e))
        await repo.set_status(document_id, "failed", str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_ingest.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/kb_ingest.py backend/apps/backend/src/chatbot/features/chat/test_kb_ingest.py
git commit -m "feat(backend): fail-open KB ingestion pipeline (text + file)"
```

---

## Task 8: Documents router (HTTP surface)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/kb_documents_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_documents_router.py`

**Interfaces:**
- Consumes: `KbRepository`, `Embedder`, `Settings`, ingestion functions.
- Produces: `build_kb_documents_router(repo, embedder, settings) -> APIRouter` with `POST /kb/documents/text`, `POST /kb/documents/file`, `GET /kb/documents`, `GET /kb/documents/{id}`, `DELETE /kb/documents/{id}`. All guarded by `x-api-key`.

- [ ] **Step 1: Write the failing test**

```python
# test_kb_documents_router.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.kb_documents_router import build_kb_documents_router
from chatbot.features.chat.kb_repository import InMemoryKbRepository
from chatbot.platform.config import Settings


class _Embedder:
    async def embed(self, text): return [1.0, 0.0]


def _client(repo):
    s = Settings(faq_admin_api_key="fk", kb_chunk_size_tokens=200, kb_chunk_overlap_tokens=20)
    app = FastAPI()
    app.include_router(build_kb_documents_router(repo, _Embedder(), s))
    return TestClient(app, raise_server_exceptions=False)


def test_requires_api_key() -> None:
    c = _client(InMemoryKbRepository())
    assert c.get("/kb/documents").status_code == 401


def test_create_text_then_list_indexed() -> None:
    repo = InMemoryKbRepository()
    c = _client(repo)
    r = c.post("/kb/documents/text",
               json={"title": "Warranty", "body": "the warranty is five years"},
               headers={"x-api-key": "fk"})
    assert r.status_code == 200
    doc_id = r.json()["id"]

    listing = c.get("/kb/documents", headers={"x-api-key": "fk"}).json()
    assert listing["documents"][0]["id"] == doc_id
    # TestClient runs the BackgroundTask before returning, so it is already indexed
    assert listing["documents"][0]["status"] == "indexed"


def test_upload_file_and_delete() -> None:
    repo = InMemoryKbRepository()
    c = _client(repo)
    r = c.post("/kb/documents/file",
               files={"file": ("notes.txt", b"hello knowledge base", "text/plain")},
               headers={"x-api-key": "fk"})
    doc_id = r.json()["id"]
    assert c.delete(f"/kb/documents/{doc_id}", headers={"x-api-key": "fk"}).status_code == 200
    assert c.get("/kb/documents", headers={"x-api-key": "fk"}).json()["documents"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_documents_router.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the router**

```python
"""HTTP surface for operator-authored knowledge documents.

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


def build_kb_documents_router(repo, embedder, settings) -> APIRouter:
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

    @router.post("/kb/documents/text")
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

    @router.post("/kb/documents/file")
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

    @router.get("/kb/documents")
    async def list_documents(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        rows = await repo.list_documents()
        return {"documents": [_doc_dict(r) for r in rows]}

    @router.get("/kb/documents/{document_id}")
    async def get_document(
        document_id: str, x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        row = await repo.get_document(document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _doc_dict(row)

    @router.delete("/kb/documents/{document_id}")
    async def delete_document(
        document_id: str, x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        if not await repo.delete_document(document_id):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": document_id, "status": "deleted"}

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_documents_router.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/kb_documents_router.py backend/apps/backend/src/chatbot/features/chat/test_kb_documents_router.py
git commit -m "feat(backend): /kb/documents CRUD router with background ingestion"
```

---

## Task 9: Merge into knowledge layer + wire into main.py

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/merged_knowledge.py`
- Modify: `backend/apps/backend/src/chatbot/main.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_merged_knowledge_pg.py`

**Interfaces:**
- Consumes: existing `MergedKnowledgeAdapter(base, live_faq_store, embedder)`; `PgVectorKnowledgeAdapter` (Task 6).
- Produces: `MergedKnowledgeAdapter(base, live_faq_store, embedder, pg_port=None)` — includes pgvector results (first, then live, then base; deduped by title). `main.py` builds the engine/repo/adapter/router when `knowledge_pg_enabled`.

- [ ] **Step 1: Write the failing test**

```python
# test_merged_knowledge_pg.py
from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter
from chatbot.features.chat.models import KbArticle


class _Base:
    async def search_kb(self, query, limit=2):
        return [KbArticle(title="Base", content="from-vertex", url=None)]


class _Pg:
    async def search_kb(self, query, limit=2):
        return [KbArticle(title="PgDoc", content="from-pgvector", url=None, source_type="pgvector")]


async def test_pg_results_included_and_first() -> None:
    merged = MergedKnowledgeAdapter(_Base(), None, None, pg_port=_Pg())
    out = await merged.search_kb("q", limit=5)
    titles = [a.title for a in out]
    assert "PgDoc" in titles and "Base" in titles
    assert titles[0] == "PgDoc"  # operator-authored pgvector ranks first


async def test_no_pg_port_is_backwards_compatible() -> None:
    merged = MergedKnowledgeAdapter(_Base(), None, None)
    out = await merged.search_kb("q", limit=5)
    assert [a.title for a in out] == ["Base"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_merged_knowledge_pg.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'pg_port'`.

- [ ] **Step 3: Extend `MergedKnowledgeAdapter`**

Change the constructor and `search_kb` in `merged_knowledge.py`. Add `pg_port=None` to `__init__` (store as `self._pg = pg_port`), add a helper, and prepend pg results:

```python
    def __init__(self, base, live_faq_store, embedder, pg_port=None) -> None:
        self._base = base
        self._live = live_faq_store
        self._embedder = embedder
        self._pg = pg_port

    async def _pg_articles(self, query: str, limit: int) -> list[KbArticle]:
        if self._pg is None:
            return []
        try:
            return await self._pg.search_kb(query, limit)
        except Exception as e:  # never raise into grounding
            _log.error("merged_pgvector_search_failed", error=str(e))
            return []
```

Then in `search_kb`, change the merge to include pg first:

```python
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        pg = await self._pg_articles(query, limit)
        live = await self._live_articles(query, limit)
        base = await self._base.search_kb(query, limit)
        merged: list[KbArticle] = []
        seen: set[str] = set()
        for a in [*pg, *live, *base]:
            key = (a.title or "").strip().lower()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(a)
        return merged[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_merged_knowledge_pg.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire into `main.py`**

In the app factory, after `assist_knowledge_port = MergedKnowledgeAdapter(...)` (around line 428), insert a guarded block that builds the pgvector stack and re-wraps the merged adapter with `pg_port`, initializes the DB in the lifespan/startup, and registers the router. Add near the knowledge wiring:

```python
    # --- pgvector knowledge base (subsystems A+B; default-off) ---
    kb_pg_adapter = None
    if settings.knowledge_pg_enabled and settings.knowledge_database_url:
        from chatbot.features.chat.adapters.pgvector_knowledge import PgVectorKnowledgeAdapter
        from chatbot.features.chat.kb_db import build_engine, build_session_maker, init_kb_db
        from chatbot.features.chat.kb_documents_router import build_kb_documents_router
        from chatbot.features.chat.kb_repository import PgKbRepository

        kb_engine = build_engine(settings.knowledge_database_url)
        kb_session_maker = build_session_maker(kb_engine)
        kb_repo = PgKbRepository(kb_session_maker)
        kb_embedder = (
            VertexEmbedder(_assist_genai, settings.embedding_model)
            if _assist_genai is not None else None
        )
        if kb_embedder is not None:
            kb_pg_adapter = PgVectorKnowledgeAdapter(kb_repo, kb_embedder, settings.kb_score_floor)
            app.include_router(build_kb_documents_router(kb_repo, kb_embedder, settings))
            app.state.kb_engine = kb_engine  # for init in lifespan startup

    if kb_pg_adapter is not None:
        assist_knowledge_port = MergedKnowledgeAdapter(
            knowledge_port, _assist_live_store, _assist_embedder, pg_port=kb_pg_adapter
        )
```

In the app's lifespan/startup (where the app already runs async startup), add the table init:

```python
    engine = getattr(app.state, "kb_engine", None)
    if engine is not None:
        from chatbot.features.chat.kb_db import init_kb_db
        await init_kb_db(engine)
```

> Match the exact insertion points to `main.py`'s current structure. If the app has no lifespan hook, add an `@app.on_event("startup")` that runs `init_kb_db` when `app.state.kb_engine` is set. The re-wrap must happen before `assist_knowledge_port` is passed to `_wire_assist()` / `_wire_copilot()`.

- [ ] **Step 6: Run the whole backend suite (feature still default-off in tests)**

Run: `cd backend/apps/backend && uv run pytest src/ -v`
Expected: PASS — all tests green; existing behavior unchanged when `knowledge_pg_enabled=False`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/adapters/merged_knowledge.py backend/apps/backend/src/chatbot/main.py backend/apps/backend/src/chatbot/features/chat/test_merged_knowledge_pg.py
git commit -m "feat(backend): merge pgvector into knowledge layer + wire behind flag"
```

---

## Task 10: Postgres integration test for PgKbRepository

**Files:**
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_repository_pg.py`

**Interfaces:**
- Consumes: `PgKbRepository`, `build_engine`, `build_session_maker`, `init_kb_db`.

This is the ONE test that requires a real pgvector Postgres. It is marked so the default hermetic suite skips it unless `KB_TEST_DATABASE_URL` is set (CI provides a `pgvector/pgvector` service).

- [ ] **Step 1: Write the integration test**

```python
# test_kb_repository_pg.py
import os

import pytest

from chatbot.features.chat.kb_db import build_engine, build_session_maker, init_kb_db
from chatbot.features.chat.kb_repository import PgKbRepository

_URL = os.environ.get("KB_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(_URL is None, reason="KB_TEST_DATABASE_URL not set")


async def test_pg_ingest_and_search_roundtrip() -> None:
    engine = build_engine(_URL)
    await init_kb_db(engine)
    repo = PgKbRepository(build_session_maker(engine))

    doc_id = await repo.create_document(
        title="Warranty", source_type="text",
        original_filename=None, mime_type=None, char_count=0,
    )
    near = [1.0] + [0.0] * 767
    far = [0.0] * 767 + [1.0]
    await repo.add_chunks(doc_id, [(0, "warranty five years", near, 19),
                                   (1, "unrelated", far, 9)])
    await repo.set_status(doc_id, "indexed")

    hits = await repo.search_chunks(near, limit=2)
    assert hits[0].content == "warranty five years"
    assert hits[0].score > hits[1].score

    assert await repo.delete_document(doc_id) is True
    await engine.dispose()
```

- [ ] **Step 2: Run it against a local pgvector Postgres**

```bash
docker run -d --rm --name kbpg -e POSTGRES_PASSWORD=pw -p 5433:5432 pgvector/pgvector:pg16
cd backend/apps/backend && KB_TEST_DATABASE_URL="postgresql://postgres:pw@localhost:5433/postgres" uv run pytest src/chatbot/features/chat/test_kb_repository_pg.py -v
docker stop kbpg
```
Expected: PASS (1 passed). Without `KB_TEST_DATABASE_URL`, it is SKIPPED.

- [ ] **Step 3: Verify the default suite skips it**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_kb_repository_pg.py -v`
Expected: 1 skipped.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/test_kb_repository_pg.py
git commit -m "test(backend): pgvector integration test for PgKbRepository"
```

---

## Task 11: Chatwoot "Knowledge" dashboard app

**Files:**
- Create: `backend/apps/chatwoot-knowledge/index.html`
- Create: `backend/apps/chatwoot-knowledge/README.md`

**Interfaces:**
- Consumes: `GET/POST/DELETE /kb/documents*` (Task 8). Reads `apiKey` + `backendBaseUrl` from URL query params; sends `x-api-key`; answers the Chatwoot postMessage handshake harmlessly (global admin, ignores context) — exactly like `chatwoot-faq-admin`.

This is a static app with no automated test; verification is manual (Step 3).

- [ ] **Step 1: Create `index.html`**

Model it on `backend/apps/chatwoot-faq-admin/index.html`. It must:
- Read `backendBaseUrl` (default to the same Cloud Run URL the FAQ admin uses) and `apiKey` from `URLSearchParams`.
- Provide an `apiFetch(path, options)` helper adding the `x-api-key` header and JSON-parsing (copy the FAQ-admin helper verbatim, including the empty-body handling).
- Answer the Chatwoot handshake: `window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*')` and an ignored `message` listener.
- Render three areas: (a) an "Add text" form (`title`, `body`) → `POST /kb/documents/text`; (b) an "Upload file" input (accept `.pdf,.docx,.md,.txt`) → `POST /kb/documents/file` as `multipart/form-data` (do NOT set `Content-Type`; let the browser set the boundary — so this call uses a headers object with only `x-api-key`); (c) a documents table from `GET /kb/documents` showing title, type, a **status badge** (`pending`/`indexed`/`failed` + `error` tooltip), chunk count, created date, and a delete button → `DELETE /kb/documents/{id}`.
- Auto-refresh the list every 3s while any row is `pending` (stop polling when none are).

Skeleton (fill the body markup + styles from the FAQ-admin app):

```html
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Proton Knowledge</title></head>
<body>
  <!-- add-text form, file-upload input, documents table -->
  <script>
    const DEFAULT_BACKEND = 'https://proton-backend-247165654737.asia-southeast1.run.app';
    const params = new URLSearchParams(window.location.search);
    const backendBaseUrl = (params.get('backendBaseUrl') || DEFAULT_BACKEND).replace(/\/+$/, '');
    const apiKey = params.get('apiKey') || '';

    function headers(extra) { return { ...(apiKey ? { 'x-api-key': apiKey } : {}), ...(extra || {}) }; }

    async function apiFetch(path, options) {
      const res = await fetch(`${backendBaseUrl}${path}`, options);
      if (!res.ok) { const t = await res.text(); throw new Error(`${res.status}: ${t}`); }
      const text = await res.text();
      return text ? JSON.parse(text) : {};
    }

    async function addText(title, body) {
      return apiFetch('/kb/documents/text', {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ title, body }),
      });
    }

    async function uploadFile(file, title) {
      const fd = new FormData();
      fd.append('file', file);
      if (title) fd.append('title', title);
      return apiFetch('/kb/documents/file', { method: 'POST', headers: headers(), body: fd });
    }

    async function listDocs() { return apiFetch('/kb/documents', { headers: headers() }); }
    async function deleteDoc(id) { return apiFetch(`/kb/documents/${id}`, { method: 'DELETE', headers: headers() }); }

    window.addEventListener('message', () => { /* global admin: ignore appContext */ });
    try { window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*'); } catch (e) {}
    // render(): call listDocs(), draw the table, poll every 3s while any status === 'pending'
  </script>
</body>
</html>
```

- [ ] **Step 2: Create `README.md`**

Document (mirroring `chatwoot-faq-admin/README.md`): purpose (operator knowledge-base manager), how to host it, the query params (`backendBaseUrl`, `apiKey`), how to register it in Chatwoot (Settings → Integrations → Dashboard Apps → Add, paste the URL with `?apiKey=<FAQ_ADMIN_API_KEY>&backendBaseUrl=<backend>`), and the backend flag it needs (`KNOWLEDGE_PG_ENABLED=true`).

- [ ] **Step 3: Manual verification**

Serve the app and the backend locally, then confirm the round-trip:

```bash
# backend with the feature on, pointed at a local pgvector Postgres (see Task 10)
cd backend/apps/backend && KNOWLEDGE_PG_ENABLED=true \
  KNOWLEDGE_DATABASE_URL="postgresql://postgres:pw@localhost:5433/postgres" \
  FAQ_ADMIN_API_KEY=devkey uv run uvicorn chatbot.main:app --port 8080
# serve the app
cd backend/apps/chatwoot-knowledge && python3 -m http.server 8090
# open: http://localhost:8090/index.html?apiKey=devkey&backendBaseUrl=http://localhost:8080
```
Confirm: add-text creates a row that flips `pending`→`indexed`; file upload works; delete removes it; a wrong `apiKey` yields a 401 surfaced in the UI.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/chatwoot-knowledge/
git commit -m "feat(ui): Chatwoot 'Knowledge' dashboard app (documents CRUD + upload)"
```

---

## Task 12: Deploy wiring (Postgres image, provisioning, env, rollout)

**Files:**
- Modify: `deploy/docker-compose.infra.yml`
- Modify: `deploy/scripts/add-tenant.sh`
- Modify: `deploy/tenants/example.env`

**Interfaces:** none (infra/config only).

- [ ] **Step 1: Switch the shared Postgres image to a pgvector build**

In `deploy/docker-compose.infra.yml`, change the Postgres service image to `pgvector/pgvector:pg16` (keep the same major version currently in use; adjust the tag to match). This adds the `vector` extension binary while remaining a drop-in Postgres.

- [ ] **Step 2: Document the new env vars in `deploy/tenants/example.env`**

Add, near the existing `FAQ_ADMIN_API_KEY` / `EMBEDDING_MODEL` block:

```
# --- pgvector knowledge base (subsystems A+B; default-off) ---
KNOWLEDGE_PG_ENABLED=false
KNOWLEDGE_DATABASE_URL=postgresql://backend:<password>@postgres:5432/backend_<tenant>
KB_CHUNK_SIZE_TOKENS=800
KB_CHUNK_OVERLAP_TOKENS=100
KB_SCORE_FLOOR=0.55
```

- [ ] **Step 3: Provision the backend DB + extension in `add-tenant.sh`**

Add a step that creates `backend_<tenant>` and enables the extension (mirror how the script already creates `chatwoot_<tenant>` / `agent_<tenant>`):

```bash
# backend knowledge DB (pgvector)
createdb_if_missing "backend_${TENANT}"
psql_admin "backend_${TENANT}" -c "CREATE EXTENSION IF NOT EXISTS vector;"
```
Use the script's existing admin-psql helper name/pattern; the point is: create the DB and run `CREATE EXTENSION IF NOT EXISTS vector` with a privileged role so the app user only needs table/DML rights. Set `KNOWLEDGE_DATABASE_URL` in the generated tenant env.

- [ ] **Step 4: Verify the compose config parses**

Run: `cd deploy && docker compose -f docker-compose.infra.yml config >/dev/null && echo OK`
Expected: `OK` (no YAML/interpolation errors).

- [ ] **Step 5: Commit**

```bash
git add deploy/docker-compose.infra.yml deploy/scripts/add-tenant.sh deploy/tenants/example.env
git commit -m "chore(deploy): pgvector Postgres image + backend KB DB provisioning"
```

- [ ] **Step 6: Proton-only VM rollout (manual, after merge)**

On the VM, for the **proton** tenant only: ensure the infra Postgres is the pgvector image (recreate the container if needed — data persists on the volume), run the provisioning for `backend_proton` + extension, set `KNOWLEDGE_PG_ENABLED=true` and `KNOWLEDGE_DATABASE_URL` in the proton tenant env, and restart the proton backend. Leave `default` and other tenants at `KNOWLEDGE_PG_ENABLED=false`. Verify by adding a document via the dashboard app and confirming the copilot grounds on it.

---

## Self-Review

**Spec coverage:**
- pgvector store in backend Postgres → Tasks 1, 5, 9. ✓
- Coexist with Live FAQ, merged at query → Task 9 (`pg_port` in `MergedKnowledgeAdapter`). ✓
- Vertex `text-embedding-004` embedder reused → Tasks 6–9 (`VertexEmbedder`, `EMBEDDING_DIM=768`). ✓
- Paste-text + file-upload ingestion (PDF/DOCX/MD/TXT) → Tasks 3, 7, 8. ✓
- No-code UI (Chatwoot dashboard app, content view; config view deferred) → Task 11. ✓
- Data model `kb_documents` + `kb_chunks` (vector(768), HNSW, cascade) → Task 5. ✓
- Fast-return + background ingestion; status badges → Tasks 8, 11. ✓
- Fail-open retrieval and ingestion → Tasks 6, 7, 9. ✓
- Auth reuse (`faq_admin_api_key`/`proton_backend_key`) → Task 8. ✓
- Config additions + score floor → Task 1. ✓
- Infra (pgvector image, add-tenant provisioning, env) + proton-only rollout → Task 12. ✓
- Testing: unit (sqlite/fakes) + one Postgres integration test → Tasks 2–10. ✓
- Deferred items untouched (provider picker, BYO-GCP, self-hosted embeddings, crawl, source selector, Live-FAQ migration). ✓

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". The one intentionally-open item — exact `main.py` insertion points (Task 9 Step 5) — is called out because it depends on the file's current lifespan structure; all code to insert is given verbatim.

**Type consistency:** `KbRepository` method names/signatures are identical across `InMemoryKbRepository` (Task 4), `PgKbRepository` (Task 5), and all consumers (Tasks 6–8). `ChunkHit{doc_title, content, score}` and `DocumentRow` fields are used consistently. `MergedKnowledgeAdapter(base, live_faq_store, embedder, pg_port=None)` matches the existing 3-arg call sites (backwards-compatible) and the new 4-arg wiring. `KbArticle(title, content, url, source_type)` matches the frozen dataclass. Embedding dim `768` is consistent between `EMBEDDING_DIM` and the integration test vectors.
