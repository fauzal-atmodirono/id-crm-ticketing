"""Unit tests for taxonomy admin router (P10 Task 4)."""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from chatbot.features.authz.seed import PERMISSION_REGISTRY
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
def settings(monkeypatch: pytest.MonkeyPatch):
    from chatbot.platform.config import get_settings

    s = get_settings().model_copy(update={"taxonomy_admin_enabled": True, "proton_backend_key": "test_api_key"})
    return s


def test_the_permission_appears_in_the_permission_registry() -> None:
    assert "taxonomy.manage" in PERMISSION_REGISTRY


def test_the_flag_off_returns_404_so_the_page_does_not_render(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatbot.platform.config import get_settings

    off_settings = get_settings().model_copy(update={"taxonomy_admin_enabled": False})
    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(off_settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/tree")
    assert res.status_code == 404


def test_the_tree_endpoint_returns_the_nested_active_taxonomy(settings) -> None:
    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.get("/admin/taxonomy/tree")
    assert res.status_code == 200
    assert "tree" in res.json()


def test_creating_a_node_requires_taxonomy_manage(settings) -> None:
    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    # Without auth header -> 401
    res = client.post("/admin/taxonomy/node", json={"level": 1, "key": "type_a", "label": "A"})
    assert res.status_code in (401, 403)


def test_an_agent_role_cannot_edit_the_taxonomy(settings) -> None:
    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    # Invalid auth / non-admin header
    res = client.post(
        "/admin/taxonomy/node",
        headers={"x-api-key": "invalid_key"},
        json={"level": 1, "key": "type_a", "label": "A"},
    )
    assert res.status_code == 401


async def test_retiring_a_node_with_children_returns_a_confirmation_prompt_not_an_error(settings) -> None:
    store = TaxonomyStore(settings)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))
    await store.create_node(TaxonomyNode(level=2, key="div_sales", label="Sales", parent="type_inquiry"))

    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    client = TestClient(app)

    res = client.post(
        "/admin/taxonomy/node/type_inquiry/retire",
        headers={"x-api-key": settings.proton_backend_key},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "retired"
    assert len(body["active_children"]) == 1
    assert body["active_children"][0]["key"] == "div_sales"
