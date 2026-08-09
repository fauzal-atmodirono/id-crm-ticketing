"""Compose the /chat/turn support-agent instruction from the base AGENT_INSTRUCTION
plus an operator-configured assistant persona.

AUGMENT, never replace: the base carries the essential tool-orchestration rules the
agent needs to function, so the operator persona is layered on top. An empty persona
returns the base verbatim (byte-identical default).

Task 2 (sentiment tone adjustment) extends this with a per-sentiment "## Tone" block,
appended the same way as every other persona section below -- it never edits or
removes AGENT_INSTRUCTION's own "## Tone" paragraph, it only adds a more specific one
after it, framed as overriding the general guidance for this turn. Gated by the
`sentiment_tone_adjustment_enabled` setting (default False, passed in by the caller as
`tone_adjustment_enabled`); when off, or when nothing sentiment-specific applies,
`select_tone_block` reproduces today's static "## Tone" paragraph byte-for-byte.
"""

from __future__ import annotations

from typing import Any

# The exact "## Tone" paragraph baked into prompts.AGENT_INSTRUCTION today.
# Captured verbatim -- do not reword without also updating prompts.py, or the
# flag-off / fail-open fallback below silently drifts from what
# AGENT_INSTRUCTION itself says.
_TONE_HEADING = "## Tone\n"
_STATIC_TONE_BODY = (
    "Professional, friendly, and solution-oriented. Reply in the language the "
    "customer used. Code-switching (natural Manglish/Bahasa mixture) is welcome "
    "and encouraged when matching customer tone to make the interaction feel "
    "authentic and local. Never expose internal mechanics — no mention of tools, "
    "ports, classifications, payload fields, or system parameters.\n"
)
STATIC_TONE_PARAGRAPH = _TONE_HEADING + _STATIC_TONE_BODY

# Built-in default wording per sentiment level, used while
# sentiment_tone_adjustment_enabled is True and the operator hasn't set the
# matching AssistantConfig tone_* field (empty string). The flag-off path
# never consults these -- it always returns STATIC_TONE_PARAGRAPH.
_DEFAULT_TONE_NEGATIVE_BODY = (
    "Measured, calm, and apologetic. The customer sounds frustrated or upset — "
    "acknowledge the trouble this has caused before anything else, avoid any "
    "wording that could read as dismissive or defensive, and prioritise getting "
    "them to a resolution or a human teammate quickly. Reply in the language the "
    "customer used.\n"
)
_DEFAULT_TONE_URGENT_BODY = (
    "Acknowledge the urgency in the first sentence — this may be a "
    "safety-critical situation. Be direct, calm, and action-oriented: tell the "
    "customer what to do right now (e.g. contact roadside assistance) before "
    "anything else, and escalate to a human teammate without delay. Reply in "
    "the language the customer used.\n"
)
# Neutral reproduces today's wording verbatim, so flipping the flag on changes
# nothing for the common (non-negative, non-urgent) case.
_DEFAULT_TONE_NEUTRAL_BODY = _STATIC_TONE_BODY
# A positive sentiment has no bespoke wording of its own: a satisfied customer
# is already well served by the same professional/friendly default neutral
# gets, and inventing a distinct "upbeat" register risks reading as forced or
# saccharine in a support context. This is a deliberate choice -- "positive"
# is named explicitly in the mapping below (reusing the neutral body), not
# left to fall through an `else` -- so a future editor sees the decision
# rather than assuming a level was forgotten.
_DEFAULT_TONE_POSITIVE_BODY = _STATIC_TONE_BODY

# sentiment -> (AssistantConfig attribute, built-in default body)
_TONE_CONFIG_ATTRS: dict[str, tuple[str, str]] = {
    "negative": ("tone_negative", _DEFAULT_TONE_NEGATIVE_BODY),
    "urgent": ("tone_urgent", _DEFAULT_TONE_URGENT_BODY),
    "neutral": ("tone_neutral", _DEFAULT_TONE_NEUTRAL_BODY),
    "positive": ("tone_positive", _DEFAULT_TONE_POSITIVE_BODY),
}


def select_tone_block(sentiment: str | None, config: Any, *, enabled: bool) -> str:
    """Return the '## Tone' instruction block to use for this turn.

    - ``enabled=False`` (the ``sentiment_tone_adjustment_enabled`` setting is
      off): always returns ``STATIC_TONE_PARAGRAPH``, byte-identical to
      today's wording, regardless of ``sentiment``/``config``.
    - ``enabled=True``: looks up the operator override for ``sentiment`` on
      ``config`` (an ``AssistantConfig``-shaped object, e.g. its
      ``tone_negative``/``tone_urgent``/``tone_neutral``/``tone_positive``
      fields). An unrecognised or missing sentiment (including ``None``)
      falls back to the "neutral" slot. A non-empty override wins over this
      module's built-in default wording for that sentiment level.
    - Fail-open: any exception while reading ``config`` (e.g. a tenant-store
      outage upstream resolved to a broken/partial config object) degrades to
      ``STATIC_TONE_PARAGRAPH`` -- today's wording -- rather than guessing at
      a sentiment-specific default or returning an empty block. An empty tone
      block would silently change the bot's register with no signal that
      anything went wrong.
    """
    if not enabled:
        return STATIC_TONE_PARAGRAPH
    try:
        if sentiment is not None and sentiment in _TONE_CONFIG_ATTRS:
            attr, default_body = _TONE_CONFIG_ATTRS[sentiment]
        else:
            attr, default_body = _TONE_CONFIG_ATTRS["neutral"]
        body = ""
        if config is not None:
            body = str(getattr(config, attr, "") or "").strip()
        return _TONE_HEADING + (body if body else default_body)
    except Exception:
        return STATIC_TONE_PARAGRAPH


def compose_chat_agent_instruction(
    base: str,
    assistant: Any,
    *,
    sentiment: str | None = None,
    tone_adjustment_enabled: bool = False,
) -> str:
    config = getattr(assistant, "config", None) if assistant is not None else None
    instructions = (getattr(config, "instructions", "") or "").strip() if config is not None else ""
    guardrails = (
        [g for g in (getattr(config, "guardrails", []) or []) if str(g).strip()]
        if config is not None
        else []
    )
    language = (getattr(config, "language", "") or "").strip() if config is not None else ""
    tone_block = select_tone_block(sentiment, config, enabled=tone_adjustment_enabled)
    tone_is_default = tone_block == STATIC_TONE_PARAGRAPH

    if not instructions and not guardrails and not language and tone_is_default:
        return base
    parts = [base]
    if instructions:
        parts.append(f"## Operator persona\n{instructions}")
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    if language:
        parts.append(
            f"## Language\nPrefer {language} when the customer's language is "
            "unclear, but always match the language the customer writes in."
        )
    if not tone_is_default:
        parts.append(
            "## Tone for this reply (overrides the '## Tone' guidance above "
            f"for this turn)\n{tone_block}"
        )
    return "\n\n".join(parts)
