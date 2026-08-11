"""P10 Taxonomy Store & Admin Router integration tests through main.py."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest


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
def _clean_state() -> None:
    _FakeFirestore.reset()


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("PROTON_BACKEND_KEY", "test_key")
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "test_secret")
    monkeypatch.setattr(
        "chatbot.features.taxonomy.store.firestore.Client",
        _FakeFirestore,
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from chatbot.main import bootstrap_application
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    return bootstrap_application()


def test_taxonomy_router_is_mounted_and_reachable_through_real_app(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(monkeypatch, TAXONOMY_ADMIN_ENABLED="true")
    client = TestClient(app)

    res = client.get("/admin/taxonomy/tree")
    assert res.status_code == 200
    assert "tree" in res.json()


def test_taxonomy_router_returns_404_when_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(monkeypatch, TAXONOMY_ADMIN_ENABLED="false")
    client = TestClient(app)

    res = client.get("/admin/taxonomy/tree")
    assert res.status_code == 404


def test_the_coverage_report_answers_when_the_category_department_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CATEGORY_DEPARTMENT_MAPPING_ENABLED` had no consumer anywhere.

    `example.env` documents it as the switch that "mounts GET
    /admin/taxonomy/coverage", but nothing read it -- the endpoint answered on
    `TAXONOMY_ADMIN_ENABLED` alone, so an operator who turned the documented flag
    on saw no change and an operator who left it off still got the report. This
    pair drives the real app so a future refactor cannot quietly disconnect the
    flag again.
    """
    app = _boot(
        monkeypatch,
        TAXONOMY_ADMIN_ENABLED="true",
        CATEGORY_DEPARTMENT_MAPPING_ENABLED="true",
    )
    client = TestClient(app)

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 200
    assert "unmapped_categories" in res.json()


def test_the_coverage_report_404s_when_only_the_taxonomy_admin_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(
        monkeypatch,
        TAXONOMY_ADMIN_ENABLED="true",
        CATEGORY_DEPARTMENT_MAPPING_ENABLED="false",
    )
    client = TestClient(app)

    # The taxonomy admin itself is on, so this is not the admin gate answering.
    assert client.get("/admin/taxonomy/tree").status_code == 200

    res = client.get("/admin/taxonomy/coverage")
    assert res.status_code == 404
    assert "CATEGORY_DEPARTMENT_MAPPING_ENABLED" in res.json()["detail"]
