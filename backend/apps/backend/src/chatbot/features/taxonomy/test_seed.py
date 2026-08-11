"""Unit tests for non-destructive taxonomy seeding (P10 Task 2)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest

from chatbot.features.taxonomy.seed import seed_taxonomy_from_env
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
    monkeypatch.setattr("chatbot.features.taxonomy.store.firestore.Client", _FakeFirestore)


@pytest.fixture
def store() -> TaxonomyStore:
    from chatbot.platform.config import get_settings

    return TaxonomyStore(get_settings())


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings()


async def test_an_empty_store_is_seeded_with_the_full_appendix_a_taxonomy(
    store: TaxonomyStore, settings
) -> None:
    created = await seed_taxonomy_from_env(store, settings)
    assert created > 0

    all_nodes = await store.list_nodes(active_only=True)
    assert len(all_nodes) > 10


async def test_all_three_case_types_are_seeded(store: TaxonomyStore, settings) -> None:
    await seed_taxonomy_from_env(store, settings)

    nodes = await store.list_nodes(active_only=True)
    l1_nodes = [n for n in nodes if n.level == 1]
    l1_labels = {n.label for n in l1_nodes}
    assert l1_labels == {"Inquiry", "Complaint", "Compliment & Feedback"}


async def test_all_eight_divisions_are_seeded(store: TaxonomyStore, settings) -> None:
    await seed_taxonomy_from_env(store, settings)

    nodes = await store.list_nodes(active_only=True)
    l2_nodes = [n for n in nodes if n.level == 2]
    l2_labels = {n.label for n in l2_nodes}
    expected = {
        "Sales",
        "Product",
        "Network",
        "Charging",
        "Apps",
        "After Sales",
        "Others",
        "Marketing",
    }
    assert l2_labels == expected


async def test_the_seeded_tree_matches_what_the_env_json_produces_today(
    store: TaxonomyStore, settings
) -> None:
    await seed_taxonomy_from_env(store, settings)

    tree = await store.tree()
    assert len(tree) >= 1
    root = tree[0]
    assert root["label"] == "Inquiry"
    # Root should contain children divisions
    div_labels = {child["label"] for child in root["children"]}
    assert "Sales" in div_labels
    assert "After Sales" in div_labels


async def test_re_seeding_never_overwrites_an_operator_edited_label(
    store: TaxonomyStore, settings
) -> None:
    await seed_taxonomy_from_env(store, settings)

    node = await store.get_node("div_sales")
    assert node is not None
    node.label = "Sales & Retail"
    await store.create_node(node)

    # Re-seed
    newly_created = await seed_taxonomy_from_env(store, settings)
    assert newly_created == 0

    re_read = await store.get_node("div_sales")
    assert re_read is not None
    assert re_read.label == "Sales & Retail"


async def test_re_seeding_never_reactivates_a_retired_node(
    store: TaxonomyStore, settings
) -> None:
    await seed_taxonomy_from_env(store, settings)

    await store.retire_node("div_sales")
    retired = await store.get_node("div_sales")
    assert retired is not None
    assert retired.active is False

    # Re-seed
    await seed_taxonomy_from_env(store, settings)

    re_read = await store.get_node("div_sales")
    assert re_read is not None
    assert re_read.active is False


async def test_re_seeding_adds_a_node_that_appeared_in_the_env_json(
    store: TaxonomyStore, settings
) -> None:
    await seed_taxonomy_from_env(store, settings)

    # Modify settings to include a new division "Leasing"
    tax_dict = json.loads(settings.case_taxonomy_json)
    tax_dict["leasing"] = {"label": "Leasing", "subcategories": ["Long Term"]}
    updated_settings = settings.model_copy(
        update={"case_taxonomy_json": json.dumps(tax_dict)}
    )

    created = await seed_taxonomy_from_env(store, updated_settings)
    assert created >= 1

    new_node = await store.get_node("div_leasing")
    assert new_node is not None
    assert new_node.label == "Leasing"
    assert new_node.active is True
