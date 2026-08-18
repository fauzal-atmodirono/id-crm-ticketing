"""The registry is an OPTIMISATION, never a gate. Every test here is really
asking the same question: can a bad answer from this store strand a live
caller? It must not -- the worst it may cost is one wasted ring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry


class FakeCollection:
    """Stands in for the Firestore collection: {doc_id: {"agent_id", "at"}}."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.fail = False

    def set(self, doc_id: str, data: dict) -> None:
        if self.fail:
            raise RuntimeError("firestore unavailable")
        self.docs[doc_id] = data

    def delete(self, doc_id: str) -> None:
        if self.fail:
            raise RuntimeError("firestore unavailable")
        self.docs.pop(doc_id, None)

    def all(self) -> list[dict]:
        if self.fail:
            raise RuntimeError("firestore unavailable")
        return list(self.docs.values())


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(update={"phone_softphone_registration_ttl_seconds": 90})


@pytest.fixture
def registry(settings):
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    reg = SoftphoneRegistry(settings, clock=lambda: now)
    reg._collection = FakeCollection()  # type: ignore[assignment]
    return reg


async def test_heartbeat_then_registered(registry):
    await registry.heartbeat(17)
    assert await registry.registered_ids() == {17}


async def test_unregister_removes(registry):
    await registry.heartbeat(17)
    await registry.unregister(17)
    assert await registry.registered_ids() == set()


async def test_entry_older_than_ttl_is_ignored(registry, settings):
    """A tab that closed without unregistering must age out, or we would ring
    a dead identity and burn a stage."""
    stale = registry._now() - timedelta(
        seconds=settings.phone_softphone_registration_ttl_seconds + 1
    )
    registry._collection.docs["agent-17"] = {"agent_id": 17, "at": stale}
    assert await registry.registered_ids() == set()


async def test_store_failure_returns_empty_and_does_not_raise(registry):
    """This is read from _attempt_transfer, which runs INLINE in the audio
    pump. An exception here would be dead air on a live call."""
    registry._collection.fail = True
    assert await registry.registered_ids() == set()


async def test_heartbeat_failure_does_not_raise(registry):
    registry._collection.fail = True
    await registry.heartbeat(17)  # must not raise
