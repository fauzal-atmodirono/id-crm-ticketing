"""Tests for InMemoryInboxTimingStore (round-trip, partial, delete, keys)."""
from __future__ import annotations

from chatbot.features.chat.adapters.inbox_timing_store import (
    TIMING_KEYS,
    MESSAGE_KEY,
    MESSAGE_KEYS,
    ENABLED_KEY,
    InboxTimingStorePort,
    InMemoryInboxTimingStore,
)


def test_timing_keys_are_the_four_lifecycle_fields():
    assert TIMING_KEYS == (
        "idle_warn_minutes",
        "idle_close_grace_minutes",
        "idle_close_out_of_hours_grace_minutes",
        "confirm_grace_minutes",
    )


def test_inmemory_satisfies_port():
    assert isinstance(InMemoryInboxTimingStore(), InboxTimingStorePort)


async def test_set_get_roundtrip_full():
    store = InMemoryInboxTimingStore()
    await store.set(7, {
        "idle_warn_minutes": 12,
        "idle_close_grace_minutes": 3,
        "idle_close_out_of_hours_grace_minutes": 0,
        "confirm_grace_minutes": 8,
    })
    assert await store.get(7) == {
        "idle_warn_minutes": 12,
        "idle_close_grace_minutes": 3,
        "idle_close_out_of_hours_grace_minutes": 0,
        "confirm_grace_minutes": 8,
    }


async def test_set_partial_stores_only_given_keys():
    store = InMemoryInboxTimingStore()
    await store.set(7, {"idle_warn_minutes": 15})
    assert await store.get(7) == {"idle_warn_minutes": 15}


async def test_get_missing_returns_none():
    store = InMemoryInboxTimingStore()
    assert await store.get(999) is None


async def test_get_all_returns_copies():
    store = InMemoryInboxTimingStore()
    await store.set(1, {"confirm_grace_minutes": 5})
    all_ = await store.get_all()
    assert all_ == {1: {"confirm_grace_minutes": 5}}
    all_[1]["confirm_grace_minutes"] = 999  # mutating the copy must not leak
    assert await store.get(1) == {"confirm_grace_minutes": 5}


async def test_delete_removes_entry():
    store = InMemoryInboxTimingStore()
    await store.set(1, {"idle_warn_minutes": 10})
    await store.delete(1)
    assert await store.get(1) is None
    await store.delete(1)  # idempotent, no raise


async def test_roundtrip_message_and_enabled():
    store = InMemoryInboxTimingStore()
    await store.set(7, {
        "idle_warn_minutes": 3,
        MESSAGE_KEY: "Closing in {{minutes}} min.",
        ENABLED_KEY: False,
    })
    assert await store.get(7) == {
        "idle_warn_minutes": 3,
        MESSAGE_KEY: "Closing in {{minutes}} min.",
        ENABLED_KEY: False,
    }


async def test_partial_only_enabled():
    store = InMemoryInboxTimingStore()
    await store.set(7, {ENABLED_KEY: True})
    assert await store.get(7) == {ENABLED_KEY: True}


async def test_wrong_types_dropped():
    store = InMemoryInboxTimingStore()
    # message must be str, enabled must be bool — wrong types are ignored.
    await store.set(7, {MESSAGE_KEY: 123, ENABLED_KEY: "yes", "idle_warn_minutes": 5})
    assert await store.get(7) == {"idle_warn_minutes": 5}


def test_message_keys_are_the_seven():
    assert MESSAGE_KEYS == (
        "idle_warning_message", "idle_close_message", "resolution_prompt_message",
        "assign_agent_message", "survey_ai_message", "survey_agent_message", "thanks_message",
    )


async def test_roundtrip_all_messages():
    store = InMemoryInboxTimingStore()
    payload = {k: f"msg-{k}" for k in MESSAGE_KEYS}
    payload["idle_warn_minutes"] = 3
    await store.set(7, payload)
    got = await store.get(7)
    for k in MESSAGE_KEYS:
        assert got[k] == f"msg-{k}"
    assert got["idle_warn_minutes"] == 3


async def test_message_wrong_type_dropped():
    store = InMemoryInboxTimingStore()
    await store.set(7, {"idle_close_message": 123, "thanks_message": "ok"})
    assert await store.get(7) == {"thanks_message": "ok"}
