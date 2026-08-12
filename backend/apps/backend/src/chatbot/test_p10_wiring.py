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


def test_startup_does_not_seed_when_the_admin_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, TAXONOMY_ADMIN_ENABLED="false")

    with TestClient(app):
        assert not hasattr(app.state, "taxonomy_seed_task")
    assert _FakeFirestore.documents == {}


def test_data_scoped_rbac_refuses_to_boot_because_nothing_enforces_it() -> None:
    """The flag restricts no data, so it must not be quietly settable.

    `features/authz/data_scope.py` is built and unwired: no caller for
    `apply_scope_to_filters`, an untouched query adapter, and `_ROLE_DATA_SCOPES`
    with no persistence or admin surface. A data-access flag that silently
    restricts nothing is the worst shape available -- an operator configures a
    dealer-scoped role and that user still reads every dealer's volumes.

    This test is the tripwire for the fix: when enforcement lands, the validator
    goes and this test fails, which is the point. Replace it then with one that
    proves scoping actually filters.
    """
    import pytest  # noqa: PLC0415

    from chatbot.platform.config import Settings  # noqa: PLC0415

    with pytest.raises(ValueError, match="DATA_SCOPED_RBAC_ENABLED is not implemented"):
        Settings(data_scoped_rbac_enabled=True)


def test_the_flag_is_absent_from_the_flags_on_gate() -> None:
    """Belt to the braces above: the gate must not try to enable it.

    If someone adds it back to FLAGS_ON, every bootstrap test in the flags-ON half
    fails to boot -- correct, but confusing. This says why in one place.
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
        "DATA_SCOPED_RBAC_ENABLED is in FLAGS_ON but refuses to boot; "
        f"remove it until enforcement is wired. Found: {enabled_lines}"
    )
