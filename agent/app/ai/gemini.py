"""Thin wrapper around the google-genai SDK for the AI decision layer.

Two entry points:
  - `decide`: forces the model to pick one of the three `tools.TOOLS`
    function calls (`tool_config`'s `function_calling_config` mode `ANY`) and
    returns a `Decision`. If the model answers with plain text instead of a
    function call — or every attempt errors out — we fall back to a
    `handoff_to_human` decision so a conversation never silently stalls.
  - `generate`: plain text generation (no tools), used by the resolution-time
    categorization fallback classifier (services.categorize).

Both accept an injectable `client` so tests never touch the real API. The
google-genai SDK's `generate_content` call is synchronous, so both entry
points run it via `asyncio.to_thread` — a real Gemini round-trip must never
block the event loop (and with it every other request on the worker).
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from google import genai
from google.genai import types

from app.ai.tools import TOOLS
from app.config import get_settings

logger = logging.getLogger(__name__)

_HANDOFF_NO_ACTION_REASON = "model returned no action"
_MAX_ATTEMPTS = 2  # one try + one retry


class Decision(NamedTuple):
    action: str
    args: dict
    raw_text: str | None
    prompt_tokens: int | None
    output_tokens: int | None = None
    cached_tokens: int | None = None


def _client() -> genai.Client:
    settings = get_settings()
    # Prefer Vertex AI via Application Default Credentials (the mounted service
    # account); fall back to the AI-Studio API-key path only when explicitly
    # disabled. Mirrors the backend's genai client construction.
    if settings.google_genai_use_vertexai:
        return genai.Client(
            vertexai=True,
            project=settings.vertex_project_id or None,
            location=settings.vertex_location,
        )
    return genai.Client(api_key=settings.gemini_api_key)


def _usage_tokens(response: object) -> tuple[int | None, int | None, int | None]:
    """Extract (prompt, output, cached) token counts from `usage_metadata`.

    Missing metadata records `None` for all three, never `0` — a zero-token
    call is a real, observed thing that happened; "we did not capture it" is
    a different fact, and a cost report that conflates the two understates
    spend, silently, in exactly the direction nobody double-checks. Each
    field is looked up independently via `getattr(..., None)` so a genuinely
    zero field (attribute present, value `0`) is preserved as `0` rather than
    collapsed into the same `None` as a field the SDK never populated.

    Field names verified against the installed `google-genai` 2.12.1 SDK's
    `types.GenerateContentResponseUsageMetadata` (`prompt_token_count`,
    `candidates_token_count`, `cached_content_token_count`) — see task-1
    report for how.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None, None, None
    prompt_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)
    cached_tokens = getattr(usage, "cached_content_token_count", None)
    return prompt_tokens, output_tokens, cached_tokens


def _handoff_fallback(
    reason: str = _HANDOFF_NO_ACTION_REASON,
    raw_text: str | None = None,
    prompt_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
) -> Decision:
    return Decision(
        action="handoff_to_human",
        args={"reason": reason},
        raw_text=raw_text,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )


def _extract_decision(response: object) -> Decision:
    prompt_tokens, output_tokens, cached_tokens = _usage_tokens(response)

    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                return Decision(
                    action=function_call.name,
                    args=dict(function_call.args or {}),
                    raw_text=None,
                    prompt_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                )

    # The model answered in plain text (or returned nothing usable) instead
    # of calling a function. Map that to a handoff so a human always sees
    # it -- but a real Gemini call still happened and still has real usage
    # metadata, so still record it: losing the token count on exactly the
    # calls that went wrong would silently under-report them.
    text = getattr(response, "text", None)
    return _handoff_fallback(
        raw_text=text,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )


async def decide(
    system_prompt: str,
    conversation_context: str,
    client: genai.Client | None = None,
) -> Decision:
    settings = get_settings()
    genai_client = client if client is not None else _client()

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=TOOLS,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="ANY"),
        ),
    )

    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await asyncio.to_thread(
                genai_client.models.generate_content,
                model=settings.gemini_model,
                contents=conversation_context,
                config=config,
            )
            return _extract_decision(response)
        except Exception as exc:  # noqa: BLE001 - any SDK/transport failure is transient here
            last_error = exc
            logger.warning("gemini.decide: attempt %s/%s failed: %s", attempt, _MAX_ATTEMPTS, exc)

    logger.error("gemini.decide: giving up after %s attempts, handing off: %s", _MAX_ATTEMPTS, last_error)
    return _handoff_fallback()


async def generate(
    system_prompt: str,
    context: str,
    client: genai.Client | None = None,
) -> str:
    """Plain text generation (no tools), for the resolution-time
    categorization fallback classifier. Raises after exhausting retries —
    callers decide whether/how to degrade."""
    settings = get_settings()
    genai_client = client if client is not None else _client()

    config = types.GenerateContentConfig(system_instruction=system_prompt)

    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await asyncio.to_thread(
                genai_client.models.generate_content,
                model=settings.gemini_model,
                contents=context,
                config=config,
            )
            return (getattr(response, "text", None) or "").strip()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("gemini.generate: attempt %s/%s failed: %s", attempt, _MAX_ATTEMPTS, exc)

    logger.error("gemini.generate: giving up after %s attempts: %s", _MAX_ATTEMPTS, last_error)
    raise RuntimeError("gemini.generate failed after retry") from last_error
