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


class _LogRecorder:
    """Stands in for `seed.py`'s structlog logger so tests can assert on events.

    `seed_taxonomy_from_env` never raises for a dropped detail -- it logs
    `taxonomy_seed_details_unresolved` instead -- so that log call is the only
    observable a test has for "the seeder dropped something."
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **kwargs: Any) -> None:
        self.events.append((level, event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._record("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._record("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._record("error", event, **kwargs)


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
    l1_labels = {n.label for n in nodes if n.level == 1}
    assert {"Inquiry", "Complaint", "Compliment & Feedback"} <= l1_labels


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


async def test_every_detail_option_finds_its_parent_including_after_sales(
    store: TaxonomyStore, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The division key is `aftersales`; the detail prefix is `After Sales`.

    Re-slugifying the prefix gives `after_sales`, which matches no division key,
    so 100 of 246 details used to be dropped with no log line. Resolution goes
    through a label -> key map now, and the seeder's own
    `taxonomy_seed_details_unresolved` warning -- its only observable for "a
    detail's parent could not be found" -- must never fire against the real
    config: that's the property this test actually needs, not a raw node
    count, which conflates missing-parent drops with same-key collisions
    (see below).
    """
    recorder = _LogRecorder()
    monkeypatch.setattr("chatbot.features.taxonomy.seed._log", recorder)

    await seed_taxonomy_from_env(store, settings)

    unresolved = [e for e in recorder.events if e[1] == "taxonomy_seed_details_unresolved"]
    assert unresolved == [], f"seeder dropped details for want of a parent: {unresolved}"

    nodes = await store.list_nodes(active_only=True)
    seeded_details = [n for n in nodes if n.level == 4]
    # 245, one fewer than the config's 246 option strings: "Charging: Public
    # Charging: others" and "...: Others" differ only by case and slugify to
    # the same det_ key, so the second collapses into the first. That is
    # correct dedup, not a dropped parent -- the assertion above is what
    # guards the actual defect this task fixes.
    assert len(seeded_details) == 245

    after_sales_details = [
        n for n in seeded_details if n.parent is not None and n.parent.startswith("cat_aftersales_")
    ]
    assert after_sales_details, "no After Sales details were seeded"


async def test_divisions_hang_off_the_neutral_root_not_a_case_type(
    store: TaxonomyStore, settings
) -> None:
    """Appendix A's Case Category is orthogonal to Division.

    Parenting divisions to whichever case type sorts first made the page assert
    that every division belongs to Inquiry. The neutral root makes no such claim.
    """
    await seed_taxonomy_from_env(store, settings)

    nodes = await store.list_nodes(active_only=True)
    divisions = [n for n in nodes if n.level == 2]
    assert divisions
    assert {n.parent for n in divisions} == {"type_case_divisions"}

    root = await store.get_node("type_case_divisions")
    assert root is not None
    assert root.level == 1
    assert root.label == "Case divisions"
    assert root.parent is None


async def test_the_three_case_types_are_seeded_as_childless_roots(
    store: TaxonomyStore, settings
) -> None:
    await seed_taxonomy_from_env(store, settings)

    tree = await store.tree()
    by_label = {root["label"]: root for root in tree}
    assert set(by_label) == {
        "Inquiry",
        "Complaint",
        "Compliment & Feedback",
        "Case divisions",
    }
    for label in ("Inquiry", "Complaint", "Compliment & Feedback"):
        assert by_label[label]["children"] == []

    div_labels = {child["label"] for child in by_label["Case divisions"]["children"]}
    assert div_labels == {
        "Sales",
        "Product",
        "Network",
        "Charging",
        "Apps",
        "After Sales",
        "Others",
        "Marketing",
    }


async def test_a_detail_whose_parent_does_not_exist_is_skipped_not_raised(
    store: TaxonomyStore, settings
) -> None:
    updated = settings.model_copy(
        update={
            "case_detail_options_json": json.dumps(
                {"options": ["Nonexistent Division: Nonexistent Category: Some Detail"]}
            )
        }
    )

    created = await seed_taxonomy_from_env(store, updated)

    assert created > 0  # types, root and divisions still seeded
    nodes = await store.list_nodes(active_only=True)
    assert [n for n in nodes if n.level == 4] == []
