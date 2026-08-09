from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig
from chatbot.features.chat.chat_persona import (
    STATIC_TONE_PARAGRAPH,
    compose_chat_agent_instruction,
    select_tone_block,
)

BASE = "BASE AGENT INSTRUCTION."


def _a(**cfg):
    return Assistant(
        id="test-id",
        name="A",
        description="",
        product_name="",
        config=AssistantConfig(**cfg),
        enabled=True,
        is_default=False,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_empty_persona_returns_base_verbatim():
    assert compose_chat_agent_instruction(BASE, _a()) == BASE
    assert compose_chat_agent_instruction(BASE, None) == BASE


def test_instructions_appended_as_operator_persona():
    out = compose_chat_agent_instruction(BASE, _a(instructions="Be warm and brief."))
    assert out.startswith(BASE)
    assert "## Operator persona" in out and "Be warm and brief." in out


def test_guardrails_and_language_appended():
    out = compose_chat_agent_instruction(
        BASE, _a(guardrails=["No prices", "No promises"], language="Bahasa Melayu")
    )
    assert out.startswith(BASE)
    assert "## Guardrails" in out and "- No prices" in out and "- No promises" in out
    assert "## Language" in out
    assert (
        "Prefer Bahasa Melayu when the customer's language is unclear, but "
        "always match the language the customer writes in." in out
    )


# ---------------------------------------------------------------------------
# Task 2 -- sentiment tone adjustment (`select_tone_block` /
# `compose_chat_agent_instruction`'s `sentiment`/`tone_adjustment_enabled`
# kwargs).
# ---------------------------------------------------------------------------


def test_a_negative_sentiment_selects_the_measured_apologetic_tone():
    block = select_tone_block("negative", _a().config, enabled=True)
    assert "measured" in block.lower()
    assert "apologetic" in block.lower()
    assert block != STATIC_TONE_PARAGRAPH


def test_an_urgent_sentiment_selects_the_urgency_acknowledging_tone():
    block = select_tone_block("urgent", _a().config, enabled=True)
    assert "urgency" in block.lower()
    assert block != STATIC_TONE_PARAGRAPH


def test_a_neutral_sentiment_selects_the_default_tone():
    # Neutral reproduces today's wording verbatim -- enabling the flag must
    # not change anything for the common (non-negative, non-urgent) case.
    block = select_tone_block("neutral", _a().config, enabled=True)
    assert block == STATIC_TONE_PARAGRAPH


def test_an_operator_edited_tone_block_is_used_over_the_default():
    edited = "Offer a goodwill voucher and personally apologise by name."
    config = _a(tone_negative=edited).config
    block = select_tone_block("negative", config, enabled=True)
    assert edited in block
    # The built-in default wording must not also be present -- the operator
    # edit replaces the default slot, it doesn't merge with it.
    assert "measured, calm, and apologetic" not in block.lower()


class _BrokenConfig:
    """Stands in for a config object a broken tenant-store read resolved to.

    A real `AssistantConfig` field access never raises, but the whole point
    of `select_tone_block`'s fail-open guarantee is to survive something
    upstream going wrong regardless -- this simulates that.
    """

    @property
    def tone_negative(self) -> str:
        raise RuntimeError("tenant store unavailable")


def test_a_tenant_store_outage_falls_back_to_the_static_paragraph():
    block = select_tone_block("negative", _BrokenConfig(), enabled=True)
    assert block == STATIC_TONE_PARAGRAPH


def test_the_flag_off_produces_the_exact_static_paragraph_used_today():
    # Even with a sentiment AND an operator edit present, the flag being off
    # must win: byte-identical to today's wording, no exceptions.
    config = _a(tone_negative="Custom wording that must be ignored").config
    block = select_tone_block("negative", config, enabled=False)
    assert block == STATIC_TONE_PARAGRAPH


def test_the_tone_block_augments_and_never_replaces_the_agent_instruction():
    out = compose_chat_agent_instruction(
        BASE,
        _a(),
        sentiment="urgent",
        tone_adjustment_enabled=True,
    )
    # The base instruction (everything the agent needs to function) must
    # still be there, verbatim, at the front -- the tone block is additive.
    assert out.startswith(BASE)
    assert "urgency" in out.lower()
