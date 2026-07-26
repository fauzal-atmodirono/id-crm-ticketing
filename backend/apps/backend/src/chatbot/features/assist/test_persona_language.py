"""Tests: persona language injection into copilot + assist prompts."""

from chatbot.features.assist.assistant_runtime import build_system_prompt
from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig


def _assistant(**cfg) -> Assistant:
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


def test_language_section_added_when_set() -> None:
    p = build_system_prompt(_assistant(language="Bahasa Melayu"))
    assert "Always respond in Bahasa Melayu." in p


def test_no_language_section_when_empty() -> None:
    p = build_system_prompt(_assistant())
    assert "Always respond in" not in p
