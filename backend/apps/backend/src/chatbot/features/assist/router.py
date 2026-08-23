"""POST /assist/suggest|summarize|ask|rewrite — Proton AI-assist endpoints.

Called by the patched Chatwoot frontend (patch 0002; /rewrite added by patch
0078). All endpoints require `x-api-key` matching `Settings.proton_backend_key`;
an empty key returns 503 so misconfigured deployments fail loudly rather than
leaving the endpoint open.

Request shape (all four endpoints share a base):
  - conversation_id : str          — Chatwoot conversation id (for logging)
  - messages        : list[str]    — conversation turns, most-recent last
  - assistant_id    : str | None   — optional; steers output via product_name +
                                     guardrails when assistants_store is provided
  - question        : str          — only for /ask

/suggest also accepts:
  - limit : int (default 3) — max KB hits to ground the reply

/rewrite also accepts (and does not use `messages` for anything but the
inherited min-length validation — it operates on the agent's own draft, not
the conversation transcript):
  - content : str — the agent's current composer draft to rewrite
  - mode    : str — one of the `_REWRITE_TASKS` keys (improve, fix_grammar,
                    tone_professional, tone_casual, tone_straightforward,
                    tone_confident, tone_friendly); an unknown mode is a 400

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

from chatbot.features.assist.assist_media import (
    AssistMessage,
    MediaTermsCache,
    collect_media_parts,
    customer_texts,
    render_transcript,
)

if TYPE_CHECKING:
    from google.genai import types

    from chatbot.features.assist.chatwoot_context import ChatwootContextClient
    from chatbot.features.chat.adapters.assistants_store import AssistantsStorePort
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
    from chatbot.features.chat.ports import KnowledgePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_SUGGEST_SYSTEM = (
    "You are a customer-support agent for Proton Holdings.\n"
    "Read the ENTIRE conversation below — not just the last line — and connect "
    "the dots: what the customer wants, which details they have already "
    "provided, and what the agent or bot has already said or done.\n\n"
    "Then write the single most useful next reply to the customer:\n"
    "- If the customer's request is already complete (for example, every detail "
    "for a booking or request has been collected) AND the agent/bot has already "
    "told them they are being connected to a human or will be contacted, do NOT "
    "repeat that handoff. Instead write a brief, specific confirmation that "
    "reflects the concrete details they gave (such as the model, the dealer or "
    "location, and how they will be contacted).\n"
    "- Otherwise, answer or advance the conversation using the FAQ context "
    "below.\n\n"
    "LANGUAGE (critical): reply in the EXACT SAME language as the customer's "
    "latest message. If they wrote in English, reply in English; if in Malay, "
    "reply in Malay. Never switch languages and never default to Malay when the "
    "customer wrote in another language. "
    "Do not include a salutation or sign-off. "
    "Return only the reply text, nothing else.\n\n"
    "FAQ context:\n{faq_context}"
)

# PII note (fix for a gap P7 task 9's resolved-case-index report found: this
# constant had NO PII-omission instruction at all, even though the P7 design
# states the summariser "is instructed to omit identifiers" as the mitigation
# before summaries go into a pgvector store). The sentence below is a REQUEST
# to the model, not a masking or redaction mechanism — nothing in this module,
# or in resolved_case_index.py (which stores this endpoint's output), inspects,
# strips, or validates the returned summary text. The model can still include
# a name, phone number, plate number, or address if it includes one anyway.
# The real fix is gap R16, tracked as blocked on Q7 in
# docs/analysis/2026-08-09-blocked-work-register.md — do not read this
# instruction as closing that gap, only as making the stated mitigation
# actually exist as a prompt line.
#
# `_apply_persona` (below, and at its one call site for this constant) only
# PREPENDS an operator persona prefix ahead of this string — it never removes
# or replaces text after it, so no wiring path can structurally drop this
# instruction. An operator's own guardrails/instructions could still tell the
# model to do the opposite (e.g. "always include the customer's full name");
# that is a model-instruction-following risk, not a code path that strips
# this sentence.
_SUMMARIZE_SYSTEM = (
    "You are a customer-support supervisor. "
    "Summarise the following conversation between a customer and a support agent "
    "in 3–5 bullet points. Focus on: the customer's issue, steps taken, and current status. "
    "Do not include the customer's name, phone number, email address, home "
    "address, or vehicle registration/plate number in the summary — refer to "
    'them generically instead (e.g. "the customer", "their vehicle"). '
    "Reply in English regardless of the conversation language."
)

_ASK_SYSTEM = (
    "You are a customer-support knowledge assistant. "
    "Answer the agent's question using the conversation history and the FAQ context below. "
    "Be concise. If the answer is not in the context, say so clearly.\n\n"
    "FAQ context:\n{faq_context}"
)

# Rules shared by every /rewrite mode. Appended after the mode-specific task
# instruction (see `_REWRITE_TASKS`) so a persona prefix — from
# `_apply_persona`, prepended ahead of both — can still steer output without
# ever being able to unset these.
_REWRITE_COMMON_RULES = (
    "Return ONLY the rewritten message — no preamble, no explanation, and no "
    "quotes around it. The result is inserted directly into the reply "
    "composer, so anything you write beyond the rewritten message itself "
    "goes to the customer verbatim.\n"
    "Preserve the original language exactly: if the draft is in Indonesian, "
    "the rewrite is in Indonesian; if it is in English, the rewrite is in "
    "English. Never translate the draft into a different language.\n"
    "Preserve meaning and every factual claim exactly as written — names, "
    "numbers, dates, and order/case references must not change. Never invent "
    "a commitment the agent did not make."
)

# One task instruction per /rewrite `mode`. Combined with
# `_REWRITE_COMMON_RULES` (see `_task_system` in `build_assist_router`) to
# form the full task system prompt before `_apply_persona` prepends the
# operator persona. An unknown key here is what the endpoint rejects with 400.
_REWRITE_TASKS: dict[str, str] = {
    "improve": (
        "You are a customer-support writing assistant. Improve the clarity "
        "and flow of the agent's draft reply below, while keeping the "
        "agent's intent exactly as written."
    ),
    "fix_grammar": (
        "You are a customer-support proofreader. Correct ONLY the spelling, "
        "grammar, and punctuation of the agent's draft reply below. Do not "
        "restyle it, shorten it, or add anything to it — a punctuation or "
        "spelling fix is in scope, a rewrite for tone or clarity is not."
    ),
    "tone_professional": (
        "You are a customer-support writing assistant. Rewrite the agent's "
        "draft reply below in a more professional, businesslike register. "
        "Shift tone and word choice only — do not add, remove, or reorder "
        "content."
    ),
    "tone_casual": (
        "You are a customer-support writing assistant. Rewrite the agent's "
        "draft reply below in a more casual, relaxed register, as if talking "
        "to a friend. Shift tone and word choice only — do not add, remove, "
        "or reorder content."
    ),
    "tone_straightforward": (
        "You are a customer-support writing assistant. Rewrite the agent's "
        "draft reply below in a more direct, straightforward register — "
        "short sentences, no hedging, no filler. Shift tone and word choice "
        "only — do not add, remove, or reorder content."
    ),
    "tone_confident": (
        "You are a customer-support writing assistant. Rewrite the agent's "
        "draft reply below in a more confident, assured register — remove "
        "hedging language like 'maybe' or 'I think' without changing what is "
        "being promised. Shift tone and word choice only — do not add, "
        "remove, or reorder content."
    ),
    "tone_friendly": (
        "You are a customer-support writing assistant. Rewrite the agent's "
        "draft reply below in a warmer, friendlier register. Shift tone and "
        "word choice only — do not add, remove, or reorder content."
    ),
}

# Appended to the task system prompt ONLY on requests that actually carry
# media, so a text-only call produces a byte-identical prompt to today's.
#
# Load-bearing for the bug this feature exists to fix: with the video attached
# but no instruction, the model still hedged and asked the customer to explain
# what they had just sent. The transcript marker ("[sent a video]") tells it
# something exists; this tells it to look.
_MEDIA_INSTRUCTION = (
    "The customer's attachments referenced in the transcript "
    "(photo, video, voice note, or document) are attached to this request. "
    "Use what they actually show or say. Never ask the customer to describe, "
    "explain, or re-send something they have already sent — if their message "
    'refers to an attachment ("this one", "like this", "see photo"), the '
    "referent is attached, so answer about it directly."
)

# Extraction prompt for the retrieval fallback (see `_kb_context`). Asks for
# keywords rather than a description on purpose: the output goes straight into
# a KB similarity search, where "The customer has sent a video showing..."
# is mostly noise around the two or three words that matter.
_MEDIA_QUERY_SYSTEM = (
    "You extract search keywords from customer-supplied media for a car "
    "support knowledge-base lookup.\n"
    "Look at the attachment(s) and reply with a SHORT comma-separated list of "
    "concrete, searchable things you can see or hear: the vehicle model if it "
    "is identifiable, the part or component in view, any warning light, error "
    "message, visible damage, or audible symptom.\n"
    "No sentences, no preamble, no explanation. Do not guess: if nothing "
    "concrete is identifiable, reply with nothing at all."
)

# Ceiling on the extracted keyword string before it becomes a search query.
_MEDIA_TERMS_MAX_CHARS = 200

_SNIPPET = 300


class AssistBase(BaseModel):
    conversation_id: str = Field(min_length=1)
    # Two accepted shapes. `list[AssistMessage]` is what the Chatwoot fork now
    # posts: structured, so the backend owns all transcript rendering from one
    # registry and the SPA holds no label vocabulary to drift from it.
    # `list[str]` is the legacy pre-rendered shape and still renders
    # byte-identically, so an un-upgraded Chatwoot image keeps working exactly
    # as it does today rather than 422-ing.
    messages: list[AssistMessage] | list[str] = Field(min_length=1)
    assistant_id: str | None = None


class SuggestRequest(AssistBase):
    limit: int = Field(default=3, ge=1, le=10)


class SummarizeRequest(AssistBase):
    pass


class AskRequest(AssistBase):
    question: str = Field(min_length=1)


class RewriteRequest(AssistBase):
    content: str = Field(min_length=1)
    mode: str


def _build_persona_prefix(product_name: str, guardrails: list[str], language: str = "") -> str:
    """Build a brief persona prefix from product_name, guardrails, and language.

    Returns an empty string when all are absent so the task prompt is
    unchanged — behaviour-preserving for the no-assistant / empty-assistant path.
    """
    parts: list[str] = []
    if product_name:
        parts.append(f"Product: {product_name}")
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    language = (language or "").strip()
    if language:
        parts.append(
            f"Default to {language} when no language is otherwise indicated, "
            "but always follow this task's own language instructions below "
            "if they say otherwise."
        )
    return "\n".join(parts)


def _retrieval_query(messages: list[AssistMessage] | list[str], max_turns: int = 6) -> str:
    """Build the KB query from the customer's turns, not just the last line.

    Grounding on the whole customer intent keeps retrieval from being derailed
    by a one-word last turn like "bangsar". Falls back to the last rendered
    line when no customer turn is present, so callers passing unlabelled
    strings behave exactly as before.

    Attachment markers never enter the query: `customer_texts` reads `content`
    off structured messages rather than un-rendering markers back out of
    strings, so "a video" cannot become a search term.
    """
    customer = customer_texts(messages)
    if not customer:
        rendered = render_transcript(messages)
        return rendered[-1] if rendered else ""
    return "\n".join(customer[-max_turns:])


def build_assist_router(
    settings: Settings,
    knowledge_port: KnowledgePort,
    genai_client: Any,
    assistants_store: AssistantsStorePort | None = None,
    tenant_settings_store: TenantSettingsStorePort | None = None,
    chatwoot_context: ChatwootContextClient | None = None,
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
        chatwoot_context: optional read-only Chatwoot client used to fetch the
            conversation's attachments.  When None (every pre-existing
            call-site), no request ever carries media and the endpoints behave
            exactly as they did before — the media path is additive.
    """
    router = APIRouter(prefix="/assist", tags=["assist"])
    # Per-router, not module-global: two apps in one process (and every test
    # call-site) get their own, so cached terms can never leak across tenants.
    _terms_cache = MediaTermsCache()

    def _authorize(x_api_key: str | None) -> None:
        key = settings.proton_backend_key
        if not key:
            raise HTTPException(status_code=503, detail="Assist endpoints not configured")
        if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), key.encode()):
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
        language = getattr(assistant.config, "language", "") or ""
        return _build_persona_prefix(assistant.product_name, assistant.config.guardrails, language)

    async def _collect_media(conversation_id: str) -> list[types.Part]:
        return await collect_media_parts(
            chatwoot_context,
            conversation_id,
            enabled=settings.assist_media_understanding_enabled,
            max_bytes=settings.assist_media_max_bytes,
        )

    async def _generate(
        system: str, user_prompt: str, media_parts: list[types.Part] | None = None
    ) -> str:
        """One Gemini call. `media_parts` is additive and optional.

        With no media the call is made exactly as before — `contents` stays a
        plain string rather than a one-element `Content`. That is not
        cosmetic: it keeps the no-media path byte-identical for every existing
        caller, including P7's resolved-case summariser which invokes
        `/assist/summarize`'s endpoint function in-process.
        """
        model = await _resolve_model()
        contents: Any = user_prompt
        if media_parts:
            from google.genai import types as _types  # noqa: PLC0415

            contents = _types.Content(
                role="user",
                parts=[_types.Part.from_text(text=user_prompt), *media_parts],
            )
            system = f"{system}\n\n{_MEDIA_INSTRUCTION}"
        response = await genai_client.aio.models.generate_content(
            model=model,
            contents=contents,
            config={"system_instruction": system},
        )
        return (response.text or "").strip()

    def _format_messages(messages: list[AssistMessage] | list[str]) -> str:
        return "\n".join(f"[{i + 1}] {m}" for i, m in enumerate(render_transcript(messages)))

    async def _media_search_terms(media_parts: list[types.Part]) -> str:
        """Ask Gemini what the attachments actually show, as search keywords.

        One extra call, so it is made only where it can pay for itself — see
        `_kb_context`. Returns "" on any failure, which simply leaves retrieval
        where it already was.
        """
        if not media_parts:
            return ""
        try:
            from google.genai import types as _types  # noqa: PLC0415

            model = await _resolve_model()
            response = await genai_client.aio.models.generate_content(
                model=model,
                contents=_types.Content(
                    role="user",
                    parts=[
                        _types.Part.from_text(text="What is in this media?"),
                        *media_parts,
                    ],
                ),
                config={"system_instruction": _MEDIA_QUERY_SYSTEM},
            )
            # Capped: a model that ignores "short list" and narrates a paragraph
            # would otherwise become the entire retrieval query and bury the
            # real terms.
            return (response.text or "").strip()[:_MEDIA_TERMS_MAX_CHARS]
        except Exception:
            _log.warning("assist_media_terms_failed", exc_info=True)
            return ""

    async def _media_terms(conversation_id: str, media_parts: list[types.Part]) -> str:
        """Cached keywords for this conversation's media. `""` if none/failed."""
        cached = _terms_cache.get(conversation_id)
        if cached is not None:
            return cached
        terms = await _media_search_terms(media_parts)
        _terms_cache.put(conversation_id, terms)
        if terms:
            _log.info("assist_media_terms", conv_id=conversation_id, terms=terms)
        return terms

    async def _kb_context(
        query: str,
        limit: int,
        media_parts: list[types.Part] | None = None,
        conversation_id: str = "",
    ) -> tuple[str, list[dict[str, Any]]]:
        """KB articles for the query, grounded on the media when there is any.

        A customer whose entire question is the attachment ("this one", a photo
        of a warning light) gives us nothing to search on, so the query retrieves
        whatever the index thinks is nearest and the reply becomes "I have no
        information about that" — while the model sits next to a video it can
        plainly read. So when media is attached we ask what it shows and put
        those keywords at the FRONT of the search query.

        Why this fires on every media request rather than only when retrieval
        looks bad: it was originally gated on the text search returning ZERO
        articles, and in production that never happened. The KB is a similarity
        search — it always returns nearest neighbours, so a meaningless query
        yields confidently irrelevant hits rather than nothing. Retrieval here
        fails IRRELEVANT, not EMPTY, and `KbArticle` carries no score to tell
        the two apart. The gate was unfireable, so it is gone. (Surfacing a
        relevance score through `KnowledgePort` would let this be precise again;
        that is a five-adapter contract change, deliberately not done here.)

        The cost of firing always is blunted by `_terms_cache`, not by guessing:
        an agent clicking Suggest, then Ask, then Suggest on one conversation
        pays for a single extraction.

        The customer's own text stays in the query after the terms — it has not
        been shown to be useless, and it can carry intent ("warranty") worth
        blending into a similarity search.
        """
        if media_parts:
            terms = await _media_terms(conversation_id, media_parts)
            if terms:
                query = f"{terms}\n{query}".strip()
        articles = await knowledge_port.search_kb(query, limit)
        sources = [
            {
                "title": a.title,
                "snippet": a.content[:_SNIPPET],
                "url": a.url,
            }
            for a in articles
        ]
        faq_context = "\n---\n".join(f"Q: {a.title}\nA: {a.content[:_SNIPPET]}" for a in articles)
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
        # Media is collected BEFORE retrieval, not after: _kb_context needs it
        # on hand to fall back to when the text query finds nothing.
        media_parts = await _collect_media(req.conversation_id)
        query = _retrieval_query(req.messages)
        faq_context, sources = await _kb_context(query, req.limit, media_parts, req.conversation_id)
        task_system = _SUGGEST_SYSTEM.format(faq_context=faq_context or "(none)")
        system = await _apply_persona(task_system, req.assistant_id)
        user_prompt = _format_messages(req.messages)
        draft = await _generate(system, user_prompt, media_parts)
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
        media_parts = await _collect_media(req.conversation_id)
        summary = await _generate(system, user_prompt, media_parts)
        return {"summary": summary}

    @router.post("/ask")
    async def ask(
        req: AskRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_ask", conv_id=req.conversation_id, question=req.question)
        # Same ordering as /suggest: an agent can ask "what's wrong with this?"
        # about a photo, which retrieves nothing on the words alone.
        media_parts = await _collect_media(req.conversation_id)
        faq_context, _ = await _kb_context(req.question, 3, media_parts, req.conversation_id)
        task_system = _ASK_SYSTEM.format(faq_context=faq_context or "(none)")
        system = await _apply_persona(task_system, req.assistant_id)
        user_prompt = (
            f"Conversation:\n{_format_messages(req.messages)}\n\nAgent question: {req.question}"
        )
        answer = await _generate(system, user_prompt, media_parts)
        return {"answer": answer}

    @router.post("/rewrite")
    async def rewrite(
        req: RewriteRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        if req.mode not in _REWRITE_TASKS:
            valid = ", ".join(sorted(_REWRITE_TASKS))
            raise HTTPException(
                status_code=400,
                detail=f"Unknown mode {req.mode!r}. Valid modes: {valid}",
            )
        _log.info("assist_rewrite", conv_id=req.conversation_id, mode=req.mode)
        task_system = f"{_REWRITE_TASKS[req.mode]}\n\n{_REWRITE_COMMON_RULES}"
        system = await _apply_persona(task_system, req.assistant_id)
        draft = await _generate(system, req.content)
        return {"draft": draft}

    return router
