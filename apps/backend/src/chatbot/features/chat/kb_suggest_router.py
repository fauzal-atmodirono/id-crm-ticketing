"""GET /kb/suggest — real-time FAQ suggestions for agents (reuses KnowledgePort)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort

_SNIPPET = 280


def build_kb_suggest_router(knowledge_port: KnowledgePort) -> APIRouter:
    router = APIRouter(tags=["kb"])

    @router.get("/kb/suggest")
    async def suggest(q: str = "", limit: int = 3) -> dict[str, Any]:
        query = q.strip()
        if not query:
            return {"query": "", "suggestions": []}
        articles = await knowledge_port.search_kb(query, limit)
        return {
            "query": query,
            "suggestions": [
                {
                    "title": a.title,
                    "snippet": a.content[:_SNIPPET],
                    "url": a.url,
                    "source_type": a.source_type,
                }
                for a in articles
            ],
        }

    return router
