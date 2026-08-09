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
from typing import Any, ClassVar

import pytest

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
        assert "/admin/workforce" not in app.openapi()["paths"]
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

        # Every scheduler this boot created must be reachable by a shutdown
        # hook. A scheduler started without one leaves an APScheduler thread
        # alive across a reload, which looks like a leak, not a missing feature.
        _run_shutdown_hooks(app)
        never_stopped = [s.job_ids for s in _FakeScheduler.instances if s.shutdown_calls == 0]
        assert not never_stopped, f"schedulers started with no shutdown hook: {never_stopped}"
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
