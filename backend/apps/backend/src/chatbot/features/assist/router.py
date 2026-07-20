"""POST /assist/suggest|summarize|ask — Proton AI-assist endpoints.

Called by the patched Chatwoot frontend (patch 0002). All endpoints require
`x-api-key` matching `Settings.proton_backend_key`; an empty key returns 503
so misconfigured deployments fail loudly rather than leaving the endpoint open.

Request shape (all three endpoints share a base):
  - conversation_id : str          — Chatwoot conversation id (for logging)
  - messages        : list[str]    — conversation turns, most-recent last
  - assistant_id    : str | None   — optional; steers output via product_name +
                                     guardrails when assistants_store is provided
  - question        : str          — only for /ask

/suggest also accepts:
  - limit : int (default 3) — max KB hits to ground the reply

Tenant overrides
----------------
When `tenant_settings_store` is passed to `build_assist_router`, the Gemini
model used for each call is resolved via `get_effective_value` so the
`/kb/settings` `assist_gemini_model` override is honoured.  When the store is
None (default, test call-sites), the router falls back to
`settings.assist_gemini_model` — preserving existing behaviour exactly.

Persona prefix
--------------
When `assistants_store` is provided and `req.assistant_id` is set (or falls
back to the default assistant), a light persona prefix built from
`product_name` + `guardrails` is prepended to the task system prompt.  The
full copilot instructions/scenarios are intentionally NOT injected — the
task-specific prompt stays authoritative.  An empty product_name and empty
guardrails list yields an empty prefix → behaviour-preserving.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.assistants_store import AssistantsStorePort
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
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
    assistant_id: str | None = None


class SuggestRequest(AssistBase):
    limit: int = Field(default=3, ge=1, le=10)


class SummarizeRequest(AssistBase):
    pass


class AskRequest(AssistBase):
    question: str = Field(min_length=1)


def _build_persona_prefix(product_name: str, guardrails: list[str]) -> str:
    """Build a brief persona prefix from product_name and guardrails only.

    Returns an empty string when both are absent so the task prompt is
    unchanged — behaviour-preserving for the no-assistant / empty-assistant path.
    """
    parts: list[str] = []
    if product_name:
        parts.append(f"Product: {product_name}")
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    return "\n".join(parts)


def build_assist_router(
    settings: Settings,
    knowledge_port: KnowledgePort,
    genai_client: Any,
    assistants_store: AssistantsStorePort | None = None,
    tenant_settings_store: TenantSettingsStorePort | None = None,
) -> APIRouter:
    """Return a FastAPI router with three /assist/* endpoints.

    Args:
        settings: application settings (reads proton_backend_key + assist_gemini_model).
        knowledge_port: KB search port (for /suggest and /ask grounding).
        genai_client: a google.genai.Client instance (or stub in tests).
        assistants_store: optional store for assistant persona lookups.  When
            None, assistant_id in requests is silently ignored.
        tenant_settings_store: optional store for tenant setting overrides.
            When None, settings.assist_gemini_model is used directly (unchanged
            fallback — existing test call-sites remain unaffected).
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

    async def _resolve_model() -> str:
        if tenant_settings_store is not None:
            from chatbot.features.chat.settings_facade import get_effective_value  # noqa: PLC0415
            return await get_effective_value(tenant_settings_store, settings, "assist_gemini_model")
        return settings.assist_gemini_model

    async def _resolve_persona_prefix(assistant_id: str | None) -> str:
        if assistants_store is None:
            return ""
        from chatbot.features.assist.assistant_runtime import resolve_assistant  # noqa: PLC0415
        assistant = await resolve_assistant(assistants_store, assistant_id)
        return _build_persona_prefix(assistant.product_name, assistant.config.guardrails)

    async def _generate(system: str, user_prompt: str) -> str:
        model = await _resolve_model()
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

    async def _apply_persona(task_system: str, assistant_id: str | None) -> str:
        """Prepend persona prefix (product_name + guardrails) to the task system prompt.

        Returns the original task_system unchanged when the prefix is empty.
        """
        prefix = await _resolve_persona_prefix(assistant_id)
        if prefix:
            return prefix + "\n\n" + task_system
        return task_system

    @router.post("/suggest")
    async def suggest(
        req: SuggestRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_suggest", conv_id=req.conversation_id)
        query = req.messages[-1]  # ground on the customer's latest message
        faq_context, sources = await _kb_context(query, req.limit)
        task_system = _SUGGEST_SYSTEM.format(faq_context=faq_context or "(none)")
        system = await _apply_persona(task_system, req.assistant_id)
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
        system = await _apply_persona(_SUMMARIZE_SYSTEM, req.assistant_id)
        summary = await _generate(system, user_prompt)
        return {"summary": summary}

    @router.post("/ask")
    async def ask(
        req: AskRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_ask", conv_id=req.conversation_id, question=req.question)
        faq_context, _ = await _kb_context(req.question, limit=3)
        task_system = _ASK_SYSTEM.format(faq_context=faq_context or "(none)")
        system = await _apply_persona(task_system, req.assistant_id)
        user_prompt = (
            f"Conversation:\n{_format_messages(req.messages)}\n\n"
            f"Agent question: {req.question}"
        )
        answer = await _generate(system, user_prompt)
        return {"answer": answer}

    return router
