"""POST /assist/copilot — multi-turn, tool-using Ask Copilot.

Stateless: the frontend owns the chat thread and sends it each turn. The server
runs a bounded Gemini function-calling loop whose tools are read-only Chatwoot
lookups + KB search. Auth mirrors /assist/*.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.assist.chatwoot_context import ChatwootContextClient
from chatbot.features.assist.copilot_tools import COPILOT_TOOLS, ToolExecutor

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_SYSTEM = (
    "You are Copilot, an assistant for a Proton Holdings SUPPORT AGENT (not the "
    "customer). Help the agent by answering their questions about the current "
    "conversation and customer. Use the provided tools to read the transcript "
    "(including private notes), the contact's details and custom attributes, the "
    "customer's past conversations, and the knowledge base before answering. "
    "Be concise and factual; if the tools don't contain the answer, say so. "
    "Reply in the language the agent used."
)

_FALLBACK = "I couldn't complete that — try rephrasing your question."


class ThreadMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1)


class CopilotRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    thread: list[ThreadMessage] = Field(min_length=1)


def build_copilot_router(
    settings: Settings,
    knowledge_port: KnowledgePort,
    genai_client: Any,
) -> APIRouter:
    router = APIRouter(prefix="/assist", tags=["copilot"])

    def _authorize(x_api_key: str | None) -> None:
        key = settings.proton_backend_key
        if not key:
            raise HTTPException(status_code=503, detail="Copilot not configured")
        if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), key.encode()):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def _seed_contents(thread: list[ThreadMessage]) -> list[dict[str, Any]]:
        # google-genai content format: role "user"/"model", parts=[{"text": ...}]
        return [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in thread
        ]

    @router.post("/copilot")
    async def copilot(
        req: CopilotRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_copilot", conv_id=req.conversation_id, turns=len(req.thread))

        executor = ToolExecutor(
            ChatwootContextClient(settings), knowledge_port, req.conversation_id
        )
        contents = _seed_contents(req.thread)
        tool_calls: list[str] = []
        config = {
            "system_instruction": _SYSTEM,
            "tools": [{"function_declarations": COPILOT_TOOLS}],
        }

        last_text = ""
        sources: list[dict] = []
        seen_src: set[tuple] = set()
        for _ in range(settings.copilot_max_tool_iterations):
            response = await genai_client.aio.models.generate_content(
                model=settings.copilot_gemini_model,
                contents=contents,
                config=config,
            )
            # Capture text first — SDK can return text alongside function calls.
            text = (getattr(response, "text", None) or "").strip()
            if text:
                last_text = text
            calls = getattr(response, "function_calls", None) or []
            if not calls:
                return {"answer": last_text or _FALLBACK, "tool_calls": tool_calls, "sources": sources}

            # Execute each requested tool and feed the results back.
            contents.append(
                {
                    "role": "model",
                    "parts": [
                        {"function_call": {"name": c.name, "args": dict(c.args or {})}}
                        for c in calls
                    ],
                }
            )
            tool_parts = []
            for c in calls:
                tool_calls.append(c.name)
                result = await executor.run(c.name, dict(c.args or {}))
                if c.name == "search_knowledge_base":
                    for a in result.get("articles", []):
                        key = (a.get("title"), a.get("url"))
                        if key in seen_src:
                            continue
                        seen_src.add(key)
                        sources.append({"title": a.get("title"), "snippet": a.get("content", ""), "url": a.get("url")})
                tool_parts.append(
                    {"function_response": {"name": c.name, "response": result}}
                )
            contents.append({"role": "user", "parts": tool_parts})

        # Cap reached without a final text answer.
        return {"answer": last_text or _FALLBACK, "tool_calls": tool_calls, "sources": sources}

    return router
