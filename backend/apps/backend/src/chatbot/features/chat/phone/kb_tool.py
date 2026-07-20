"""The kb_search function tool the Live model calls for KB grounding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.genai import types

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort

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
    args: dict[str, object], knowledge_port: KnowledgePort
) -> dict[str, Any]:
    """Run kb_search and shape the result for send_tool_response."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"results": []}
    articles = await knowledge_port.search_kb(query, limit=3)
    return {"results": [{"title": a.title, "content": a.content, "url": a.url} for a in articles]}
