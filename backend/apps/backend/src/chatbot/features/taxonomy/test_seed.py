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

    # Positive control: proves the monkeypatch is actually wired to the
    # logger the seeder calls. Without this, a future `seed.py` change to
    # `structlog.get_logger(__name__)` *inside* the function -- a common
    # structlog idiom -- would make the patch inert, and "no unresolved
    # events" below would pass vacuously rather than testing anything.
    assert any(event == "taxonomy_seeded" for _, event, _ in recorder.events)

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
    store: TaxonomyStore, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _LogRecorder()
    monkeypatch.setattr("chatbot.features.taxonomy.seed._log", recorder)

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

    # The positive side of the assertion above: a dropped detail must be
    # observable, not merely absent. This is what would have caught the
    # original bug -- 100 of 246 details vanished with no log line at all.
    unresolved = [e for e in recorder.events if e[1] == "taxonomy_seed_details_unresolved"]
    assert len(unresolved) == 1
    _, _, kwargs = unresolved[0]
    assert kwargs["dropped"] == 1
    assert kwargs["parents"] == ["cat_nonexistent_division_nonexistent_category"]


async def test_a_failed_pre_read_does_not_overwrite_the_seeded_store(
    store: TaxonomyStore, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient collection read failure must not re-seed over operator edits.

    `TaxonomyStore.list_nodes` catches every exception and returns `[]`, so the
    seeder's one up-front read cannot tell an empty store from a failed one.
    With `create_node` writing via an unconditional `.set()`, trusting that
    empty result would re-seed all 346 nodes back to `department=None,
    active=True` and the env label -- erasing every department mapping,
    resurrecting every retired category into the agent picker, and reverting
    every edited label, while logging `taxonomy_seeded newly_created=346` as
    though it were a first boot.
    """
    await seed_taxonomy_from_env(store, settings)

    edited = await store.get_node("div_sales")
    assert edited is not None
    edited.label = "Sales & Retail"
    edited.department = "dept_sales"
    await store.create_node(edited)
    await store.retire_node("div_product")

    recorder = _LogRecorder()
    monkeypatch.setattr("chatbot.features.taxonomy.seed._log", recorder)

    # Only the collection-wide read fails; per-document reads still resolve --
    # exactly the DeadlineExceeded / malformed-document shape being guarded.
    def _boom(_self: Any) -> None:
        raise RuntimeError("503 Deadline Exceeded")

    original_get = _FakeCollection.get
    monkeypatch.setattr(_FakeCollection, "get", _boom)

    created = await seed_taxonomy_from_env(store, settings)
    assert created == 0

    # Restore only this patch -- `monkeypatch.undo()` would also revert the
    # autouse fixture's `firestore.Client` stub and point the assertions below
    # at the real Firestore.
    monkeypatch.setattr(_FakeCollection, "get", original_get)

    assert ("info", "taxonomy_seeded", {"newly_created": 0}) in recorder.events

    survived = await store.get_node("div_sales")
    assert survived is not None
    assert survived.label == "Sales & Retail"
    assert survived.department == "dept_sales"

    retired = await store.get_node("div_product")
    assert retired is not None
    assert retired.active is False


async def test_a_genuinely_empty_store_still_seeds_when_probing_per_node(
    store: TaxonomyStore, settings
) -> None:
    """The per-node fallback must not turn a real first boot into a no-op."""
    created = await seed_taxonomy_from_env(store, settings)

    assert created == 346
    nodes = await store.list_nodes(active_only=False)
    assert len(nodes) == 346


async def test_a_hyphenated_division_key_still_resolves_its_details(
    store: TaxonomyStore, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards `label_to_div_slug` storing an unslugified division key.

    An operator could enter "after-sales" as the JSON key for a division
    labelled "After Sales". The level-3 subcategory key is always built by
    slugifying that key (`cat_after_sales_...`, hyphen becomes underscore).
    If the label -> key map ever stored the raw JSON key instead of its
    slug, the detail-resolution path would compute `cat_after-sales_...`
    instead -- a key the level-3 node never has -- and every detail under
    that division would silently orphan again, reintroducing this task's
    original bug class through a different division key.
    """
    recorder = _LogRecorder()
    monkeypatch.setattr("chatbot.features.taxonomy.seed._log", recorder)

    updated = settings.model_copy(
        update={
            "case_taxonomy_json": json.dumps(
                {"after-sales": {"label": "After Sales", "subcategories": ["Warranty"]}}
            ),
            "case_detail_options_json": json.dumps(
                {"options": ["After Sales: Warranty: Rejected"]}
            ),
        }
    )

    await seed_taxonomy_from_env(store, updated)

    unresolved = [e for e in recorder.events if e[1] == "taxonomy_seed_details_unresolved"]
    assert unresolved == []

    category = await store.get_node("cat_after_sales_warranty")
    assert category is not None

    nodes = await store.list_nodes(active_only=True)
    details = [n for n in nodes if n.level == 4]
    assert len(details) == 1
    assert details[0].parent == category.key
