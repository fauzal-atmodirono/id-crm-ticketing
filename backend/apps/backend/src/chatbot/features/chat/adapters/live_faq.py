"""Real-time, CRM-editable FAQ store with Vertex embeddings + cosine search.

The CRM team authors FAQ entries (question/answer/keywords/tags) that must be
matchable by MEANING during an agent↔customer chat. Each write embeds the entry
text (question + " " + answer) via a Vertex text-embedding model and stores the
vector on the Firestore doc. `/kb/suggest` embeds the live query once and ranks
active entries by cosine similarity — an in-memory scan is fine because the
CRM-authored set is small and edits must reflect INSTANTLY.

Design notes:
- The embedding call sits behind a small `Embedder` callable so unit tests mock
  it instead of hitting Vertex.
- `search` reads active entries fresh (a short cache is optional but correctness
  and real-time behaviour beat caching), so a CRM edit shows up on the next
  suggest without any reindex step.
- All Firestore work degrades cleanly: if the SDK/collection is unavailable the
  store logs and returns empty rather than breaking `/kb/suggest`.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

from chatbot.features.chat.ports import LiveFaqEntry

if TYPE_CHECKING:
    from google.genai import Client

    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Cosine scores below this are treated as "not a real semantic match" and
# dropped, so a query with no relevant FAQ returns nothing rather than noise.
_SCORE_FLOOR = 0.55


@runtime_checkable
class Embedder(Protocol):
    """Injectable text->vector embedder (mocked in tests, Vertex in prod)."""

    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for `text` (empty list on failure)."""
        ...


class VertexEmbedder(Embedder):
    """Embeds text through the google-genai client (ADC / Vertex).

    Uses `client.models.embed_content(model=..., contents=...)`. The genai SDK
    is synchronous, so the call is dispatched to a worker thread to keep the
    event loop unblocked. Any failure returns an empty vector so callers can
    fall back to Vertex-Search-only suggestions.
    """

    def __init__(self, client: Client, model: str) -> None:
        self._client = client
        self._model = model

    async def embed(self, text: str) -> list[float]:
        clean = text.strip()
        if not clean:
            return []

        def _run() -> list[float]:
            response = self._client.models.embed_content(model=self._model, contents=clean)
            embeddings = response.embeddings or []
            if not embeddings:
                return []
            values = embeddings[0].values
            return list(values) if values else []

        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            _log.error("live_faq_embed_failed", model=self._model, error=str(e))
            return []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. Returns 0.0 for empty or
    mismatched-length inputs (so an un-embedded entry never spuriously matches)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _embed_text(entry: LiveFaqEntry) -> str:
    """The text an entry embeds on: question + " " + answer."""
    return f"{entry.question} {entry.answer}".strip()


def _rank(
    entries: list[LiveFaqEntry], query_embedding: list[float], limit: int
) -> list[tuple[LiveFaqEntry, float]]:
    """Score entries by cosine similarity, drop sub-floor hits, return top-N."""
    scored = [(e, cosine_similarity(query_embedding, e.embedding)) for e in entries]
    scored = [pair for pair in scored if pair[1] >= _SCORE_FLOOR]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[: max(limit, 0)]


