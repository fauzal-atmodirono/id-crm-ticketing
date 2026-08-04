"""Post-call transcript classification (Package C Task 4).

Derives ``case_type`` / ``division`` / ``concern`` / ``status`` from a
COMPLETED call transcript, via a single one-shot Gemini call. Deliberately
separate from the live audio path -- ``classify()`` is only ever invoked from
``PhoneBridge.finalize()``, after the call has ended, never from ``pump()``.

A classifier is not a source of truth. Every failure mode -- an empty
transcript, a request error, a timeout (bounded by the caller), a response
that isn't valid JSON, or a value that doesn't belong to the closed
vocabulary a field is validated against -- degrades to that field being
DROPPED, not written through. ``{}`` on total failure, so callers can fall
back to today's exact binary status rule (see ``bridge.py::finalize``)
byte-for-byte.

Vocabulary sourcing, and where each one comes from:
- ``division`` -- reused verbatim from
  ``chatbot.features.metrics.mapping.CATEGORY_TO_DIVISION``'s values, the
  SAME canonical division vocabulary Package E's reporting aggregates on.
  Do not add a second division list here; if the reporting vocabulary grows,
  this module picks it up automatically.
- ``case_type`` -- mapping.py has no case_type concept, so this is NOT
  sourced from it. It mirrors the decks' top-level split already encoded in
  ``deploy/scripts/seed_demo_data/generator.py``'s ``_CASE_TYPE_WEIGHTS``
  (Inquiry / Complaint / Feedback) -- the closest existing canonical source.
- ``concern`` -- mapping.py owns no closed vocabulary for this either (its
  per-division concern lists live only in the demo seeder, which is
  generation-time flavour text, not a validation source real customer
  speech would reliably match). Free text, sanity-checked only
  (non-empty, length-capped) -- constraining it to an invented enum here
  would itself be "inventing a second taxonomy", which the brief the design
  doc backing this module explicitly rules out.
- ``status`` -- the design doc's derived-status vocabulary (open / resolved
  / pending). Richer than what ``ConversationLogPort.append_conversation_
  comment``'s ``status=`` parameter currently understands (only the literal
  string ``"solved"`` has any effect there); ``bridge.py::finalize`` is
  responsible for collapsing this back down to that binary contract. This
  module validates against the full intended vocabulary so a future port
  enhancement doesn't require touching this file.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from chatbot.features.metrics.mapping import CATEGORY_TO_DIVISION

_log = structlog.get_logger(__name__)

# Lightweight, cheap model for a one-shot, best-effort, post-call
# classification -- not settings-driven, since classify()'s signature is
# pinned to (transcript, gemini) and there's no per-tenant reason yet to
# vary it.
_MODEL = "gemini-2.5-flash"

_VALID_CASE_TYPES = frozenset({"Inquiry", "Complaint", "Feedback"})
_VALID_DIVISIONS = frozenset(CATEGORY_TO_DIVISION.values())
_VALID_STATUSES = frozenset({"open", "resolved", "pending"})

_MAX_CONCERN_LENGTH = 200

_PROMPT_TEMPLATE = """You are classifying a COMPLETED customer support phone call transcript for \
an automotive company's CRM. Read the transcript and respond with ONLY a \
JSON object (no markdown, no explanation) using these keys -- omit any key \
you are not reasonably confident about, do not guess:

- "case_type": exactly one of {case_types}
- "division": the department the call is mainly about, exactly one of {divisions}
- "concern": a short (a few words) description of the specific thing the \
caller asked about, e.g. "Home Charging", "Booking", "Delivery"
- "status": exactly one of "resolved" (the caller's issue was fully \
addressed on this call), "pending" (the caller is waiting on a follow-up or \
callback), or "open" (the issue was NOT resolved and needs further action)

Transcript:
{transcript}
"""


def _canonical(value: object, valid: frozenset[str]) -> str | None:
    """Case-insensitive membership check against a closed vocabulary,
    returning the vocabulary's own canonical spelling so e.g. "sales" still
    resolves to "Sales" instead of being dropped for a casing mismatch.
    Returns None for anything not a non-empty string, or not a member."""
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if not lowered:
        return None
    for candidate in valid:
        if candidate.lower() == lowered:
            return candidate
    return None


async def classify(transcript: str, gemini: Any) -> dict[str, str]:
    """Classify a completed call transcript. Never raises -- any failure
    returns ``{}``. Fields that parse but fail their own vocabulary check
    are dropped individually; the rest of a partially-valid response is
    still returned (an invented/mistyped division must not sink a correctly
    classified case_type/status alongside it)."""
    text = transcript.strip()
    if not text:
        return {}
    try:
        response = await gemini.aio.models.generate_content(
            model=_MODEL,
            contents=_PROMPT_TEMPLATE.format(
                case_types=", ".join(sorted(_VALID_CASE_TYPES)),
                divisions=", ".join(sorted(_VALID_DIVISIONS)),
                transcript=text,
            ),
            config={"temperature": 0.0, "response_mime_type": "application/json"},
        )
        raw = (getattr(response, "text", None) or "").strip()
        data = json.loads(raw)
    except Exception as e:
        _log.warning("phone_transcript_classify_failed", error=str(e))
        return {}

    if not isinstance(data, dict):
        _log.warning("phone_transcript_classify_not_a_dict", got=type(data).__name__)
        return {}

    result: dict[str, str] = {}

    case_type = _canonical(data.get("case_type"), _VALID_CASE_TYPES)
    if case_type is not None:
        result["case_type"] = case_type
    elif data.get("case_type") is not None:
        _log.warning("phone_transcript_classify_invalid_case_type", value=data.get("case_type"))

    division = _canonical(data.get("division"), _VALID_DIVISIONS)
    if division is not None:
        result["division"] = division
    elif data.get("division") is not None:
        _log.warning("phone_transcript_classify_invalid_division", value=data.get("division"))

    concern = data.get("concern")
    if isinstance(concern, str) and concern.strip():
        result["concern"] = concern.strip()[:_MAX_CONCERN_LENGTH]

    status = _canonical(data.get("status"), _VALID_STATUSES)
    if status is not None:
        result["status"] = status
    elif data.get("status") is not None:
        _log.warning("phone_transcript_classify_invalid_status", value=data.get("status"))

    return result
