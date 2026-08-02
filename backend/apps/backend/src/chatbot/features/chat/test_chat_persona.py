from chatbot.features.chat.chat_persona import compose_chat_agent_instruction
from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig

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
