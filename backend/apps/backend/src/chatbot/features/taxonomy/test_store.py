"""Unit tests for TaxonomyStore (P10 Task 1)."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

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
def store(monkeypatch: pytest.MonkeyPatch) -> TaxonomyStore:
    from chatbot.platform.config import get_settings

    settings = get_settings()
    return TaxonomyStore(settings)


async def test_a_node_can_be_created_at_each_of_the_four_levels(store: TaxonomyStore) -> None:
    n1 = TaxonomyNode(level=1, key="type_inquiry", label="Inquiry")
    assert await store.create_node(n1) is True

    n2 = TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry")
    assert await store.create_node(n2) is True

    n3 = TaxonomyNode(level=3, key="cat_delivery", label="Delivery", parent="div_sales")
    assert await store.create_node(n3) is True

    n4 = TaxonomyNode(level=4, key="det_refund_status", label="Status", parent="cat_delivery")
    assert await store.create_node(n4) is True

    nodes = await store.list_nodes(active_only=True)
    assert len(nodes) == 4


async def test_a_level_2_node_cannot_be_created_under_a_missing_parent(store: TaxonomyStore) -> None:
    n2 = TaxonomyNode(level=2, key="div_sales", label="Sales", parent="missing_parent")
    with pytest.raises(ValueError, match="parent node 'missing_parent' does not exist"):
        await store.create_node(n2)


async def test_a_level_2_node_cannot_be_created_under_a_retired_parent(store: TaxonomyStore) -> None:
    n1 = TaxonomyNode(level=1, key="type_inquiry", label="Inquiry")
    await store.create_node(n1)
    await store.retire_node("type_inquiry")

    n2 = TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry")
    with pytest.raises(ValueError, match="parent node 'type_inquiry' is retired"):
        await store.create_node(n2)


async def test_retiring_a_node_hides_it_from_the_active_tree(store: TaxonomyStore) -> None:
    n1 = TaxonomyNode(level=1, key="type_inquiry", label="Inquiry")
    await store.create_node(n1)

    tree_before = await store.tree()
    assert len(tree_before) == 1
    assert tree_before[0]["key"] == "type_inquiry"

    await store.retire_node("type_inquiry")

    tree_after = await store.tree()
    assert len(tree_after) == 0


async def test_a_retired_node_is_still_resolvable_by_key(store: TaxonomyStore) -> None:
    n1 = TaxonomyNode(level=1, key="type_inquiry", label="Inquiry")
    await store.create_node(n1)
    await store.retire_node("type_inquiry")

    resolved = await store.get_node("type_inquiry")
    assert resolved is not None
    assert resolved.key == "type_inquiry"
    assert resolved.active is False


def test_there_is_no_delete_method_on_the_store(store: TaxonomyStore) -> None:
    assert not hasattr(store, "delete_node")
    assert not hasattr(store, "delete")


async def test_retiring_a_parent_reports_its_active_children(store: TaxonomyStore) -> None:
    n1 = TaxonomyNode(level=1, key="type_inquiry", label="Inquiry")
    await store.create_node(n1)

    n2 = TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry")
    await store.create_node(n2)

    active_children = await store.retire_node("type_inquiry")
    assert len(active_children) == 1
    assert active_children[0].key == "div_sales"


async def test_sort_order_is_respected_in_the_tree(store: TaxonomyStore) -> None:
    n1_a = TaxonomyNode(level=1, key="type_z", label="Z", sort_order=10)
    n1_b = TaxonomyNode(level=1, key="type_a", label="A", sort_order=2)
    n1_c = TaxonomyNode(level=1, key="type_m", label="M", sort_order=5)

    await store.create_node(n1_a)
    await store.create_node(n1_b)
    await store.create_node(n1_c)

    tree = await store.tree()
    assert [node["key"] for node in tree] == ["type_a", "type_m", "type_z"]


async def test_the_tree_shape_matches_what_the_cascading_picker_expects(store: TaxonomyStore) -> None:
    n1 = TaxonomyNode(level=1, key="type_inquiry", label="Inquiry", department="dept_inquiry")
    await store.create_node(n1)

    n2 = TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry")
    await store.create_node(n2)

    tree = await store.tree()
    assert len(tree) == 1
    root = tree[0]
    assert root["key"] == "type_inquiry"
    assert root["label"] == "Inquiry"
    assert root["level"] == 1
    assert root["active"] is True
    assert root["department"] == "dept_inquiry"
    assert len(root["children"]) == 1
    child = root["children"][0]
    assert child["key"] == "div_sales"
    assert child["level"] == 2
    assert child["children"] == []
