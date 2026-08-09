"""POST /assist/translate — the agent-facing translate action (P7 task 3).

An agent working a conversation in Malay, Tamil, or Chinese can read the
customer's message in English: this endpoint translates the given text,
detects its source language, and posts the translation into the conversation
as a **private note** (never an outgoing message — see the module-level
warning below), so the translation lives alongside the conversation without
ever reaching the customer.

Gated end to end on `translation_enabled` (default False, per wave 0's
config comment); when off, every call returns `{"disabled": true}` and does
nothing, the same shape `features/routing/status_router.py` already uses for
its own default-off flag, so a direct caller gets the same guarantee the
wiring gets.

CUSTOMER-SAFETY INVARIANT (read before touching the note-posting call):
the translation is posted via `TicketingPort.add_private_note`, whose real
Chatwoot implementation (`chat/adapters/chatwoot.py:737-743`) hard-codes
`{"message_type": "outgoing", "private": True}` on every call — there is no
code path in this router that can send a translation to the customer. This
repo has already shipped the opposite-direction bug once (commit `0aa643d`,
a customer-facing reply silently degraded to a private note); this endpoint
is upstream of that same bug CLASS from the other side — a translation of
the CUSTOMER's own message accidentally sent back to them as an outgoing
message — so this invariant is deliberately structural, not just tested.

THE TAMIL SPLIT (translation_outbound_tamil_enabled), not symmetric with
translation_enabled:
  - INBOUND: translating a Tamil customer message so an agent can read it
    (target_language != "ta") is gated only by translation_enabled. An
    imperfect translation an agent reads privately is strictly better than a
    message they cannot read at all — this direction never touches the
    customer.
  - OUTBOUND: translating a reply INTO Tamil (target_language == "ta", i.e.
    to send back to a Tamil-speaking customer) stays blocked
    (`translation_outbound_tamil_enabled` defaults False) until a signed-off
    evaluation of real Tamil enquiries exists — see the flag's comment in
    `platform/config.py`. This gate fires on target_language alone,
    independent of what the detected source language turns out to be, and
    fires BEFORE any Gemini call is made.
  `translation_outbound_tamil_enabled` is deliberately excluded even from
  `deploy/scripts/check-suites-both-flag-states.sh`'s all-flags-on gate; do
  not add it there.

RBAC: gated on `translation.use` (see `features/authz/seed.py`), granted to
the default "agent" role. Reading a customer's own message in translation is
an ordinary part of an agent's job on every conversation they handle, not an
administrative act — gating this admin-only would make the feature unusable
by the people it exists for (the same mistake ruling D5 and the
`presence.set_own_status` decision both had to correct). When RBAC is
unconfigured (the default), `require_permission` falls back to today's
shared-secret check — no behaviour change for a tenant that hasn't opted in.

MODEL FAILURE: a Gemini error or an unparseable response raises a 502
*before* any note is posted — a half-posted note saying nothing would be
worse than an honest failure, so the note-posting call only happens after
translation succeeds.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.ports import TicketingPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ms": "Bahasa Melayu",
    "ta": "Tamil",
    "zh": "Chinese",
}

_TRANSLATE_SYSTEM = (
    "You are a translation assistant helping a customer-support agent read a "
    "message. The message may be in English, Bahasa Melayu, Tamil, or Chinese.\n\n"
    "1. Detect the language of the message and return its two-letter code: "
    '"en", "ms", "ta", or "zh".\n'
    "2. Translate the message into {target_name}. If the message is ALREADY "
    "in {target_name}, return it completely unchanged as the translation — do "
    "not paraphrase or otherwise edit it.\n\n"
    "Return ONLY a JSON object, no other text, with exactly these two keys:\n"
    '{{"detected_source_language": "<code>", "translation": "<text>"}}'
)


class TranslateRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    # Default "en": the task this endpoint exists for is an agent reading an
    # inbound message in English. A caller may pass any other target, subject
    # to the outbound-Tamil gate above.
    target_language: str = Field(default="en", min_length=2, max_length=8)


def _strip_code_fence(raw: str) -> str:
    """Strip a ```json ... ``` (or bare ```) fence if the model added one.

    Models asked for "JSON only" occasionally still wrap it in a Markdown
    fence; stripping it here keeps the parser from treating a
    well-formed-but-fenced response as a failure.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    return text


def _parse_translation(raw: str) -> dict[str, str]:
    """Parse the model's JSON response into {translation, detected_source_language}.

    Raises ValueError on anything that isn't the expected shape, so the
    caller can fold it into the same "clear error, no note posted" path as
    any other model failure.
    """
    data = json.loads(_strip_code_fence(raw))
    translation = data["translation"]
    detected = data["detected_source_language"]
    if not isinstance(translation, str) or not isinstance(detected, str):
        raise ValueError("translation response fields must be strings")
    return {"translation": translation, "detected_source_language": detected.strip().lower()}


def build_translate_router(
    settings: Settings,
    genai_client: Any,
    ticketing_port: TicketingPort,
    authz_repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
) -> APIRouter:
    """Return a FastAPI router exposing POST /assist/translate.

    Args:
        settings: application settings (reads translation_enabled,
            translation_outbound_tamil_enabled, assist_gemini_model).
        genai_client: a google.genai.Client instance (or stub in tests).
        ticketing_port: posts the translation as a private note
            (TicketingPort.add_private_note — never an outgoing message).
        authz_repo / validator: RBAC collaborators forwarded to
            require_permission; both None reproduces today's shared-secret
            behaviour exactly (see require_permission's own docstring).
    """
    router = APIRouter(prefix="/assist", tags=["assist"])
    can_translate = require_permission(
        "translation.use", repo=authz_repo, validator=validator, settings=settings
    )

    async def _translate(text: str, target_language: str) -> dict[str, str]:
        target_name = _LANGUAGE_NAMES.get(target_language, target_language)
        system = _TRANSLATE_SYSTEM.format(target_name=target_name)
        response = await genai_client.aio.models.generate_content(
            model=settings.assist_gemini_model,
            contents=text,
            config={"system_instruction": system, "response_mime_type": "application/json"},
        )
        return _parse_translation(response.text or "")

    @router.post("/translate", dependencies=[Depends(can_translate)])
    async def translate(req: TranslateRequest) -> dict[str, Any]:
        if not settings.translation_enabled:
            return {"disabled": True, "reason": "translation_enabled is off"}

        target_language = req.target_language.strip().lower()

        # Outbound-Tamil gate: fires on the TARGET alone, before any model
        # call, regardless of what the source language turns out to be. See
        # the module docstring's "THE TAMIL SPLIT" section.
        if target_language == "ta" and not settings.translation_outbound_tamil_enabled:
            _log.info(
                "assist_translate_outbound_tamil_blocked", conversation_id=req.conversation_id
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    "Outbound Tamil translation is disabled pending a signed-off "
                    "evaluation of real Tamil enquiries "
                    "(translation_outbound_tamil_enabled=False)."
                ),
            )

        try:
            result = await _translate(req.text, target_language)
        except Exception as exc:
            _log.warning(
                "assist_translate_failed",
                conversation_id=req.conversation_id,
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail="Translation failed; no note was posted.",
            ) from exc

        note_text = (
            f"[Translation {result['detected_source_language']} → {target_language}]\n"
            f"{result['translation']}"
        )
        await ticketing_port.add_private_note(req.conversation_id, note_text)

        return {
            "translation": result["translation"],
            "detected_source_language": result["detected_source_language"],
        }

    return router
