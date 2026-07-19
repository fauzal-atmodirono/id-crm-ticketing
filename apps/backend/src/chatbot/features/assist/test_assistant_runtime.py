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


# ---------------------------------------------------------------------------
# build_tool_declarations — feature_memory gate
# ---------------------------------------------------------------------------


def _assistant_with_feature_memory(feature_memory: bool) -> Assistant:
    return Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name="",
        config=AssistantConfig(feature_memory=feature_memory),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def test_build_tool_declarations_feature_memory_false_drops_list_past_conversations() -> None:
    """feature_memory=False must drop list_past_conversations and only that tool."""
    assistant = _assistant_with_feature_memory(False)
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "list_past_conversations" not in names
    # All other built-ins must still be present.
    assert "get_conversation_transcript" in names
    assert "get_contact_details" in names
    assert "search_knowledge_base" in names


def test_build_tool_declarations_feature_memory_true_keeps_list_past_conversations() -> None:
    """feature_memory=True must keep list_past_conversations."""
    assistant = _assistant_with_feature_memory(True)
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "list_past_conversations" in names


def test_build_tool_declarations_feature_memory_default_keeps_list_past_conversations() -> None:
    """feature_memory defaults to True — list_past_conversations must be present."""
    assistant = _assistant_with_tools([t["name"] for t in COPILOT_TOOLS])
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "list_past_conversations" in names


# ---------------------------------------------------------------------------
# build_tool_declarations — feature_contact_attributes gate
# ---------------------------------------------------------------------------


def _assistant_with_feature_contact_attributes(feature_contact_attributes: bool) -> Assistant:
    return Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name="",
        config=AssistantConfig(feature_contact_attributes=feature_contact_attributes),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def test_build_tool_declarations_feature_contact_attributes_false_drops_get_contact_details() -> None:
    """feature_contact_attributes=False must drop get_contact_details and only that tool."""
    assistant = _assistant_with_feature_contact_attributes(False)
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "get_contact_details" not in names
    # All other built-ins must still be present.
    assert "get_conversation_transcript" in names
    assert "list_past_conversations" in names
    assert "search_knowledge_base" in names


def test_build_tool_declarations_feature_contact_attributes_true_keeps_get_contact_details() -> None:
    """feature_contact_attributes=True must keep get_contact_details."""
    assistant = _assistant_with_feature_contact_attributes(True)
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "get_contact_details" in names


def test_build_tool_declarations_feature_contact_attributes_default_keeps_get_contact_details() -> None:
    """feature_contact_attributes defaults to True — get_contact_details must be present."""
    assistant = _assistant_with_tools([t["name"] for t in COPILOT_TOOLS])
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    assert "get_contact_details" in names


def test_build_tool_declarations_all_features_true_returns_full_set() -> None:
    """When all feature flags are True, the full COPILOT_TOOLS set is returned."""
    assistant = Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name="",
        config=AssistantConfig(
            feature_faq=True,
            feature_memory=True,
            feature_contact_attributes=True,
        ),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    result = build_tool_declarations(assistant)
    names = [t["name"] for t in result]
    for tool in COPILOT_TOOLS:
        assert tool["name"] in names


# ---------------------------------------------------------------------------
# build_tool_declarations — custom webhook tools
# ---------------------------------------------------------------------------

from chatbot.features.chat.adapters.tools_store import CustomTool  # noqa: E402


def _make_custom_tool(slug: str, enabled: bool = True) -> CustomTool:
    return CustomTool(
        slug=slug,
        title=slug.replace("_", " ").title(),
        description=f"Description for {slug}",
        endpoint_url="https://example.com/hook",
        http_method="POST",
        auth_type="none",
        param_schema={"type": "object", "properties": {}},
        enabled=enabled,
    )


