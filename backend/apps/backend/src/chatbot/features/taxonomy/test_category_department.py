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


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeFirestore.reset()
    monkeypatch.setattr("chatbot.features.taxonomy.store.firestore.Client", _FakeFirestore)


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


async def test_the_coverage_report_lists_departments_no_category_maps_to(settings) -> None:
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.create_node(TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry", department="dept_sales"))

    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    unreferenced = res.json()["unreferenced_departments"]
    # The mapped department must be absent and the unmapped ones present -- key
    # presence alone (what this asserted before) passes on an empty list, which
    # is the answer the report gives when it is broken.
    assert "dept_sales" not in unreferenced
    assert "dept_aftersales" in unreferenced


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
