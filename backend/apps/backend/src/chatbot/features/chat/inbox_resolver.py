"""Effective inbox assignment resolver.

Given an inbox_id, resolves the assistant + mode that should handle it:
- If the assignment store has an explicit entry → "override" source.
- Else → default assistant + tenant default_mode from settings → "default" source.

Used by the copilot gating path and the GET /kb/inboxes join.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.assistants_store import AssistantsStorePort
    from chatbot.features.chat.adapters.inbox_assignment_store import InboxAssignmentStorePort
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
    from chatbot.platform.config import Settings


async def effective_assignment(
    assignment_store: InboxAssignmentStorePort,
    assistants_store: AssistantsStorePort,
    tenant_settings_store: TenantSettingsStorePort,
    settings: Settings,
    inbox_id: int,
) -> dict[str, Any]:
    """Resolve the effective {assistant_id, mode, source} for an inbox.

    If the assignment store has an explicit entry for inbox_id, that entry is
    returned with source="override". Otherwise the tenant default assistant and
    default_mode are returned with source="default". Never raises.
    """
    from chatbot.features.chat.settings_facade import get_effective_value  # noqa: PLC0415

    stored = await assignment_store.get(inbox_id)
    if stored is not None:
        return {
            "assistant_id": stored["assistant_id"],
            "mode": stored["mode"],
            "source": "override",
        }

    default_assistant = await assistants_store.get_default()
    default_mode = await get_effective_value(tenant_settings_store, settings, "default_mode")
    return {
        "assistant_id": default_assistant.id,
        "mode": default_mode,
        "source": "default",
    }
