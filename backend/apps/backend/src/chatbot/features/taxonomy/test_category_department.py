"""P10 Task 5 -- the coverage report, and the department slug on a node.

**The department *suggestion* path does not exist.** Nothing outside the coverage
report reads `TaxonomyNode.department`: no suggestion is ever made to an agent, no
override is ever recorded, and `retired_department_categories` is hardcoded `[]`.

Four tests that claimed to cover it were deleted rather than kept, because each
consisted of a Python literal asserted against itself and none imported anything
from the feature (review finding C-3):

- `test_the_suggestion_can_be_overridden_by_the_agent` -- `assert "dept_aftersales"
  != "dept_sales"`
- `test_an_override_is_recorded_in_the_audit_trail` -- built an `audit_event` dict
  in the test and asserted its own `event` key
- `test_nothing_is_auto_applied_without_agent_confirmation` -- `auto_apply = False;
  assert auto_apply is False`, standing in for the plan's suggest-only constraint
- `test_a_department_slug_that_does_not_exist_in_pic_store_is_rejected` -- created
  a node, then asserted `"dept_unknown" not in {...}` against a set written in the
  test; no rejection happens anywhere

They made "category->department is suggest-only, and overrides are audited" read
as verified by test. At acceptance there is no override audit trail to show and no
code to point at. A smaller honest suite is worth more than a larger false one, so
the count drops by four here deliberately. What remains tests the store field and
the report, which are real.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from chatbot.features.chat.pic_store import PicRecord
from chatbot.features.taxonomy.router import build_taxonomy_admin_router
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


class _FakePicStore:
    """Stand-in for `PicStore` that never touches Firestore.

    Holds the `PicRecord`s a test wants `list_all()` to return; pass `error=`
    instead to make it behave like a store whose read failed.
    """

    def __init__(
        self, records: list[PicRecord] | None = None, *, error: Exception | None = None
    ) -> None:
        self._records = records or []
        self._error = error

    async def list_all(self) -> list[PicRecord]:
        if self._error is not None:
            raise self._error
        return list(self._records)


def _pic_record(department: str) -> PicRecord:
    return PicRecord(department=department, pic_name="Test PIC", pic_email="pic@test", pic_whatsapp="")


def _patch_pic_store(monkeypatch: pytest.MonkeyPatch, fake: _FakePicStore) -> None:
    """`router.py` builds `PicStore(settings)` once per router; patching the
    name it imported hands back `fake` regardless of the settings argument --
    the same technique `_clean_state` already uses for `firestore.Client`.
    """
    monkeypatch.setattr("chatbot.features.taxonomy.router.PicStore", lambda _settings: fake)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeFirestore.reset()
    monkeypatch.setattr("chatbot.features.taxonomy.store.firestore.Client", _FakeFirestore)
    # Default to an empty, never-raising fake so tests that don't care about
    # departments never construct a real `PicStore` against real Firestore.
    _patch_pic_store(monkeypatch, _FakePicStore())


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(update={"taxonomy_admin_enabled": True, "category_department_mapping_enabled": True})


async def test_a_category_node_round_trips_its_mapped_department_slug(settings) -> None:
    """Renamed from `test_applying_a_mapped_category_suggests_its_department`:
    nothing applies and nothing suggests. This is a store round-trip.
    """
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.create_node(TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry", department="dept_sales"))

    node = await store.get_node("div_sales")
    assert node is not None
    assert node.department == "dept_sales"


async def test_the_coverage_report_lists_active_categories_with_no_department(settings) -> None:
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.create_node(TaxonomyNode(level=2, key="div_unmapped", label="Unmapped", parent="type_inquiry"))

    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    body = res.json()
    assert len(body["unmapped_categories"]) >= 1
    keys = [c["key"] for c in body["unmapped_categories"]]
    assert "div_unmapped" in keys


async def test_the_coverage_report_lists_departments_no_category_maps_to(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pic_store(
        monkeypatch, _FakePicStore([_pic_record("sales"), _pic_record("aftersales")])
    )
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.create_node(TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry", department="dept_sales"))

    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    body = res.json()
    # The mapped department must be absent and the unmapped one present -- key
    # presence alone (what this asserted before) passes on an empty list, which
    # is the answer the report gives when it is broken.
    assert "dept_sales" not in body["unreferenced_departments"]
    assert "dept_aftersales" in body["unreferenced_departments"]
    assert body["departments_source"] == "pic_store"


async def test_unreferenced_departments_come_from_the_pic_store_not_a_hardcoded_list(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the defect itself: the old code fell back to a
    hardcoded `{dept_sales, dept_aftersales, dept_network, dept_charging}`
    whenever the (broken) PicStore import raised. `dept_network` and
    `dept_charging` have no PIC on the live tenant and must never appear;
    a working `PicStore` read is the only thing that can put a department in
    this list.
    """
    _patch_pic_store(
        monkeypatch, _FakePicStore([_pic_record("sales"), _pic_record("pre_sales")])
    )
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    body = res.json()
    assert body["unreferenced_departments"] == ["dept_pre_sales", "dept_sales"]
    assert "dept_network" not in body["unreferenced_departments"]
    assert "dept_charging" not in body["unreferenced_departments"]
    assert body["departments_source"] == "pic_store"


async def test_unreferenced_departments_dedupe_case_insensitively(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`PicStore._doc_ref` lowercases document ids, so `"Sales"` and `"sales"`
    are the same document -- the report must collapse them the same way.
    """
    _patch_pic_store(monkeypatch, _FakePicStore([_pic_record("Sales"), _pic_record("sales")]))
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    assert res.json()["unreferenced_departments"] == ["dept_sales"]


async def test_pic_store_failure_yields_empty_unreferenced_departments_not_500(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pic_store(monkeypatch, _FakePicStore(error=RuntimeError("firestore unreachable")))
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.create_node(TaxonomyNode(level=2, key="div_unmapped", label="Unmapped", parent="type_inquiry"))

    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    body = res.json()
    assert body["unreferenced_departments"] == []
    assert body["departments_source"] == "unavailable"
    # The left-hand panel is a wholly separate read (TaxonomyStore, not
    # PicStore) and must not be affected by the PIC store failing.
    keys = [c["key"] for c in body["unmapped_categories"]]
    assert "div_unmapped" in keys


async def test_the_retired_department_category_list_is_present_but_never_populated(
    settings,
) -> None:
    """Renamed from `test_a_category_mapped_to_a_retired_department_is_flagged`.

    Nothing is flagged. `router.py` hardcodes `retired_dept_categories = []`
    because flagging needs a retired/active distinction `PicStore` does not
    expose, and the old name reported that unbuilt check as verified while
    asserting only that the key exists. Asserting the empty list instead means
    this test fails -- and has to be renamed -- on the day it is implemented.
    """
    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    assert res.json()["retired_department_categories"] == []
