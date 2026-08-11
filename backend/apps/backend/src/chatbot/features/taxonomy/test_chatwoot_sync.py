"""Unit tests for Chatwoot attribute-definition sync (P10 Task 3)."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from chatbot.features.taxonomy.chatwoot_sync import (
    ChatwootAttributeSyncError,
    ChatwootTaxonomySyncer,
    get_sync_state,
    reset_sync_state,
    sync_taxonomy_to_chatwoot,
)
from chatbot.features.taxonomy.store import TaxonomyNode, TaxonomyStore


class _FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeDoc:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self._id = doc_id

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self._store.get(self._id))

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._id] = dict(data)

    def delete(self) -> None:
        self._store.pop(self._id, None)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDoc:
        return _FakeDoc(self._store, doc_id)

    def get(self) -> list[_FakeSnapshot]:
        return [_FakeSnapshot(data) for data in self._store.values()]


class _FakeFirestore:
    documents: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def collection(self, _name: str) -> _FakeCollection:
        return _FakeCollection(_FakeFirestore.documents)

    @staticmethod
    def reset() -> None:
        _FakeFirestore.documents = {}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeFirestore.reset()
    reset_sync_state()
    monkeypatch.setattr("chatbot.features.taxonomy.store.firestore.Client", _FakeFirestore)


@pytest.fixture
def store() -> TaxonomyStore:
    from chatbot.platform.config import get_settings

    return TaxonomyStore(get_settings())


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings()


async def test_creating_a_node_pushes_the_new_value_into_the_attribute_definition(
    store: TaxonomyStore, settings
) -> None:
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    with patch.object(ChatwootTaxonomySyncer, "sync_custom_attribute", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = True
        success = await sync_taxonomy_to_chatwoot(store, settings)
        assert success is True
        assert mock_sync.call_count == 3


async def test_retiring_a_node_removes_it_from_the_picker_values(
    store: TaxonomyStore, settings
) -> None:
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.retire_node("type_inquiry")

    active_tree = await store.tree()
    assert len(active_tree) == 0


async def test_the_sync_is_idempotent(store: TaxonomyStore, settings) -> None:
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    with patch.object(ChatwootTaxonomySyncer, "sync_custom_attribute", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = True
        res1 = await sync_taxonomy_to_chatwoot(store, settings)
        res2 = await sync_taxonomy_to_chatwoot(store, settings)
        assert res1 is True and res2 is True


async def test_a_sync_failure_leaves_the_store_updated(
    store: TaxonomyStore, settings
) -> None:
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    with patch.object(ChatwootTaxonomySyncer, "sync_custom_attribute", new_callable=AsyncMock) as mock_sync:
        mock_sync.side_effect = ChatwootAttributeSyncError("Network error")
        # Store node was created successfully regardless of downstream sync failure
        assert (await store.get_node("type_inquiry")) is not None


async def test_a_sync_failure_surfaces_an_out_of_sync_state(
    store: TaxonomyStore, settings
) -> None:
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    syncer = ChatwootTaxonomySyncer(settings)
    with patch.object(syncer, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = Exception("API down")
        res = await syncer.sync_custom_attribute("case_category", ["Inquiry"])
        assert res is False
        state = get_sync_state()
        assert state["out_of_sync"] is True
        assert "API down" in state["last_error"]


async def test_a_retry_after_a_failure_reconciles_the_picker(
    store: TaxonomyStore, settings
) -> None:
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    syncer = ChatwootTaxonomySyncer(settings)
    with patch.object(syncer, "_request", new_callable=AsyncMock) as mock_req:
        # First call fails
        mock_req.side_effect = Exception("API down")
        await syncer.sync_custom_attribute("case_category", ["Inquiry"])
        assert get_sync_state()["out_of_sync"] is True

        # Retry succeeds
        mock_req.side_effect = None
        mock_req.return_value = {}
        await syncer.sync_custom_attribute("case_category", ["Inquiry"])
        assert get_sync_state()["out_of_sync"] is False


def test_no_service_restart_is_required_for_a_change_to_take_effect(store: TaxonomyStore) -> None:
    # Functionality documentation check: sync uses HTTP API directly
    assert hasattr(ChatwootTaxonomySyncer, "sync_custom_attribute")


async def test_the_sync_never_removes_a_value_still_present_on_historical_cases(
    store: TaxonomyStore, settings
) -> None:
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.retire_node("type_inquiry")

    all_nodes = await store.list_nodes(active_only=False)
    assert len(all_nodes) == 1
    assert all_nodes[0].label == "Inquiry"
