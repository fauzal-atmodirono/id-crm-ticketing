"""Tests for inbox_resolver.effective_assignment."""
from __future__ import annotations

import pytest

from chatbot.features.chat.adapters.assistants_store import InMemoryAssistantsStore
from chatbot.features.chat.adapters.inbox_assignment_store import InMemoryInboxAssignmentStore
from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.features.chat.inbox_resolver import effective_assignment
from chatbot.platform.config import Settings


@pytest.fixture()
def settings() -> Settings:
    return Settings(chatwoot_enabled=False)


@pytest.fixture()
def assignment_store() -> InMemoryInboxAssignmentStore:
    return InMemoryInboxAssignmentStore()


@pytest.fixture()
def assistants_store() -> InMemoryAssistantsStore:
    return InMemoryAssistantsStore()


@pytest.fixture()
def tenant_store() -> InMemoryTenantSettingsStore:
    return InMemoryTenantSettingsStore()


async def test_no_assignment_returns_default(
    assignment_store: InMemoryInboxAssignmentStore,
    assistants_store: InMemoryAssistantsStore,
    tenant_store: InMemoryTenantSettingsStore,
    settings: Settings,
) -> None:
    """An inbox with no stored assignment returns the default assistant + mode."""
    eff = await effective_assignment(
        assignment_store, assistants_store, tenant_store, settings, inbox_id=42
    )
    default = await assistants_store.get_default()
    assert eff["assistant_id"] == default.id
    assert eff["mode"] == "suggest"  # hardcoded default from settings_facade
    assert eff["source"] == "default"


async def test_stored_assignment_returned_as_override(
    assignment_store: InMemoryInboxAssignmentStore,
    assistants_store: InMemoryAssistantsStore,
    tenant_store: InMemoryTenantSettingsStore,
    settings: Settings,
) -> None:
    """A stored assignment is returned with source='override'."""
    await assignment_store.set(10, "asst_abc", "off")
    eff = await effective_assignment(
        assignment_store, assistants_store, tenant_store, settings, inbox_id=10
    )
    assert eff["assistant_id"] == "asst_abc"
    assert eff["mode"] == "off"
    assert eff["source"] == "override"


async def test_tenant_override_default_mode_applied(
    assignment_store: InMemoryInboxAssignmentStore,
    assistants_store: InMemoryAssistantsStore,
    tenant_store: InMemoryTenantSettingsStore,
    settings: Settings,
) -> None:
    """Tenant-level default_mode override is respected for the 'default' path."""
    await tenant_store.set_overrides({"default_mode": "auto"})
    eff = await effective_assignment(
        assignment_store, assistants_store, tenant_store, settings, inbox_id=99
    )
    assert eff["mode"] == "auto"
    assert eff["source"] == "default"


async def test_delete_reverts_to_default(
    assignment_store: InMemoryInboxAssignmentStore,
    assistants_store: InMemoryAssistantsStore,
    tenant_store: InMemoryTenantSettingsStore,
    settings: Settings,
) -> None:
    """After deleting an assignment the inbox reverts to default."""
    await assignment_store.set(5, "asst_xyz", "auto")
    await assignment_store.delete(5)
    eff = await effective_assignment(
        assignment_store, assistants_store, tenant_store, settings, inbox_id=5
    )
    assert eff["source"] == "default"


async def test_delete_for_assistant_clears_assignments(
    assignment_store: InMemoryInboxAssignmentStore,
    assistants_store: InMemoryAssistantsStore,  # noqa: ARG001
    tenant_store: InMemoryTenantSettingsStore,  # noqa: ARG001
    settings: Settings,  # noqa: ARG001
) -> None:
    """delete_for_assistant removes all inboxes assigned to that assistant."""
    await assignment_store.set(1, "asst_gone", "suggest")
    await assignment_store.set(2, "asst_gone", "auto")
    await assignment_store.set(3, "asst_other", "off")

    await assignment_store.delete_for_assistant("asst_gone")

    assert await assignment_store.get(1) is None
    assert await assignment_store.get(2) is None
    # Other assistant's assignment is untouched.
    assert (await assignment_store.get(3)) is not None
