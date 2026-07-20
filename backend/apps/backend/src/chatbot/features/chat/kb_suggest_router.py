"""GET /kb/suggest — real-time FAQ suggestions for agents.

Merges two sources into a single `suggestions` list (shape unchanged so the
existing Chatwoot agent-assist panel renders them as-is):

1. CRM-authored **live FAQ** entries, matched by MEANING — the query is embedded
   once and cosine-ranked against the active live-FAQ set (`source_type:
   "live_faq"`). These rank FIRST so a freshly-edited answer surfaces on top.
2. The existing batch-indexed Vertex AI Search KB (`KnowledgePort`).

If the live store or its embedding is unavailable the endpoint falls back to
Vertex-only and never errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.live_faq import Embedder
    from chatbot.features.chat.ports import KnowledgePort, LiveFaqPort

_log = structlog.get_logger(__name__)

_SNIPPET = 280


def build_kb_suggest_router(
    knowledge_port: KnowledgePort,
    live_faq_store: LiveFaqPort | None = None,
    embedder: Embedder | None = None,
) -> APIRouter:
    router = APIRouter(tags=["kb"])

    async def _live_faq_suggestions(query: str, limit: int) -> list[dict[str, Any]]:
        """Semantic live-FAQ hits, or [] if the store/embedder is unavailable."""
        if live_faq_store is None or embedder is None:
            return []
        try:
            query_embedding = await embedder.embed(query)
            if not query_embedding:
                return []
            hits = await live_faq_store.search(query_embedding, limit)
        except Exception as e:
            _log.error("live_faq_suggest_failed", query=query, error=str(e))
            return []
        return [
            {
                "title": entry.question,
                "snippet": entry.answer[:_SNIPPET],
                "url": None,
                "source_type": "live_faq",
                "score": round(score, 4),
            }
            for entry, score in hits
        ]

    @router.get("/kb/suggest")
    async def suggest(q: str = "", limit: int = 3) -> dict[str, Any]:
        query = q.strip()
        if not query:
            return {"query": "", "suggestions": []}

        # Live FAQ (semantic) first, then Vertex Search. Live hits rank ahead so
        # CRM edits win; Vertex fills the remaining slots.
        live_suggestions = await _live_faq_suggestions(query, limit)

        articles = await knowledge_port.search_kb(query, limit)
        vertex_suggestions = [
            {
                "title": a.title,
                "snippet": a.content[:_SNIPPET],
                "url": a.url,
                "source_type": a.source_type,
            }
            for a in articles
        ]

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*live_suggestions, *vertex_suggestions]:
            key = (item.get("title") or "").strip().lower()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)

        return {"query": query, "suggestions": merged[:limit]}

    return router
