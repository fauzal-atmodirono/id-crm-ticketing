"""P6 task 11 -- what `main.py` actually wires, in both flag states.

Ten P6 components landed with zero `main.py` wiring on purpose, so that the
parallel implementers never contended on that one file. This suite is the
check that they are now reachable, and that turning the flags off makes them
unreachable rather than merely quiet.

Both directions matter and neither substitutes for the other:

- **Flags off** must register no scheduler job at all. A poller that ticks and
  finds nothing to do is not the same thing as a poller that does not exist:
  the first one still calls Chatwoot every minute on every tenant that has
  never asked for presence tracking.
- **Flags on** is the path nobody exercises until a tenant opts in, which is
  exactly why it is worth a test. This does not attempt a live run -- with the
  flags on, the real schedulers would open Firestore and Chatwoot connections
  from a background thread. `BackgroundScheduler` is replaced in all four
  scheduler modules with a recording fake, so the assertions are about the
  wiring (which jobs are registered, and that each one's shutdown hook reaches
  its own scheduler), not about a real thread doing real I/O.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

_P6_JOB_IDS = {
    "agent_presence_poll",
    "presence_threshold_sweeper",
    "acw_timeout_sweep",
    "routing_assignment_sweep",
}

# Every module whose `start_*` helper constructs its own BackgroundScheduler.
_SCHEDULER_MODULES = (
    "chatbot.features.routing.presence_poller",
    "chatbot.features.routing.presence_thresholds",
    "chatbot.features.routing.acw",
    "chatbot.features.routing.sweeper",
)


class _FakeScheduler:
    """Records what was scheduled instead of starting a thread."""

    instances: ClassVar[list[_FakeScheduler]] = []

    def __init__(self) -> None:
        self.job_ids: list[str] = []
        self.started = False
        self.shutdown_calls = 0
        _FakeScheduler.instances.append(self)

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.job_ids.append(str(kwargs.get("id")))

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls += 1


class _AbsentDoc:
    """A document read that SUCCEEDED and found nothing."""

    exists = False

    def to_dict(self) -> dict[str, Any] | None:
        return None


class _FakeDocRef:
    def get(self) -> _AbsentDoc:
        return _AbsentDoc()

    def set(self, data: dict[str, Any]) -> None:
        _EmptyFirestore.written.append(data)


class _FakeCollection:
    """Query chain wide enough for both P6 stores: `document().get()`,
    `where().order_by().limit().stream()` and `add()`."""

    def __init__(self, name: str) -> None:
        self.name = name

    def document(self, key: str) -> _FakeDocRef:
        return _FakeDocRef()

    def where(self, *a: Any, **k: Any) -> _FakeCollection:
        return self

    def order_by(self, *a: Any, **k: Any) -> _FakeCollection:
        return self

    def limit(self, *a: Any, **k: Any) -> _FakeCollection:
        return self

    def stream(self) -> Iterator[Any]:
        return iter(())

    def add(self, data: dict[str, Any]) -> None:
        _EmptyFirestore.appended.append(data)


class _EmptyFirestore:
    """Stands in for `firestore.Client` so no test here touches the network.

    Every document reads as absent on a *successful* read -- the state of a
    tenant whose seed has never run -- because that is the branch the
    catalogue's shipped-defaults fallback exists for, and therefore the branch
    a first status selection actually takes. Writes are captured, not stored.
    """

    appended: ClassVar[list[dict[str, Any]]] = []
    written: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(name)


class _RecordingAvailabilityWriter:
    """The Chatwoot `PATCH /agents/{id}` leg of `set_status`, captured at the
    edge so the rest of the chain is the real, wired one."""

    calls: ClassVar[list[tuple[int, str]]] = []

    def __init__(self, settings: Any) -> None:
        pass

    async def set_availability(self, agent_id: int, native: str) -> bool:
        _RecordingAvailabilityWriter.calls.append((agent_id, native))
        return True


def _patch_firestore_and_chatwoot(monkeypatch: pytest.MonkeyPatch) -> None:
    _EmptyFirestore.appended = []
    _EmptyFirestore.written = []
    _RecordingAvailabilityWriter.calls = []
    # `custom_status` and `presence_store` import the same `firestore` module
    # object, so this reaches both stores.
    monkeypatch.setattr("chatbot.features.routing.custom_status.firestore.Client", _EmptyFirestore)
    monkeypatch.setattr(
        "chatbot.features.routing.custom_status.ChatwootAvailabilityWriter",
        _RecordingAvailabilityWriter,
    )


def _patch_schedulers(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeScheduler.instances = []
    for module in _SCHEDULER_MODULES:
        monkeypatch.setattr(f"{module}.BackgroundScheduler", _FakeScheduler)


def _boot(monkeypatch: pytest.MonkeyPatch) -> Any:
    # Same minimal genai environment test_routing_mount.py uses, so
    # OrchestratorService construction doesn't demand live credentials.
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    from chatbot.main import bootstrap_application  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    return bootstrap_application()


def _clear_settings_cache() -> None:
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()


def _startup_hook_names(app: Any) -> list[str]:
    return [getattr(hook, "__name__", "") for hook in app.router.on_startup]


def _run_shutdown_hooks(app: Any) -> None:
    for hook in app.router.on_shutdown:
        if inspect.iscoroutinefunction(hook):  # pragma: no cover - none today
            continue
        hook()


def test_no_p6_scheduler_starts_with_every_flag_off(monkeypatch):
    _patch_schedulers(monkeypatch)
    for var in (
        "PRESENCE_TRACKING_ENABLED",
        "PRESENCE_CUSTOM_STATUSES_ENABLED",
        "PRESENCE_THRESHOLD_ALERTS_ENABLED",
        "ACW_ENABLED",
        "ROUTING_FAIR_SHARE_ENABLED",
        "ROUTING_SWEEP_ENABLED",
        "FOLLOW_UP_DATE_ENABLED",
        "ROUTING_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)

    try:
        app = _boot(monkeypatch)
        scheduled = {job for s in _FakeScheduler.instances for job in s.job_ids}
        assert not (scheduled & _P6_JOB_IDS), f"P6 jobs registered with flags off: {scheduled}"
        paths = app.openapi()["paths"]
        assert "/admin/workforce" not in paths
        # The status-selection router is gated the same way, so a tenant that
        # never enabled custom statuses gets FastAPI's own 404 with no handler
        # code reachable -- not a live endpoint answering `{"disabled": true}`,
        # which a UI could read as a status change that worked.
        assert not [p for p in paths if p.startswith("/routing/presence")]
    finally:
        _clear_settings_cache()


def test_every_p6_component_is_reachable_with_the_flags_on(monkeypatch):
    _patch_schedulers(monkeypatch)
    for var in (
        "PRESENCE_TRACKING_ENABLED",
        "PRESENCE_CUSTOM_STATUSES_ENABLED",
        "PRESENCE_THRESHOLD_ALERTS_ENABLED",
        "ACW_ENABLED",
        "ROUTING_FAIR_SHARE_ENABLED",
        "ROUTING_SWEEP_ENABLED",
        "FOLLOW_UP_DATE_ENABLED",
        # The routing sweeper is gated on the Phase-5 engine as well as its own
        # flag -- sweeping for unassigned work is pointless with selection off.
        "ROUTING_ENABLED",
    ):
        monkeypatch.setenv(var, "true")

    try:
        app = _boot(monkeypatch)

        scheduled = {job for s in _FakeScheduler.instances for job in s.job_ids}
        assert scheduled >= _P6_JOB_IDS, f"missing P6 jobs: {_P6_JOB_IDS - scheduled}"
        assert all(s.started for s in _FakeScheduler.instances)

        paths = app.openapi()["paths"]
        assert "/admin/workforce" in paths
        assert "/routing/assign" in paths
        # The status-selection endpoints, without which no named status can
        # ever enter the presence log (review-final C1). Reachability, not just
        # registration, is asserted in its own test below.
        assert "/routing/presence/status" in paths

        # Every scheduler this boot created must be reachable by a shutdown
        # hook. A scheduler started without one leaves an APScheduler thread
        # alive across a reload, which looks like a leak, not a missing feature.
        _run_shutdown_hooks(app)
        never_stopped = [s.job_ids for s in _FakeScheduler.instances if s.shutdown_calls == 0]
        assert not never_stopped, f"schedulers started with no shutdown hook: {never_stopped}"
    finally:
        _clear_settings_cache()


def test_the_status_router_is_reachable_through_the_real_app(monkeypatch):
    """review-final C1's other half: mounted, not merely written.

    `status_router.py` shipped complete and 404ed on every endpoint, because
    `main.py` was outside the scope of the fix that wrote it. A green unit suite
    for a router nothing mounts is precisely the shape of failure C1 was, so
    this drives the endpoints through `bootstrap_application()` itself.

    Everything faked here is faked at the edge -- Firestore and the Chatwoot
    availability PATCH -- so the wiring under test (which stores `main.py`
    hands the router, and whether a request reaches them at all) is real.
    """
    _patch_schedulers(monkeypatch)
    _patch_firestore_and_chatwoot(monkeypatch)
    monkeypatch.setenv("PRESENCE_CUSTOM_STATUSES_ENABLED", "true")
    monkeypatch.setenv("PROTON_BACKEND_KEY", "mount-test-key")
    monkeypatch.delenv("RBAC_ENABLED", raising=False)

    try:
        app = _boot(monkeypatch)
        paths = app.openapi()["paths"]
        assert {
            "/routing/presence/statuses",
            "/routing/presence/status",
            "/routing/presence/statuses/{key}",
        } <= set(paths)

        # No context manager on purpose: entering it would run the app's
        # startup hooks, including the catalogue seed, which is not what this
        # test is about.
        client = TestClient(app)

        # A mounted route that refuses is the distinction that matters. 404
        # was the bug; 401 proves the route exists and its permission gate ran.
        assert client.get("/routing/presence/statuses").status_code == 401

        authed = {"x-api-key": "mount-test-key"}
        listed = client.get("/routing/presence/statuses", headers=authed)
        assert listed.status_code == 200, listed.text
        keys = [row["key"] for row in listed.json()["statuses"]]
        assert {"available", "lunch", "acw", "offline"} <= set(keys)

        # The write path is the whole of C1: `set_status` had no HTTP caller,
        # so no named status could ever enter the presence log. Assert the
        # native mirror AND the appended event, because the log is what the
        # threshold sweeper, the dashboard and the `routable` filter read --
        # i.e. that main.py handed this router the same presence store they use.
        posted = client.post(
            "/routing/presence/status", json={"key": "lunch", "agent_id": 7}, headers=authed
        )
        assert posted.status_code == 200, posted.text
        assert posted.json()["key"] == "lunch"
        assert _RecordingAvailabilityWriter.calls == [(7, "busy")]
        assert [(e["agent_id"], e["status"]) for e in _EmptyFirestore.appended] == [(7, "lunch")]

        # And the read the picker needs: no history for agent 8 must answer
        # `null`, never a fabricated "available".
        current = client.get("/routing/presence/status?agent_id=8", headers=authed)
        assert current.status_code == 200, current.text
        assert current.json()["key"] is None
    finally:
        _clear_settings_cache()


def test_the_custom_status_seed_runs_at_startup_only_with_its_own_flag(monkeypatch):
    """`seed()` is create-only, but it is still a Firestore write. With the
    custom-status flag off nothing reads the catalogue, so seeding anyway would
    put nine documents into every tenant that never asked for the feature.
    """
    _patch_schedulers(monkeypatch)

    monkeypatch.delenv("PRESENCE_CUSTOM_STATUSES_ENABLED", raising=False)
    try:
        app = _boot(monkeypatch)
        assert "_seed_custom_statuses" not in _startup_hook_names(app)
    finally:
        _clear_settings_cache()

    monkeypatch.setenv("PRESENCE_CUSTOM_STATUSES_ENABLED", "true")
    try:
        app = _boot(monkeypatch)
        assert "_seed_custom_statuses" in _startup_hook_names(app)
    finally:
        _clear_settings_cache()
