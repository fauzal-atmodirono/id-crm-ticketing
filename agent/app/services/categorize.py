"""Bot case-categorization via a single Gemini classify call.

This is a **resolution-time fallback**: `backend/`'s mid-conversation
classifier (see
`backend/apps/backend/src/chatbot/features/chat/case_taxonomy.py` and
friends, Tasks 3-4) is expected to have already set the `case_category`
custom attribute on most conversations as they progress. This module only
fires when that never happened — a just-resolved conversation with no
`case_category` set yet — and picks one slug (plus, if the taxonomy defines
subcategories for it, one subcategory) from the tenant's configured taxonomy
(``case_taxonomy_json``) for the conversation transcript, using the plain-text
Gemini entry point. Fail-open: any error or an answer that is not one of the
candidates yields ``None`` (no attribute written), never an exception —
categorization must never block the resolution it rides on.
"""

from __future__ import annotations

import logging

from app.ai import gemini
from app.clients.deps import get_chatwoot_client
from app.config import get_settings
from app.services.case_taxonomy import CaseTaxonomy, build_case_taxonomy

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a support ticket classifier. Given a customer conversation "
    "transcript and a list of allowed category slugs, reply with EXACTLY ONE "
    "slug from the list that best fits the conversation — no punctuation, no "
    "explanation, just the slug. If none fit, reply with the single word NONE."
)


def _candidate_slugs(taxonomy: CaseTaxonomy) -> list[str]:
    return taxonomy.main_categories()


async def classify_category(transcript: str, candidates: list[str]) -> str | None:
    if not candidates:
        return None
    context = (
        f"Allowed category slugs: {', '.join(candidates)}\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        answer = await gemini.generate(_SYSTEM_PROMPT, context)
    except Exception:
        logger.exception("categorize: gemini classify failed; skipping")
        return None
    slug = (answer or "").strip()
    return slug if slug in candidates else None


def _transcript_from_messages(raw: object) -> str:
    if isinstance(raw, dict):
        messages = raw.get("payload") or []
    else:
        messages = raw or []
    lines: list[str] = []
    for message in messages[-20:]:
        if message.get("private"):
            continue
        sender = (message.get("sender") or {}).get("name", "Unknown")
        lines.append(f"{sender}: {message.get('content') or ''}")
    return "\n".join(lines)


async def maybe_categorize(conversation_id: int, *, settings=None, chatwoot=None) -> None:
    """Classify a just-resolved conversation, gated + fail-open, *only if*
    `backend/`'s mid-conversation classifier never set `case_category` on it.
    Writes `case_category` (and `case_subcategory`, if the taxonomy defines
    subcategories for the picked category and one matches) as Chatwoot
    conversation custom attributes. Any error is logged and swallowed — this
    never blocks the resolution it rides on."""
    settings = settings or get_settings()
    chatwoot = chatwoot or get_chatwoot_client()

    if not settings.lifecycle_auto_categorize:
        return

    taxonomy = build_case_taxonomy(settings)
    if taxonomy.is_empty():
        return

    try:
        conversation = await chatwoot.get_conversation(conversation_id)
        existing = (conversation or {}).get("custom_attributes") or {}
        if existing.get("case_category"):
            return  # already classified mid-conversation — never overwrite

        candidates = _candidate_slugs(taxonomy)
        raw = await chatwoot.get_messages(conversation_id)
        transcript = _transcript_from_messages(raw)
        if not transcript:
            return

        category = await classify_category(transcript, candidates)
        if category is None:
            return

        # Write the LABEL + flattened "<Label>: <Subcategory>" format, matching
        # what provision_case_taxonomy.py provisions as the Chatwoot List custom
        # attribute options (chatwoot-config/provision_case_taxonomy.py). Fallback
        # to the raw slug only in the unreachable case label_for() returns None for
        # a category that was just validated as a taxonomy member.
        label = taxonomy.label_for(category) or category
        attrs = {"case_category": label}
        subcategory_candidates = taxonomy.subcategories_for(category)
        if subcategory_candidates:
            subcategory = await classify_category(transcript, subcategory_candidates)
            if subcategory is not None:
                attrs["case_subcategory"] = f"{label}: {subcategory}"

        await chatwoot.set_custom_attributes(conversation_id, attrs)
    except Exception:
        logger.exception(
            "categorize: maybe_categorize failed for conversation %s", conversation_id
        )
