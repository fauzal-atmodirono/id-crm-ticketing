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


# ---------------------------------------------------------------------------
# build_system_prompt — persona additions
# ---------------------------------------------------------------------------


def _assistant_with_persona(
    product_name: str = "",
    guardrails: list[str] | None = None,
    response_guidelines: list[str] | None = None,
    instructions: str = "",
) -> Assistant:
    return Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name=product_name,
        config=AssistantConfig(
            instructions=instructions,
            guardrails=guardrails or [],
            response_guidelines=response_guidelines or [],
        ),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def test_build_system_prompt_no_persona_returns_base_only() -> None:
    """When product_name, guardrails, and response_guidelines are all empty,
    the prompt is exactly the base (DEFAULT_COPILOT_PROMPT) — behaviour-preserving.
    """
    assistant = _assistant_with_persona()
    assert build_system_prompt(assistant) == DEFAULT_COPILOT_PROMPT


def test_build_system_prompt_appends_product_name() -> None:
    assistant = _assistant_with_persona(product_name="Proton Mail")
    result = build_system_prompt(assistant)
    assert result.startswith(DEFAULT_COPILOT_PROMPT)
    assert "Product: Proton Mail" in result


def test_build_system_prompt_appends_guardrails() -> None:
    assistant = _assistant_with_persona(guardrails=["Never disclose PII", "Stay on topic"])
    result = build_system_prompt(assistant)
    assert "## Guardrails" in result
    assert "- Never disclose PII" in result
    assert "- Stay on topic" in result


def test_build_system_prompt_appends_response_guidelines() -> None:
    assistant = _assistant_with_persona(response_guidelines=["Be concise", "Use bullet points"])
    result = build_system_prompt(assistant)
    assert "## Response guidelines" in result
    assert "- Be concise" in result
    assert "- Use bullet points" in result


def test_build_system_prompt_full_persona_order() -> None:
    """All persona sections must appear in the correct order after the base."""
    assistant = _assistant_with_persona(
        product_name="Proton Drive",
        guardrails=["G1"],
        response_guidelines=["R1"],
    )
    result = build_system_prompt(assistant)
    product_pos = result.index("Product: Proton Drive")
    guardrails_pos = result.index("## Guardrails")
    guidelines_pos = result.index("## Response guidelines")
    assert product_pos < guardrails_pos < guidelines_pos


def test_build_system_prompt_custom_instructions_plus_persona() -> None:
    """Custom instructions still get persona sections appended."""
    assistant = _assistant_with_persona(
        instructions="You are a specialist.",
        product_name="Proton VPN",
    )
    result = build_system_prompt(assistant)
    assert result.startswith("You are a specialist.")
    assert "Product: Proton VPN" in result


def test_build_system_prompt_empty_guardrails_not_appended() -> None:
    """An empty guardrails list must NOT produce a ## Guardrails section."""
    assistant = _assistant_with_persona(guardrails=[])
    assert "## Guardrails" not in build_system_prompt(assistant)


def test_build_system_prompt_empty_response_guidelines_not_appended() -> None:
    """An empty response_guidelines list must NOT produce a ## Response guidelines section."""
    assistant = _assistant_with_persona(response_guidelines=[])
    assert "## Response guidelines" not in build_system_prompt(assistant)


# ---------------------------------------------------------------------------
# build_tool_declarations — feature_faq gate
# ---------------------------------------------------------------------------


def _assistant_with_feature_faq(feature_faq: bool) -> Assistant:
    return Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name="",
        config=AssistantConfig(feature_faq=feature_faq),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def test_build_tool_declarations_feature_faq_false_drops_search_knowledge_base() -> None:
    assistant = _assistant_with_feature_faq(False)
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "search_knowledge_base" not in names


def test_build_tool_declarations_feature_faq_true_keeps_search_knowledge_base() -> None:
    assistant = _assistant_with_feature_faq(True)
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "search_knowledge_base" in names


def test_build_tool_declarations_feature_faq_default_keeps_search_knowledge_base() -> None:
    """feature_faq defaults to True — search_knowledge_base must be present."""
    assistant = _assistant_with_tools([t["name"] for t in COPILOT_TOOLS])
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "search_knowledge_base" in names
