"""Bot case-categorization via a single Gemini classify call.

When the bot resolves a conversation, the SOP requires it to assign the
appropriate case category. This picks one slug from the tenant's configured
taxonomy (``lifecycle_category_labels``) for the conversation transcript, using
the plain-text Gemini entry point. Fail-open: any error or an answer that is not
one of the candidates yields ``None`` (no label applied), never an exception —
categorization must never block the resolution it rides on.
"""

from __future__ import annotations

import logging

from app.ai import gemini
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a support ticket classifier. Given a customer conversation "
    "transcript and a list of allowed category slugs, reply with EXACTLY ONE "
    "slug from the list that best fits the conversation — no punctuation, no "
    "explanation, just the slug. If none fit, reply with the single word NONE."
)


def _candidate_slugs(settings: Settings) -> list[str]:
    raw = settings.lifecycle_category_labels or ""
    return [slug.strip() for slug in raw.split(",") if slug.strip()]


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
