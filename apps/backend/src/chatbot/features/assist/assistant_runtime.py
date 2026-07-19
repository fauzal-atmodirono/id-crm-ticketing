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
    """
    instructions = assistant.config.instructions
    if instructions and instructions.strip():
        return instructions.strip()
    return DEFAULT_COPILOT_PROMPT


def build_tool_declarations(assistant: Assistant) -> list[dict[str, Any]]:
    """Return the function-declaration dicts for the enabled built-in tools.

    If *enabled_builtin_tools* is empty/falsy, ALL of COPILOT_TOOLS are
    returned (safe default — preserves existing behaviour for the seeded
    default assistant whose list is populated with all names).

    Original COPILOT_TOOLS order is preserved; only the subset whose name
    appears in *enabled_builtin_tools* is included when the list is non-empty.
    """
    enabled = assistant.config.enabled_builtin_tools
    if not enabled:
        return list(COPILOT_TOOLS)
    enabled_set = set(enabled)
    return [t for t in COPILOT_TOOLS if t["name"] in enabled_set]
