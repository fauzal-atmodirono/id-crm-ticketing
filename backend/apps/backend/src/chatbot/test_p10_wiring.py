"""P10 Taxonomy Store & Admin Router integration tests through main.py."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient


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
    # Keyed per-collection: `TaxonomyStore` writes to "taxonomy_nodes" and
    # `CustomStatusStore` (a wholly separate feature, also wired through this
    # same `firestore.Client` patch point) writes to "custom_statuses". A
    # single shared dict here made the taxonomy-store assertions see the other
    # feature's documents whenever both startup hooks ran in the same test --
    # see the flags-ON CI gate failure this was built to fix.
    collections: ClassVar[dict[str, dict[str, dict[str, Any]]]] = {}

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(_FakeFirestore.collections.setdefault(name, {}))

    @staticmethod
    def reset() -> None:
        _FakeFirestore.collections = {}


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


def test_taxonomy_router_is_mounted_and_reachable_through_real_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_startup_seeds_the_taxonomy_store_when_the_admin_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seeder existed and had no caller but its own tests.

    The admin page rendered "No active taxonomy nodes yet" on a tenant with the
    flag on, the router mounted and the Appendix A data sitting in config --
    because nothing ever wrote it to Firestore.
    """
    import time

    app = _boot(monkeypatch, TAXONOMY_ADMIN_ENABLED="true")

    with TestClient(app) as client:
        # The seed runs on the TestClient's own event loop, in its own thread.
        # Poll rather than await: this test function is sync and cannot drive
        # that loop. Against `_FakeFirestore` the seed is in-memory and finishes
        # almost immediately.
        for _ in range(200):
            if app.state.taxonomy_seed_task.done():
                break
            time.sleep(0.01)
        assert app.state.taxonomy_seed_task.done(), "seed task did not finish"

        res = client.get("/admin/taxonomy/tree")
        assert res.status_code == 200
        roots = res.json()["tree"]
        assert {root["label"] for root in roots} == {
            "Inquiry",
            "Complaint",
            "Compliment & Feedback",
            "Case divisions",
        }

        # Root labels alone pass even if the seed dies partway through writing
        # levels 2-4 (`_run` swallows the exception) -- the exact bug class
        # Task 1 fixed, where 100 of 246 detail nodes vanished silently. Count
        # every node in the tree and require the deepest level actually landed.
        total_nodes = 0
        has_level_4 = False

        def _walk(nodes: list[dict[str, Any]]) -> None:
            nonlocal total_nodes, has_level_4
            for node in nodes:
                total_nodes += 1
                if node["level"] == 4:
                    has_level_4 = True
                _walk(node["children"])

        _walk(roots)
        assert total_nodes == 346
        assert has_level_4


def test_startup_does_not_seed_when_the_admin_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, TAXONOMY_ADMIN_ENABLED="false")

    with TestClient(app):
        assert not hasattr(app.state, "taxonomy_seed_task")
    assert _FakeFirestore.collections.get("taxonomy_nodes", {}) == {}


def test_data_scoped_rbac_boots_with_rbac_and_refuses_without_it() -> None:
    """Wave 2 Task 28 replaced the "not implemented" tripwire: enforcement is
    wired (features/authz/test_scope_enforcement.py proves each surface
    filters), so the flag now boots -- but only alongside RBAC, because a
    scope is resolved from the caller's RBAC roles and without RBAC it would
    restrict nothing while appearing to.
    """
    import pytest  # noqa: PLC0415

    from chatbot.platform.config import Settings  # noqa: PLC0415

    assert Settings(data_scoped_rbac_enabled=True, rbac_enabled=True).data_scoped_rbac_enabled
    with pytest.raises(ValueError, match="DATA_SCOPED_RBAC_ENABLED requires RBAC_ENABLED"):
        Settings(data_scoped_rbac_enabled=True, rbac_enabled=False)


def test_the_flag_is_absent_from_the_flags_on_gate() -> None:
    """The gate must not enable it on its own: FLAGS_ON does not set
    RBAC_ENABLED, so the flags-ON half would refuse to boot -- correct, but
    confusing. This says why in one place.
    """
    from pathlib import Path  # noqa: PLC0415

    gate = (
        Path(__file__).resolve().parents[5]
        / "deploy"
        / "scripts"
        / "check-suites-both-flag-states.sh"
    ).read_text(encoding="utf-8")

    enabled_lines = [
        ln.strip() for ln in gate.splitlines() if ln.strip().startswith("DATA_SCOPED_RBAC_ENABLED=")
    ]
    assert not enabled_lines, (
        "DATA_SCOPED_RBAC_ENABLED is in FLAGS_ON without RBAC_ENABLED and refuses "
        f"to boot; add RBAC_ENABLED=true alongside it. Found: {enabled_lines}"
    )
