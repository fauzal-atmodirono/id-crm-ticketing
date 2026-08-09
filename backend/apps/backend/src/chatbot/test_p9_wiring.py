"""P9 task 7 -- what `main.py` actually wires, driven through the real app.

Two gaps, both left open by tasks that did not own `main.py`, both closed here
and both proved the only way a mount can honestly be proved: by booting
`bootstrap_application()` and making a request.

1. **`features/alerts/rules_router.py` was mounted nowhere.** Thirteen green
   tests in `features/alerts/`, five endpoints, and every one of them a 404
   against a live backend -- so every agent silently got `BUILT_IN_DEFAULTS`
   and the per-agent override layer was unreachable by the people it exists
   for. Task 1 correctly declined to write this test itself: with nothing
   mounting the router it would have failed for a reason that task could not
   fix, and a known-red test in a shared suite is worse than a named gap.
2. **`/metrics/dashboard` carried no freshness stamp.** Task 5 stamped twelve
   metrics responses and could not stamp the §2.2.3 executive dashboard,
   because `build_metrics_query_router` took no `Settings`. It left a test
   asserting that gap which failed the moment the signature changed -- so the
   signature changed here, and the assertion flipped in
   `features/metrics/test_freshness_contract.py`.

`openapi()` path assertions appear only to say WHICH path should exist; they are
never the proof, because a path can be present while its dependency wiring is
wrong. **401-rather-than-404 is the proof**: 404 was the bug, and 401 shows both
that the route is mounted and that its permission dependency actually ran.

The Firestore edge is the only thing faked, at `firestore.Client` -- so the real
`AlertRuleStore`, the real `asyncio.to_thread` hop, the real serialisation, the
real `resolve()` precedence and the real `require_permission` dependency are all
in the path. Nothing here touches a network, and nothing here implies the fork's
preferences page has been seen in a browser (it has not -- see the blocked-work
register, section 3h).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

_API_KEY = "p9-wiring-key"
_WEBHOOK_SECRET = "p9-wiring-secret"

# Every P9 flag, so a test clears the lot before setting the ones it means. The
# both-flag-states gate runs this suite a second time with all six exported, and
# without this loop a "flag off" test there would assert the ambient environment
# rather than its own name -- the `Settings(_env_file=None)` trap, which does not
# stop pydantic-settings reading `os.environ`.
_P9_FLAGS = (
    "INBOUND_ALERTS_ENABLED",
    "ALERT_RULES_ENABLED",
    "ANOMALY_HOURLY_ENABLED",
    "ANOMALY_HOURLY_ZSCORE_K",
    "ANOMALY_HOURLY_MIN_BASELINE",
    "DASHBOARD_FRESHNESS_ENABLED",
)

_RULES_PATHS = (
    "/alerts/rules/defaults",
    "/alerts/rules/defaults/{event}",
    "/alerts/rules/mine",
    "/alerts/rules/mine/{event}",
)


# --- the Firestore edge ----------------------------------------------------


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


class _FakeFirestore:
    """One in-memory document store, shared by every client this app builds.

    Class-level, because `AlertRuleStore._client()` constructs a fresh client on
    every single call -- a per-instance store would lose the write between a PUT
    and the GET that has to read it back.
    """

    documents: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def collection(self, _name: str) -> _FakeCollection:
        return _FakeCollection(_FakeFirestore.documents)

    @staticmethod
    def reset() -> None:
        _FakeFirestore.documents = {}


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    """No test may inherit either piece of process-local state: the fake
    Firestore's documents, or the freshness sync clock."""
    from chatbot.features.metrics.freshness import reset_sync_clock  # noqa: PLC0415

    _FakeFirestore.reset()
    reset_sync_clock()
    yield
    _FakeFirestore.reset()
    reset_sync_clock()


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    for flag in _P9_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("PROTON_BACKEND_KEY", _API_KEY)
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    # RBAC off, so `require_permission` falls back to the shared-secret check
    # and the router's own non-RBAC path (which demands an explicit `agent_id`,
    # because there is no verifiable caller identity to infer one from) is the
    # one under test. The RBAC-on path is covered by
    # `features/alerts/test_rules_router.py`.
    monkeypatch.delenv("RBAC_ENABLED", raising=False)
    monkeypatch.setattr(
        "chatbot.features.alerts.rules_store.firestore.Client",
        _FakeFirestore,
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from chatbot.main import bootstrap_application  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    return bootstrap_application()


def _clear_settings_cache() -> None:
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()


def _auth() -> dict[str, str]:
    return {"x-api-key": _API_KEY}


# --- Job 1a: the alert-preferences router ----------------------------------


def test_the_alert_preferences_endpoints_refuse_rather_than_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one assertion that proves the mount.

    404 was the bug for every one of these five endpoints. A 401 proves two
    things at once: FastAPI resolved the route (so `main.py` included the
    router), and `require_permission` ran (so it was handed a real `settings`
    and is refusing, not absent).
    """
    try:
        app = _boot(monkeypatch, ALERT_RULES_ENABLED="true")
        paths = app.openapi()["paths"]
        for path in _RULES_PATHS:
            assert path in paths, f"{path} is not mounted"

        client = TestClient(app)
        # No x-api-key at all: RBAC is off, so this is the shared-secret path.
        assert client.get("/alerts/rules/mine").status_code == 401
        assert client.get("/alerts/rules/defaults").status_code == 401
        assert (
            client.put(
                "/alerts/rules/mine/new_inbound",
                json={"scope": "my_inbox", "modalities": ["toast"]},
            ).status_code
            == 401
        )
        assert client.delete("/alerts/rules/mine/new_inbound").status_code == 401
        assert (
            client.put(
                "/alerts/rules/defaults/new_inbound",
                json={"scope": "my_inbox", "modalities": ["toast"]},
            ).status_code
            == 401
        )
    finally:
        _clear_settings_cache()


def test_an_agent_turns_their_own_new_inbound_sound_on_through_the_real_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round trip the whole feature is for, end to end through `main.py`.

    `new_inbound` is deliberately the event used here: it ships toast-only, and
    the requirement is met by the sound being *available and configurable*, not
    by the default being loud. This test is what proves it is genuinely reachable
    -- an agent who wants the beep can now get it, which was not true while the
    router was unmounted.
    """
    try:
        app = _boot(monkeypatch, ALERT_RULES_ENABLED="true")
        client = TestClient(app)

        before = client.get("/alerts/rules/mine", params={"agent_id": 7}, headers=_auth())
        assert before.status_code == 200, before.text
        assert before.json()["rules"]["new_inbound"]["modalities"] == ["toast"]

        put = client.put(
            "/alerts/rules/mine/new_inbound",
            params={"agent_id": 7},
            headers=_auth(),
            json={"scope": "my_inbox", "modalities": ["toast", "sound"], "enabled": True},
        )
        assert put.status_code == 200, put.text

        after = client.get("/alerts/rules/mine", params={"agent_id": 7}, headers=_auth())
        assert after.json()["rules"]["new_inbound"]["modalities"] == ["toast", "sound"]

        # A different agent is untouched -- the override layer is per agent, and
        # a mount that shared one document between agents would pass every test
        # above.
        other = client.get("/alerts/rules/mine", params={"agent_id": 8}, headers=_auth())
        assert other.json()["rules"]["new_inbound"]["modalities"] == ["toast"]

        reset = client.delete(
            "/alerts/rules/mine/new_inbound", params={"agent_id": 7}, headers=_auth()
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["rule"]["modalities"] == ["toast"]
    finally:
        _clear_settings_cache()


def test_the_preferences_endpoints_answer_disabled_rather_than_404_with_the_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why this router is mounted UNCONDITIONALLY, unlike the custom-status one.

    The fork's preferences page renders `{"disabled": true, "reason": ...}`
    verbatim. A 404 with the flag off would make "this tenant has not enabled
    alert rules" indistinguishable from "this backend is the wrong version", and
    the page would have to guess -- which is the entire reason that body exists.
    """
    try:
        app = _boot(monkeypatch)
        client = TestClient(app)

        mine = client.get("/alerts/rules/mine", params={"agent_id": 7}, headers=_auth())
        assert mine.status_code == 200, mine.text
        body = mine.json()
        assert body["disabled"] is True
        assert "ALERT_RULES_ENABLED" in body["reason"]
        # An empty rule set, not a fabricated one: the page fills its six rows
        # from its own DEFAULT_ALERT_RULES, so a backend that invented rules here
        # would be a second source of truth for the same table.
        assert body["rules"] == {}

        defaults = client.get("/alerts/rules/defaults", headers=_auth())
        assert defaults.status_code == 200
        assert defaults.json()["disabled"] is True
        # And nothing was written: the flag is checked before the store is
        # touched, so an unenabled tenant gets no Firestore traffic at all.
        assert _FakeFirestore.documents == {}
    finally:
        _clear_settings_cache()


# --- Job 1b: the executive dashboard's freshness stamp ---------------------


def test_the_executive_dashboard_is_stamped_through_the_real_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/metrics/dashboard` through `bootstrap_application()`, not through a
    hand-built `FastAPI()`. The gap task 5 named was a *wiring* gap -- the
    factory could not be given `Settings` from `main.py` -- so a unit test that
    constructs the router itself cannot close it."""
    from chatbot.features.metrics.freshness import record_sync_completed  # noqa: PLC0415

    try:
        app = _boot(monkeypatch, DASHBOARD_FRESHNESS_ENABLED="true")
        record_sync_completed(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))
        body = TestClient(app).get("/metrics/dashboard").json()
        assert body["as_of"] == "2026-08-09T12:00:00+00:00"
        assert body["source"] == "batch_6h"
        assert body["freshness"]["as_of_status"] == "measured"
        # The basis sentence is the point, not the timestamp: it is what makes a
        # dashboard/CRM discrepancy an expected difference rather than a bug.
        assert "expected" in body["freshness"]["basis"]
    finally:
        _clear_settings_cache()


def test_the_executive_dashboard_is_unchanged_with_the_freshness_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ship-dark half, through the real app. The deployed SPA's overview
    panel parses this payload; off must add no key at all."""
    try:
        app = _boot(monkeypatch)
        body = TestClient(app).get("/metrics/dashboard").json()
        assert "as_of" not in body
        assert "source" not in body
        assert "freshness" not in body
    finally:
        _clear_settings_cache()
