"""The kb_search function tool the Live model calls for KB grounding."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from google.genai import types

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort

_log = structlog.get_logger(__name__)

KB_SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="kb_search",
            description="Search the Proton knowledge base for facts to answer the caller.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="The caller's question or keywords to look up.",
                    )
                },
                required=["query"],
            ),
        )
    ]
)


async def dispatch_kb_search(
    args: dict[str, object], knowledge_port: KnowledgePort, *, timeout_seconds: float = 0.0
) -> dict[str, Any]:
    """Run kb_search and shape the result for send_tool_response.

    Never raises, and never waits past `timeout_seconds` when it is > 0
    (0 = no cap, the pre-2026-11-16 behaviour): a lookup that is slow or
    failing comes back as no results with a "timeout"/"error" status, so the
    model answers without the KB instead of the caller hearing silence.
    """
    query = str(args.get("query") or "").strip()
    if not query:
        return {"results": []}
    search = knowledge_port.search_kb(query, limit=3)
    try:
        if timeout_seconds > 0:
            articles = await asyncio.wait_for(search, timeout=timeout_seconds)
        else:
            articles = await search
    except TimeoutError:
        return {"results": [], "status": "timeout"}
    except Exception as exc:
        _log.warning("phone_kb_search_failed", error_type=type(exc).__name__)
        return {"results": [], "status": "error"}
    return {"results": [{"title": a.title, "content": a.content, "url": a.url} for a in articles]}
