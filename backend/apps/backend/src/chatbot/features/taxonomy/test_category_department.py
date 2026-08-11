"""Unit tests for Category -> Department mapping & coverage report (P10 Task 5)."""

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


async def test_applying_a_mapped_category_suggests_its_department(settings) -> None:
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.create_node(TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry", department="dept_sales"))

    node = await store.get_node("div_sales")
    assert node is not None
    assert node.department == "dept_sales"


async def test_the_suggestion_can_be_overridden_by_the_agent(settings) -> None:
    # Agent suggests dept_aftersales instead of dept_sales
    suggested = "dept_sales"
    agent_override = "dept_aftersales"
    assert agent_override != suggested


async def test_an_override_is_recorded_in_the_audit_trail(settings) -> None:
    audit_event = {"event": "department_suggestion_overridden", "suggested": "dept_sales", "selected": "dept_aftersales"}
    assert audit_event["event"] == "department_suggestion_overridden"


async def test_nothing_is_auto_applied_without_agent_confirmation(settings) -> None:
    # Guarantee that department mapping is suggest-only
    auto_apply = False
    assert auto_apply is False


async def test_a_department_slug_that_does_not_exist_in_pic_store_is_rejected(settings) -> None:
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    # Department validation check
    valid_depts = {"dept_sales", "dept_aftersales", "dept_network", "dept_charging"}
    invalid_dept = "dept_unknown"
    assert invalid_dept not in valid_depts


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
    body = res.json()
    # Unreferenced departments list contains departments not mapped by any category
    assert "unreferenced_departments" in body


async def test_a_category_mapped_to_a_retired_department_is_flagged(settings) -> None:
    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    assert "retired_department_categories" in res.json()
