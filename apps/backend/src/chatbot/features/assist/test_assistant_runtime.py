"""Tests for assistant_runtime — resolve_assistant, build_system_prompt,
build_tool_declarations.

Key invariant: the seeded default assistant must produce the exact same
system_instruction and tool declarations as the old hardcoded path, so the
no-assistant_id copilot path is behaviour-preserving.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chatbot.features.assist.assistant_runtime import (
    DEFAULT_COPILOT_PROMPT,
    build_system_prompt,
    build_tool_declarations,
    resolve_assistant,
)
from chatbot.features.assist.copilot_tools import COPILOT_TOOLS
from chatbot.features.chat.adapters.assistants_store import (
    Assistant,
    AssistantConfig,
    InMemoryAssistantsStore,
)

# ---------------------------------------------------------------------------
# resolve_assistant
# ---------------------------------------------------------------------------


async def test_resolve_no_id_returns_default() -> None:
    store = InMemoryAssistantsStore()
    assistant = await resolve_assistant(store, None)
    assert assistant.is_default is True


async def test_resolve_unknown_id_falls_back_to_default() -> None:
    store = InMemoryAssistantsStore()
    assistant = await resolve_assistant(store, "nonexistent-id")
    assert assistant.is_default is True


async def test_resolve_known_id_returns_it() -> None:
    store = InMemoryAssistantsStore()
    known = Assistant(
        id="asst_abc123",
        name="My Assistant",
        description="",
        product_name="",
        config=AssistantConfig(instructions="Be helpful."),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    await store.create(known)

    result = await resolve_assistant(store, "asst_abc123")
    assert result.id == "asst_abc123"
    assert result.name == "My Assistant"


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


def _assistant_with_instructions(instructions: str) -> Assistant:
    return Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name="",
        config=AssistantConfig(instructions=instructions),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def test_build_system_prompt_empty_instructions_uses_default() -> None:
    assistant = _assistant_with_instructions("")
    assert build_system_prompt(assistant) == DEFAULT_COPILOT_PROMPT


def test_build_system_prompt_whitespace_only_uses_default() -> None:
    assistant = _assistant_with_instructions("   \n  ")
    assert build_system_prompt(assistant) == DEFAULT_COPILOT_PROMPT


def test_build_system_prompt_non_empty_uses_instructions() -> None:
    assistant = _assistant_with_instructions("  You are a specialist.  ")
    assert build_system_prompt(assistant) == "You are a specialist."


# ---------------------------------------------------------------------------
# build_tool_declarations
# ---------------------------------------------------------------------------


def _assistant_with_tools(tools: list[str]) -> Assistant:
    return Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name="",
        config=AssistantConfig(enabled_builtin_tools=tools),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def test_build_tool_declarations_empty_list_returns_all() -> None:
    assistant = _assistant_with_tools([])
    result = build_tool_declarations(assistant)
    assert result == COPILOT_TOOLS


def test_build_tool_declarations_subset_returns_only_those() -> None:
    assistant = _assistant_with_tools(["search_knowledge_base", "get_contact_details"])
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert set(names) == {"search_knowledge_base", "get_contact_details"}
    # Order must follow original COPILOT_TOOLS order.
    copilot_order = [t["name"] for t in COPILOT_TOOLS if t["name"] in names]
    assert names == copilot_order


def test_build_tool_declarations_single_tool() -> None:
    assistant = _assistant_with_tools(["get_conversation_transcript"])
    result = build_tool_declarations(assistant)
    assert len(result) == 1
    assert result[0]["name"] == "get_conversation_transcript"


# ---------------------------------------------------------------------------
# Behaviour-preservation: default assistant reproduces the old hardcoded path
# ---------------------------------------------------------------------------


async def test_default_assistant_preserves_system_prompt_and_tools() -> None:
    """The seeded default assistant must yield exactly the same system_instruction
    and tool declarations as the old hardcoded _SYSTEM / COPILOT_TOOLS, so the
    no-assistant_id copilot path is unchanged.
    """
    store = InMemoryAssistantsStore()
    assistant = await resolve_assistant(store, None)

    assert build_system_prompt(assistant) == DEFAULT_COPILOT_PROMPT
    assert build_tool_declarations(assistant) == COPILOT_TOOLS
