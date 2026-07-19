"""Copilot tool declarations (for Gemini function calling) + their executor.

The tools are read-only lookups. `conversation_id` is fixed per Copilot turn and
injected by the executor, so the model never has to pass it — this prevents the
model from probing arbitrary conversations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.features.assist.chatwoot_context import ChatwootContextClient
    from chatbot.features.chat.ports import KnowledgePort

COPILOT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_conversation_transcript",
        "description": (
            "Return the full transcript of the CURRENT conversation, including "
            "internal private notes (flagged private=true). Use to see what the "
            "customer said and what the team has already discussed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_contact_details",
        "description": (
            "Return the current customer's profile and custom attributes "
            "(e.g. plan, customer type, tags)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_past_conversations",
        "description": (
            "List this customer's OTHER (past) conversations with status and "
            "labels. Use to check history before answering."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_knowledge_base",
        "description": "Search the product/FAQ knowledge base for grounding facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
                "limit": {"type": "integer", "description": "max hits (1-10)"},
            },
            "required": ["query"],
        },
    },
]


class ToolExecutor:
    def __init__(
        self,
        context: ChatwootContextClient,
        knowledge: KnowledgePort,
        conversation_id: str,
    ) -> None:
        self._ctx = context
        self._kb = knowledge
        self._conv_id = conversation_id

    async def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_conversation_transcript":
            return {"turns": await self._ctx.get_transcript(self._conv_id)}
        if name == "get_contact_details":
            return {"contact": await self._ctx.get_contact(self._conv_id)}
        if name == "list_past_conversations":
            return {"conversations": await self._ctx.list_contact_conversations(self._conv_id)}
        if name == "search_knowledge_base":
            query = str(args.get("query", "")).strip()
            limit = min(max(int(args.get("limit", 3)), 1), 10)
            articles = await self._kb.search_kb(query, limit)
            return {
                "articles": [
                    {"title": a.title, "content": a.content[:500], "url": a.url}
                    for a in articles
                ]
            }
        return {"error": f"unknown tool: {name}"}
