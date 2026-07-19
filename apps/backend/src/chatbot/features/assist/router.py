"""POST /assist/suggest|summarize|ask — Proton AI-assist endpoints.

Called by the patched Chatwoot frontend (patch 0002). All endpoints require
`x-api-key` matching `Settings.proton_backend_key`; an empty key returns 503
so misconfigured deployments fail loudly rather than leaving the endpoint open.

Request shape (all three endpoints share a base):
  - conversation_id : str   — Chatwoot conversation id (for logging)
  - messages        : list[str] — conversation turns, most-recent last
  - question        : str   — only for /ask

/suggest also accepts:
  - limit : int (default 3) — max KB hits to ground the reply
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_SUGGEST_SYSTEM = (
    "You are a customer-support agent for Proton Holdings. "
    "Given the conversation history and the relevant FAQ context below, "
    "write a concise, professional reply to the customer's latest message. "
    "LANGUAGE (critical): reply in the EXACT SAME language as the customer's "
    "latest message. If they wrote in English, reply in English; if in Malay, "
    "reply in Malay. Never switch languages and never default to Malay when the "
    "customer wrote in another language. "
    "Do not include a salutation or sign-off. "
    "Return only the reply text, nothing else.\n\n"
    "FAQ context:\n{faq_context}"
)

_SUMMARIZE_SYSTEM = (
    "You are a customer-support supervisor. "
    "Summarise the following conversation between a customer and a support agent "
    "in 3–5 bullet points. Focus on: the customer's issue, steps taken, and current status. "
    "Reply in English regardless of the conversation language."
)

_ASK_SYSTEM = (
    "You are a customer-support knowledge assistant. "
    "Answer the agent's question using the conversation history and the FAQ context below. "
    "Be concise. If the answer is not in the context, say so clearly.\n\n"
    "FAQ context:\n{faq_context}"
)

_SNIPPET = 300


class AssistBase(BaseModel):
    conversation_id: str = Field(min_length=1)
    messages: list[str] = Field(min_length=1)


class SuggestRequest(AssistBase):
    limit: int = Field(default=3, ge=1, le=10)


class SummarizeRequest(AssistBase):
    pass


class AskRequest(AssistBase):
    question: str = Field(min_length=1)


def build_assist_router(
    settings: Settings,
    knowledge_port: KnowledgePort,
    genai_client: Any,
) -> APIRouter:
    """Return a FastAPI router with three /assist/* endpoints.

    Args:
        settings: application settings (reads proton_backend_key + assist_gemini_model).
        knowledge_port: KB search port (for /suggest and /ask grounding).
        genai_client: a google.genai.Client instance (or stub in tests).
    """
    router = APIRouter(prefix="/assist", tags=["assist"])

    def _authorize(x_api_key: str | None) -> None:
        key = settings.proton_backend_key
        if not key:
            raise HTTPException(status_code=503, detail="Assist endpoints not configured")
        if (
            x_api_key is None
            or not hmac.compare_digest(x_api_key.encode(), key.encode())
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    async def _generate(system: str, user_prompt: str) -> str:
        model = settings.assist_gemini_model
        response = await genai_client.aio.models.generate_content(
            model=model,
            contents=user_prompt,
            config={"system_instruction": system},
        )
        return (response.text or "").strip()

    def _format_messages(messages: list[str]) -> str:
        return "\n".join(f"[{i+1}] {m}" for i, m in enumerate(messages))

    async def _kb_context(query: str, limit: int) -> tuple[str, list[dict[str, Any]]]:
        articles = await knowledge_port.search_kb(query, limit)
        sources = [
            {
                "title": a.title,
                "snippet": a.content[:_SNIPPET],
                "url": a.url,
            }
            for a in articles
        ]
        faq_context = "\n---\n".join(
            f"Q: {a.title}\nA: {a.content[:_SNIPPET]}" for a in articles
        )
        return faq_context, sources

    @router.post("/suggest")
    async def suggest(
        req: SuggestRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_suggest", conv_id=req.conversation_id)
        query = req.messages[-1]  # ground on the customer's latest message
        faq_context, sources = await _kb_context(query, req.limit)
        system = _SUGGEST_SYSTEM.format(faq_context=faq_context or "(none)")
        user_prompt = _format_messages(req.messages)
        draft = await _generate(system, user_prompt)
        return {"draft": draft, "sources": sources}

    @router.post("/summarize")
    async def summarize(
        req: SummarizeRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_summarize", conv_id=req.conversation_id)
        user_prompt = _format_messages(req.messages)
        summary = await _generate(_SUMMARIZE_SYSTEM, user_prompt)
        return {"summary": summary}

    @router.post("/ask")
    async def ask(
        req: AskRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_ask", conv_id=req.conversation_id, question=req.question)
        faq_context, _ = await _kb_context(req.question, limit=3)
        system = _ASK_SYSTEM.format(faq_context=faq_context or "(none)")
        user_prompt = (
            f"Conversation:\n{_format_messages(req.messages)}\n\n"
            f"Agent question: {req.question}"
        )
        answer = await _generate(system, user_prompt)
        return {"answer": answer}

    return router