class InMemoryLiveFaqStore:
    """Volatile live-FAQ store for tests/dev. Embeddings via injected Embedder."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._entries: dict[str, LiveFaqEntry] = {}

    async def create(self, entry: LiveFaqEntry) -> str:
        entry_id = entry.id or uuid.uuid4().hex
        embedding = await self._embedder.embed(_embed_text(entry))
        stored = LiveFaqEntry(
            id=entry_id,
            question=entry.question,
            answer=entry.answer,
            keywords=list(entry.keywords),
            tags=list(entry.tags),
            embedding=embedding,
            active=entry.active,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._entries[entry_id] = stored
        return entry_id

    async def update(self, entry_id: str, fields: dict[str, Any]) -> None:
        current = self._entries.get(entry_id)
        if current is None:
            return
        question = fields.get("question", current.question)
        answer = fields.get("answer", current.answer)
        keywords = fields.get("keywords", current.keywords)
        tags = fields.get("tags", current.tags)
        active = fields.get("active", current.active)
        embedding = current.embedding
        if "question" in fields or "answer" in fields:
            embedding = await self._embedder.embed(
                _embed_text(
                    LiveFaqEntry(id=entry_id, question=question, answer=answer),
                )
            )
        self._entries[entry_id] = LiveFaqEntry(
            id=entry_id,
            question=question,
            answer=answer,
            keywords=list(keywords),
            tags=list(tags),
            embedding=embedding,
            active=active,
            updated_at=datetime.now(UTC).isoformat(),
        )

    async def delete(self, entry_id: str) -> None:
        self._entries.pop(entry_id, None)

    async def list_all(self) -> list[LiveFaqEntry]:
        return list(self._entries.values())

    async def list_active(self) -> list[LiveFaqEntry]:
        return [e for e in self._entries.values() if e.active]

    async def search(
        self, query_embedding: list[float], limit: int
    ) -> list[tuple[LiveFaqEntry, float]]:
        if not query_embedding:
            return []
        return _rank(await self.list_active(), query_embedding, limit)


class FirestoreLiveFaqStore:
    """Firestore-backed live-FAQ store.

    Each entry is one document in `<collection>/<id>` carrying question, answer,
    keywords, tags, the precomputed `embedding` vector, `active`, and
    `updated_at`. Writes (create/update) recompute the embedding via the
    injected `Embedder`; `search` reads active entries fresh and ranks them
    in-memory by cosine similarity — no Firestore vector index required.

    The Firestore SDK is synchronous; calls run in a worker thread. Every read
    path degrades to empty on failure so `/kb/suggest` never breaks.
    """

    def __init__(self, settings: Settings, embedder: Embedder) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._embedder = embedder
        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = settings.live_faq_collection
        _log.info(
            "firestore_live_faq_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
            collection=self._collection_name,
        )

    def _collection(self) -> Any:
        return self._client.collection(self._collection_name)

    @staticmethod
    def _to_entry(doc_id: str, data: dict[str, Any]) -> LiveFaqEntry:
        return LiveFaqEntry(
            id=doc_id,
            question=str(data.get("question", "")),
            answer=str(data.get("answer", "")),
            keywords=[str(k) for k in (data.get("keywords") or [])],
            tags=[str(t) for t in (data.get("tags") or [])],
            embedding=[float(v) for v in (data.get("embedding") or [])],
            active=bool(data.get("active", True)),
            updated_at=str(data.get("updated_at", "")),
        )

    async def create(self, entry: LiveFaqEntry) -> str:
        entry_id = entry.id or uuid.uuid4().hex
        embedding = await self._embedder.embed(_embed_text(entry))
        doc_data = {
            "question": entry.question,
            "answer": entry.answer,
            "keywords": list(entry.keywords),
            "tags": list(entry.tags),
            "embedding": embedding,
            "active": entry.active,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        def _write() -> None:
            self._collection().document(entry_id).set(doc_data)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("live_faq_create_failed", entry_id=entry_id, error=str(e))
        return entry_id

    async def update(self, entry_id: str, fields: dict[str, Any]) -> None:
        def _read() -> dict[str, Any] | None:
            snap = self._collection().document(entry_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            return data if isinstance(data, dict) else None

        try:
            current = await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("live_faq_update_read_failed", entry_id=entry_id, error=str(e))
            return
        if current is None:
            return

        patch: dict[str, Any] = {}
        for key in ("question", "answer", "keywords", "tags", "active"):
            if key in fields:
                patch[key] = fields[key]
        if "question" in fields or "answer" in fields:
            question = fields.get("question", current.get("question", ""))
            answer = fields.get("answer", current.get("answer", ""))
            patch["embedding"] = await self._embedder.embed(f"{question} {answer}".strip())
        patch["updated_at"] = datetime.now(UTC).isoformat()

        def _write() -> None:
            self._collection().document(entry_id).update(patch)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("live_faq_update_failed", entry_id=entry_id, error=str(e))

    async def delete(self, entry_id: str) -> None:
        def _delete() -> None:
            self._collection().document(entry_id).delete()

        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            _log.error("live_faq_delete_failed", entry_id=entry_id, error=str(e))

    async def list_all(self) -> list[LiveFaqEntry]:
        def _read() -> list[LiveFaqEntry]:
            return [
                self._to_entry(doc.id, doc.to_dict() or {}) for doc in self._collection().stream()
            ]

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("live_faq_list_all_failed", error=str(e))
            return []

    async def list_active(self) -> list[LiveFaqEntry]:
        def _read() -> list[LiveFaqEntry]:
            docs = self._collection().where("active", "==", True).stream()
            return [self._to_entry(doc.id, doc.to_dict() or {}) for doc in docs]

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("live_faq_list_active_failed", error=str(e))
            return []

    async def search(
        self, query_embedding: list[float], limit: int
    ) -> list[tuple[LiveFaqEntry, float]]:
        if not query_embedding:
            return []
        return _rank(await self.list_active(), query_embedding, limit)


def build_live_faq_store(settings: Settings, client: Client | None) -> Any:
    """Construct the FirestoreLiveFaqStore with a Vertex embedder.

    Returns None when Firestore can't be initialised (so wiring falls back to
    Vertex-Search-only suggestions and never breaks boot). Requires a genai
    client for embeddings; without one there is no way to embed, so returns None.
    """
    if client is None:
        _log.info("live_faq_store_disabled_no_genai_client")
        return None
    embedder = VertexEmbedder(client, settings.embedding_model)
    try:
        return FirestoreLiveFaqStore(settings, embedder)
    except Exception as e:
        _log.warning("firestore_live_faq_store_init_failed", error=str(e))
        return None
