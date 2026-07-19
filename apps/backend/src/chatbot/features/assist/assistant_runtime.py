"""Per-assistant runtime helpers for the copilot.

Resolves an Assistant from the store (with default fallback), then
builds the system prompt and tool declarations for a single Gemini call.

Custom webhook tools are assembled by passing a pre-fetched list of
CustomTool objects to build_tool_declarations; this keeps the function
sync (no store I/O) and lets the caller (copilot_router) await the store
once, outside the tight config-build path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantsStorePort
    from chatbot.features.chat.adapters.scenarios_store import Scenario
    from chatbot.features.chat.adapters.tools_store import CustomTool

from chatbot.features.assist.copilot_tools import COPILOT_TOOLS

# The canonical system prompt — verbatim move from copilot_router._SYSTEM.
DEFAULT_COPILOT_PROMPT = (
    "You are Copilot, an assistant for a Proton Holdings SUPPORT AGENT (not the "
    "customer). Help the agent by answering their questions about the current "
    "conversation and customer. Use the provided tools to read the transcript "
    "(including private notes), the contact's details and custom attributes, the "
    "customer's past conversations, and the knowledge base before answering. "
    "Be concise and factual; if the tools don't contain the answer, say so. "
    "Reply in the language the agent used."
)


async def resolve_assistant(store: AssistantsStorePort, assistant_id: str | None) -> Assistant:
    """Return the assistant for *assistant_id*, falling back to the default.

    - No id supplied → get_default().
    - Id supplied but unknown → fall back to get_default() (defensive).
    - Id supplied and found → return it.
    """
    if assistant_id:
        found = await store.get(assistant_id)
        if found is not None:
            return found
    return await store.get_default()


def build_system_prompt(
    assistant: Assistant,
    scenarios: list[Scenario] | None = None,
) -> str:
    """Return the system prompt to use for *assistant*.

    Non-empty instructions on the assistant take precedence; otherwise
    the canonical DEFAULT_COPILOT_PROMPT is used so the no-assistant_id
    path reproduces existing behaviour exactly.

    After the base prompt, persona additions are appended in this order
    (only when non-empty):
      - Product: <product_name>
      - ## Guardrails section (one bullet per guardrail)
      - ## Response guidelines section (one bullet per guideline)
      - ## Playbooks section (one subsection per *enabled* scenario)

    *scenarios* is the pre-fetched list for the resolved assistant.  None or
    an empty list → no Playbooks section (behaviour-preserving for callers that
    do not pass scenarios).
    """
    instructions = assistant.config.instructions
    base = instructions.strip() if (instructions and instructions.strip()) else DEFAULT_COPILOT_PROMPT

    parts: list[str] = [base]

    if assistant.product_name:
        parts.append(f"Product: {assistant.product_name}")

    guardrails = assistant.config.guardrails
    if guardrails:
        section = "## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails)
        parts.append(section)

    guidelines = assistant.config.response_guidelines
    if guidelines:
        section = "## Response guidelines\n" + "\n".join(f"- {g}" for g in guidelines)
        parts.append(section)

    enabled_scenarios = [s for s in (scenarios or []) if s.enabled]
    if enabled_scenarios:
        playbook_lines = ["## Playbooks"]
        for s in enabled_scenarios:
            playbook_lines.append(f"### {s.title} — {s.description}")
            playbook_lines.append(s.instruction)
            if s.tools:
                playbook_lines.append(f"Allowed tools: {', '.join(s.tools)}")
        parts.append("\n".join(playbook_lines))

    return "\n".join(parts)


def build_tool_declarations(
    assistant: Assistant,
    custom_tools: list[CustomTool] | None = None,
) -> list[dict[str, Any]]:
    """Return the function-declaration dicts for the enabled tools.

    Built-in tools
    --------------
    If *enabled_builtin_tools* is empty/falsy, ALL of COPILOT_TOOLS are
    returned (safe default — preserves existing behaviour for the seeded
    default assistant whose list is populated with all names).

    Original COPILOT_TOOLS order is preserved; only the subset whose name
    appears in *enabled_builtin_tools* is included when the list is non-empty.

    Additionally, if ``feature_faq`` is False the ``search_knowledge_base``
    tool is excluded from the result regardless of the enabled-tools list.

    Custom webhook tools
    --------------------
    *custom_tools* is a pre-fetched list of all CustomTool objects for the
    tenant (typically ``await tools_store.list_custom()``).  For each slug in
    ``assistant.config.enabled_custom_tools``, if a matching CustomTool is
    found in *custom_tools* AND it is enabled, a function declaration is
    appended after the built-ins.  Passing None (default) → built-ins only,
    which is fully back-compatible with callers that predate custom tools.
    """
    enabled = assistant.config.enabled_builtin_tools
    if not enabled:
        tools: list[dict[str, Any]] = list(COPILOT_TOOLS)
    else:
        enabled_set = set(enabled)
        tools = [t for t in COPILOT_TOOLS if t["name"] in enabled_set]

    if not assistant.config.feature_faq:
        tools = [t for t in tools if t["name"] != "search_knowledge_base"]

    if custom_tools is not None:
        custom_by_slug = {ct.slug: ct for ct in custom_tools}
        for slug in (assistant.config.enabled_custom_tools or []):
            ct = custom_by_slug.get(slug)
            if ct is not None and ct.enabled:
                tools.append(
                    {
                        "name": ct.slug,
                        "description": ct.description,
                        "parameters": ct.param_schema,
                    }
                )

    return tools
