"""Per-assistant runtime helpers for the copilot.

Resolves an Assistant from the store (with default fallback), then
builds the system prompt and tool declarations for a single Gemini call.

Custom webhook tools and per-assistant persona additions (guardrails,
scenarios) are handled by later tasks — this module covers only the
system prompt and built-in tool subset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantsStorePort

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


def build_system_prompt(assistant: Assistant) -> str:
    """Return the system prompt to use for *assistant*.

    Non-empty instructions on the assistant take precedence; otherwise
    the canonical DEFAULT_COPILOT_PROMPT is used so the no-assistant_id
    path reproduces existing behaviour exactly.

    After the base prompt, persona additions are appended in this order
    (only when non-empty):
      - Product: <product_name>
      - ## Guardrails section (one bullet per guardrail)
      - ## Response guidelines section (one bullet per guideline)
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

    return "\n".join(parts)


def build_tool_declarations(assistant: Assistant) -> list[dict[str, Any]]:
    """Return the function-declaration dicts for the enabled built-in tools.

    If *enabled_builtin_tools* is empty/falsy, ALL of COPILOT_TOOLS are
    returned (safe default — preserves existing behaviour for the seeded
    default assistant whose list is populated with all names).

    Original COPILOT_TOOLS order is preserved; only the subset whose name
    appears in *enabled_builtin_tools* is included when the list is non-empty.

    Additionally, if ``feature_faq`` is False the ``search_knowledge_base``
    tool is excluded from the result regardless of the enabled-tools list.
    """
    enabled = assistant.config.enabled_builtin_tools
    if not enabled:
        tools = list(COPILOT_TOOLS)
    else:
        enabled_set = set(enabled)
        tools = [t for t in COPILOT_TOOLS if t["name"] in enabled_set]

    if not assistant.config.feature_faq:
        tools = [t for t in tools if t["name"] != "search_knowledge_base"]

    return tools
