"""KnowledgePort that merges CRM-authored live-FAQ entries with the batch KB,
so /assist/* and Ask Copilot ground on freshly-authored knowledge immediately.
Live-FAQ hits rank first; dedup by lowercased title; never raises (falls back to
the base KB if the live store/embedder is missing or errors). Mirrors kb_suggest.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chatbot.features.chat.models import KbArticle

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.live_faq import Embedder
    from chatbot.features.chat.ports import KnowledgePort, LiveFaqPort

_log = structlog.get_logger(__name__)


class MergedKnowledgeAdapter:
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

    async def _live_articles(self, query: str, limit: int) -> list[KbArticle]:
        if self._live is None or self._embedder is None:
            return []
        try:
            emb = await self._embedder.embed(query)
            if not emb:
                return []
            hits = await self._live.search(emb, limit)
        except Exception as e:  # never raise into grounding
            _log.error("merged_live_faq_search_failed", error=str(e))
            return []
        return [
            KbArticle(title=e.question, content=e.answer, url=None, source_type="live_faq")
            for e, _score in hits
        ]

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        pg = await self._pg_articles(query, limit)
        live = await self._live_articles(query, limit)
        base = await self._base.search_kb(query, limit)

        # pg/live intentionally rank first (freshly operator-authored
        # knowledge), but must never consume the ENTIRE limit when a
        # relevant base-KB result exists — that's the "Copilot can't find
        # things the main agent can" bug (the main agent queries base/Vertex
        # directly, unaffected by this merge). Reserve at least half of
        # `limit` (rounded up, min 1) for base.
        base_reserved = max(1, -(-limit // 2))  # ceil(limit / 2)
        priority_budget = max(0, limit - base_reserved)

        merged: list[KbArticle] = []
        seen: set[str] = set()

        def _add(article: KbArticle) -> bool:
            key = (article.title or "").strip().lower()
            if key and key in seen:
                return False
            if key:
                seen.add(key)
            merged.append(article)
            return True

        priority_added = 0
        for a in [*pg, *live]:
            if priority_added >= priority_budget:
                break
            if _add(a):
                priority_added += 1

        for a in base:
            if len(merged) >= limit:
                break
            _add(a)

        # If base didn't have enough results to fill its reserved slots, top
        # up with any remaining pg/live hits beyond the initial budget.
        if len(merged) < limit:
            for a in [*pg, *live]:
                if len(merged) >= limit:
                    break
                _add(a)

        return merged[:limit]
