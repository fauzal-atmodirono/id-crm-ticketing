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
import re
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


# Punctuation/whitespace stripped before comparing a keyword against the raw
# query -- comparison-only, never applied to `query_text` itself or to any
# value `_rank` returns (see `_keyword_hit` docstring).
_KEYWORD_MATCH_STRIP_RE = re.compile(r"[^\w]")


def _normalize_for_keyword_match(text: str) -> str:
    return _KEYWORD_MATCH_STRIP_RE.sub("", text.lower())


def _keyword_hit(query_text: str | None, keywords: list[str]) -> bool:
    """True if any authored keyword appears in the query text.

    Both sides are lower-cased and stripped of punctuation before comparing,
    so an authored keyword like "E.MAS7" matches a customer's "e.mas7" or
    "emas7" -- a real product code that embeds badly (poor cosine similarity)
    but should still match exactly, regardless of case or stray punctuation.
    This normalisation is scoped entirely to this comparison: it never
    mutates `query_text` and nothing built from it is returned by `_rank` --
    the retrieval query itself is a separate concern owned by a sibling
    task's normaliser.
    """
    if not query_text or not keywords:
        return False
    normalized_query = _normalize_for_keyword_match(query_text)
    if not normalized_query:
        return False
    return any(
        (normalized_keyword := _normalize_for_keyword_match(keyword))
        and normalized_keyword in normalized_query
        for keyword in keywords
    )


def _rank(
    entries: list[LiveFaqEntry],
    query_embedding: list[float],
    limit: int,
    *,
    query_text: str | None = None,
    keyword_weight: float = 0.0,
) -> list[tuple[LiveFaqEntry, float]]:
    """Score entries by cosine similarity blended with a keyword-hit bonus,
    drop sub-floor hits, return top-N.

    Blend: `score = semantic + keyword_weight * (1.0 if hit else 0.0)`, an
    additive bonus rather than an interpolation like
    `semantic * (1 - w) + keyword * w`. That choice is load-bearing for two
    properties the package treats as safety requirements:

    - `keyword_weight=0.0` must reproduce today's score for every entry
      exactly, and an additive `+ 0.0` leaves the cosine value bit-for-bit
      untouched (an interpolation would too, at w=0, but see the next point).
    - an entry with no authored `keywords` (so `_keyword_hit` is always
      `False` for it) must be *completely* unaffected by the weight, at any
      value. An additive bonus guarantees this: `keyword_weight * 0.0 == 0.0`
      regardless of `keyword_weight`. An interpolated blend fails this --
      `semantic * (1 - w)` shrinks a keyword-less entry's score as soon as
      `w > 0`, even though that entry never engaged the keyword signal.

    `query_text=None` (a caller with an embedding but no raw query string --
    not every caller has one) degrades to pure semantic ranking: `_keyword_hit`
    is `False` for every entry, so scores and ordering are identical to a bare
    cosine rank regardless of `keyword_weight`.
    """
    scored = [
        (
            e,
            cosine_similarity(query_embedding, e.embedding)
            + (keyword_weight if _keyword_hit(query_text, e.keywords) else 0.0),
        )
        for e in entries
    ]
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
        self._keyword_weight = settings.faq_keyword_weight
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
        self,
        query_embedding: list[float],
        limit: int,
        *,
        query_text: str | None = None,
    ) -> list[tuple[LiveFaqEntry, float]]:
        if not query_embedding:
            return []
        # `keyword_weight` is sourced from `settings.faq_keyword_weight` at
        # construction time (see `__init__`) -- never hardcoded here.
        #
        # `query_text` must be threaded from the caller or the whole tunable is
        # inert: `_rank` can only score the authored `keywords` field against a
        # real string, so an unset `query_text` makes `_keyword_hit` False for
        # every entry at every weight, and raising `FAQ_KEYWORD_WEIGHT` changes
        # nothing at all. That was the shipped state until the P7 final review
        # caught it -- the unit test passed the weight into `_rank` directly and
        # so never exercised this path. Callers pass the RAW query here; see the
        # port docstring for why it must not be the normalised copy.
        return _rank(
            await self.list_active(),
            query_embedding,
            limit,
            query_text=query_text,
            keyword_weight=self._keyword_weight,
        )


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
