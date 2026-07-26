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
