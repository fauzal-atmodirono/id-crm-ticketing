"""KnowledgePort that merges CRM-authored live-FAQ entries with the batch KB,
so /assist/* and Ask Copilot ground on freshly-authored knowledge immediately.
Live-FAQ hits rank first; dedup by lowercased title; never raises (falls back to
the base KB if the live store/embedder is missing or errors). Mirrors kb_suggest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.nlu_normalise import NORMALISE_RETRIEVAL_QUERY_ENABLED, normalise

if TYPE_CHECKING:
    pass

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

    async def _live_articles(
        self, query: str, limit: int, *, keyword_query: str | None = None
    ) -> list[KbArticle]:
        """`query` is embedded; `keyword_query` is matched against authored
        keywords. They differ when normalisation is on — see `search_kb`, and the
        `LiveFaqPort.search` docstring for why keyword matching must see the raw
        string rather than the normalised one.
        """
        if self._live is None or self._embedder is None:
            return []
        try:
            emb = await self._embedder.embed(query)
            if not emb:
                return []
            hits = await self._live.search(
                emb, limit, query_text=keyword_query if keyword_query is not None else query
            )
        except Exception as e:  # never raise into grounding
            _log.error("merged_live_faq_search_failed", error=str(e))
            return []
        return [
            KbArticle(title=e.question, content=e.answer, url=None, source_type="live_faq")
            for e, _score in hits
        ]

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        # P7 task 6 -- the single production application point for the
        # SMS-register-Malay query normaliser (see nlu_normalise.py). Only
        # the copy fed to the two purely-cosine, embedding-driven branches
        # (pg + live-FAQ) is normalised; `base` always gets the untouched
        # `query`. That split matters: `base` is the Vertex-Search-backed
        # adapter, out of this task's scope, which may fold in its own
        # keyword/exact-match signal a normalised string would silently
        # break (e.g. "brp" -> "berapa" must never risk turning an exact
        # product code like "e.MAS7" into something that stops matching).
        # `NORMALISE_RETRIEVAL_QUERY_ENABLED` defaults False -- flipping it
        # is conditional on the real-credential corpus re-run recorded in
        # docs/analysis/2026-08-09-blocked-work-register.md.
        retrieval_query = normalise(query) if NORMALISE_RETRIEVAL_QUERY_ENABLED else query
        pg = await self._pg_articles(retrieval_query, limit)
        # The embedding gets the (possibly normalised) retrieval copy; the
        # authored-keywords signal gets the untouched `query`, for the same
        # reason `base` does — normalising "e.MAS7" is how an exact product-code
        # match stops matching.
        live = await self._live_articles(retrieval_query, limit, keyword_query=query)
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
