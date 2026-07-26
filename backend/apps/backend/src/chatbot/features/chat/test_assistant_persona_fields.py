"""TDD: Task 1 — new AssistantConfig persona fields.

Tests that the 8 new persona fields (language + 7 lifecycle-message fields)
exist on AssistantConfig with "" defaults, survive an InMemoryAssistantsStore
roundtrip, and that a PUT config-merge preserves untouched fields.
"""

from chatbot.features.chat.adapters.assistants_store import AssistantConfig, InMemoryAssistantsStore


def test_new_persona_fields_default_empty() -> None:
    c = AssistantConfig()
    assert c.language == ""
    for f in (
        "idle_warning_message",
        "idle_close_message",
        "resolution_prompt_message",
        "survey_ai_message",
        "survey_agent_message",
        "thanks_message",
        "assign_agent_message",
    ):
        assert getattr(c, f) == ""


async def test_new_fields_survive_store_roundtrip() -> None:
    store = InMemoryAssistantsStore()
    a = await store.get_default()
    await store.update(a.id, {"config": {"language": "Bahasa Melayu", "thanks_message": "Terima kasih!"}})
    got = await store.get(a.id)
    assert got is not None
    assert got.config.language == "Bahasa Melayu"
    assert got.config.thanks_message == "Terima kasih!"
    # untouched fields keep defaults
    assert got.config.idle_warning_message == ""
