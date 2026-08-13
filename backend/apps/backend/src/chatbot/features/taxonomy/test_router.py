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


class _SyncSpy:
    """Stands in for `sync_taxonomy_to_chatwoot` so a test can see if it fired."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _store: Any, _settings: Any) -> bool:
        self.calls += 1
        return True


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _record(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._record(event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._record(event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._record(event, **kwargs)


def _client_for(settings) -> TestClient:
    app = FastAPI()
    app.include_router(build_taxonomy_admin_router(settings))
    return TestClient(app)


async def test_saving_a_node_does_not_push_to_chatwoot_while_the_sync_flag_is_off(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seeded store must not be allowed to overwrite the live pickers.

    `chatwoot_sync.py` derives case_category from level-1 nodes (case types +
    the neutral divisions root) and case_detail from bare level-4 labels, while
    Chatwoot holds the 8 division labels and full "Division: Subcategory:
    Detail" strings. Firing it on the first operator save would break fork
    patch 0050's cascade for every agent.
    """
    spy = _SyncSpy()
    recorder = _LogRecorder()
    monkeypatch.setattr("chatbot.features.taxonomy.router.sync_taxonomy_to_chatwoot", spy)
    monkeypatch.setattr("chatbot.features.taxonomy.router._log", recorder)

    off = settings.model_copy(update={"taxonomy_chatwoot_sync_enabled": False})
    res = _client_for(off).post(
        "/admin/taxonomy/node",
        headers={"x-api-key": off.proton_backend_key},
        json={"level": 1, "key": "type_inquiry", "label": "Inquiry"},
    )

    assert res.status_code == 200
    assert spy.calls == 0
    # The skip must be observable: an operator whose picker did not change
    # needs the reason in the log, not a guess about a broken sync.
    skipped = [e for e in recorder.events if e[0] == "taxonomy_chatwoot_sync_skipped"]
    assert len(skipped) == 1
    assert skipped[0][1]["setting"] == "TAXONOMY_CHATWOOT_SYNC_ENABLED"


async def test_retiring_a_node_does_not_push_to_chatwoot_while_the_sync_flag_is_off(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SyncSpy()
    monkeypatch.setattr("chatbot.features.taxonomy.router.sync_taxonomy_to_chatwoot", spy)

    off = settings.model_copy(update={"taxonomy_chatwoot_sync_enabled": False})
    store = TaxonomyStore(off)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    res = _client_for(off).post(
        "/admin/taxonomy/node/type_inquiry/retire",
        headers={"x-api-key": off.proton_backend_key},
    )

    assert res.status_code == 200
    assert spy.calls == 0


async def test_saving_a_node_pushes_to_chatwoot_when_the_sync_flag_is_on(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SyncSpy()
    monkeypatch.setattr("chatbot.features.taxonomy.router.sync_taxonomy_to_chatwoot", spy)

    on = settings.model_copy(update={"taxonomy_chatwoot_sync_enabled": True})
    res = _client_for(on).post(
        "/admin/taxonomy/node",
        headers={"x-api-key": on.proton_backend_key},
        json={"level": 1, "key": "type_inquiry", "label": "Inquiry"},
    )

    assert res.status_code == 200
    assert spy.calls == 1


async def test_retiring_a_node_pushes_to_chatwoot_when_the_sync_flag_is_on(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _SyncSpy()
    monkeypatch.setattr("chatbot.features.taxonomy.router.sync_taxonomy_to_chatwoot", spy)

    on = settings.model_copy(update={"taxonomy_chatwoot_sync_enabled": True})
    store = TaxonomyStore(on)
    await store.create_node(TaxonomyNode(level=1, key="type_inquiry", label="Inquiry"))

    res = _client_for(on).post(
        "/admin/taxonomy/node/type_inquiry/retire",
        headers={"x-api-key": on.proton_backend_key},
    )

    assert res.status_code == 200
    assert spy.calls == 1


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
