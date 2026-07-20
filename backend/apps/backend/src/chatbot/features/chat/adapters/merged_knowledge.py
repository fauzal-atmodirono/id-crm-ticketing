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
    def __init__(self, base, live_faq_store, embedder) -> None:
        self._base = base
        self._live = live_faq_store
        self._embedder = embedder

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
        live = await self._live_articles(query, limit)
        base = await self._base.search_kb(query, limit)
        merged: list[KbArticle] = []
        seen: set[str] = set()
        for a in [*live, *base]:
            key = (a.title or "").strip().lower()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(a)
        return merged[:limit]