def _assistant_with_custom_tools(slugs: list[str]) -> Assistant:
    return Assistant(
        id="asst_test",
        name="Test",
        description="",
        product_name="",
        config=AssistantConfig(enabled_custom_tools=slugs),
        enabled=True,
        is_default=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def test_build_tool_declarations_appends_enabled_custom_tool() -> None:
    """An enabled custom tool in enabled_custom_tools is appended after built-ins."""
    ct = _make_custom_tool("custom_check_order")
    assistant = _assistant_with_custom_tools(["custom_check_order"])
    result = build_tool_declarations(assistant, custom_tools=[ct])
    names = [t["name"] for t in result]
    assert "custom_check_order" in names
    # Built-ins still present (feature_faq default True).
    assert "search_knowledge_base" in names


def test_build_tool_declarations_excludes_disabled_custom_tool() -> None:
    """A disabled custom tool must NOT appear in declarations even if in enabled_custom_tools."""
    ct = _make_custom_tool("custom_check_order", enabled=False)
    assistant = _assistant_with_custom_tools(["custom_check_order"])
    result = build_tool_declarations(assistant, custom_tools=[ct])
    names = [t["name"] for t in result]
    assert "custom_check_order" not in names


def test_build_tool_declarations_excludes_not_in_enabled_list() -> None:
    """A custom tool not in assistant's enabled_custom_tools is excluded."""
    ct = _make_custom_tool("custom_check_order")
    assistant = _assistant_with_custom_tools([])  # empty → not opted in
    result = build_tool_declarations(assistant, custom_tools=[ct])
    names = [t["name"] for t in result]
    assert "custom_check_order" not in names


def test_build_tool_declarations_none_custom_tools_returns_builtins_only() -> None:
    """Passing custom_tools=None is back-compat and returns built-ins only."""
    assistant = _assistant_with_tools([])
    result = build_tool_declarations(assistant, custom_tools=None)
    assert result == COPILOT_TOOLS


def test_build_tool_declarations_custom_tool_declaration_shape() -> None:
    """Custom tool declaration must expose name, description, and parameters."""
    param_schema = {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}
    ct = CustomTool(
        slug="custom_check_order",
        title="Check Order",
        description="Checks an order status.",
        endpoint_url="https://api.example.com/orders",
        http_method="GET",
        auth_type="none",
        param_schema=param_schema,
        enabled=True,
    )
    assistant = _assistant_with_custom_tools(["custom_check_order"])
    result = build_tool_declarations(assistant, custom_tools=[ct])
    custom_decl = next(t for t in result if t["name"] == "custom_check_order")
    assert custom_decl["description"] == "Checks an order status."
    assert custom_decl["parameters"] == param_schema


# ---------------------------------------------------------------------------
# build_system_prompt — scenarios (Playbooks section)
# ---------------------------------------------------------------------------

from chatbot.features.chat.adapters.scenarios_store import Scenario  # noqa: E402


def _make_scenario(
    title: str,
    description: str,
    instruction: str,
    tools: list[str] | None = None,
    enabled: bool = True,
) -> Scenario:
    return Scenario(
        id=f"scen_{title.lower().replace(' ', '_')}",
        assistant_id="asst_test",
        title=title,
        description=description,
        instruction=instruction,
        tools=tools or [],
        enabled=enabled,
    )


def test_build_system_prompt_none_scenarios_no_playbooks_section() -> None:
    """scenarios=None must not produce a ## Playbooks section (behaviour-preserving)."""
    assistant = _assistant_with_instructions("")
    result = build_system_prompt(assistant, scenarios=None)
    assert "## Playbooks" not in result
    assert result == DEFAULT_COPILOT_PROMPT


def test_build_system_prompt_empty_scenarios_no_playbooks_section() -> None:
    """An empty scenarios list must not produce a ## Playbooks section."""
    assistant = _assistant_with_instructions("")
    result = build_system_prompt(assistant, scenarios=[])
    assert "## Playbooks" not in result
    assert result == DEFAULT_COPILOT_PROMPT


def test_build_system_prompt_enabled_scenario_appears_in_playbooks() -> None:
    """An enabled scenario must appear in the ## Playbooks section."""
    assistant = _assistant_with_instructions("")
    s = _make_scenario(
        title="Warranty Claim",
        description="Handle warranty requests",
        instruction="Ask for proof of purchase.",
        tools=["search_knowledge_base"],
        enabled=True,
    )
    result = build_system_prompt(assistant, scenarios=[s])
    assert "## Playbooks" in result
    assert "### Warranty Claim — Handle warranty requests" in result
    assert "Ask for proof of purchase." in result
    assert "Allowed tools: search_knowledge_base" in result


def test_build_system_prompt_disabled_scenario_excluded() -> None:
    """A disabled scenario must NOT appear in the Playbooks section."""
    assistant = _assistant_with_instructions("")
    enabled_s = _make_scenario("Active", "Active desc", "Do this.", enabled=True)
    disabled_s = _make_scenario("Inactive", "Inactive desc", "Never shown.", enabled=False)
    result = build_system_prompt(assistant, scenarios=[enabled_s, disabled_s])
    assert "Active" in result
    assert "Inactive" not in result
    assert "Never shown." not in result


def test_build_system_prompt_all_disabled_scenarios_no_playbooks() -> None:
    """If all scenarios are disabled, no ## Playbooks section is emitted."""
    assistant = _assistant_with_instructions("")
    s = _make_scenario("Disabled", "desc", "instruction", enabled=False)
    result = build_system_prompt(assistant, scenarios=[s])
    assert "## Playbooks" not in result


def test_build_system_prompt_scenario_no_tools_omits_allowed_tools_line() -> None:
    """When a scenario has no tools, the 'Allowed tools:' line must NOT appear."""
    assistant = _assistant_with_instructions("")
    s = _make_scenario("NoTools", "desc", "instruction", tools=[])
    result = build_system_prompt(assistant, scenarios=[s])
    assert "Allowed tools:" not in result


def test_build_system_prompt_playbooks_appended_after_persona() -> None:
    """Playbooks must appear after product/guardrails/guidelines in the prompt."""
    assistant = _assistant_with_persona(
        product_name="Proton Mail",
        guardrails=["G1"],
        response_guidelines=["R1"],
    )
    s = _make_scenario("Warranty", "desc", "instruction", enabled=True)
    result = build_system_prompt(assistant, scenarios=[s])
    guidelines_pos = result.index("## Response guidelines")
    playbooks_pos = result.index("## Playbooks")
    assert playbooks_pos > guidelines_pos


def test_build_system_prompt_multiple_enabled_scenarios() -> None:
    """All enabled scenarios appear in the Playbooks section."""
    assistant = _assistant_with_instructions("")
    s1 = _make_scenario("S1", "d1", "i1", enabled=True)
    s2 = _make_scenario("S2", "d2", "i2", enabled=True)
    result = build_system_prompt(assistant, scenarios=[s1, s2])
    assert "### S1 — d1" in result
    assert "### S2 — d2" in result
