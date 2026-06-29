from types import SimpleNamespace
from typing import Any, cast

from chatbot.features.metrics.scheduler import run_sync_job, start_metrics_scheduler
from chatbot.platform.config import Settings


def _settings() -> Settings:
    return cast(Settings, SimpleNamespace(metrics_sync_enabled=True, metrics_sync_interval_hours=6))


def test_run_sync_job_runs_sync_then_ensure() -> None:
    calls: list[str] = []

    def sync_mock(_s: Settings) -> dict[str, int]:
        calls.append("sync")
        return {"tickets": 2, "rows": 1}

    def ensure_mock(_s: Settings) -> None:
        calls.append("ensure")

    result = run_sync_job(_settings(), sync=sync_mock, ensure=ensure_mock)
    assert result == {"tickets": 2, "rows": 1}
    assert calls == ["sync", "ensure"]  # sync before ensure


def test_run_sync_job_swallows_errors() -> None:
    def boom(_s: Any) -> dict[str, int]:
        raise RuntimeError("zendesk down")

    assert run_sync_job(_settings(), sync=boom, ensure=lambda _s: None) == {}


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})

    def start(self) -> None:
        self.started = True


def test_scheduler_disabled_returns_none() -> None:
    s = cast(Settings, SimpleNamespace(metrics_sync_enabled=False, metrics_sync_interval_hours=6))
    assert start_metrics_scheduler(s) is None


def test_scheduler_enabled_adds_interval_job_and_starts() -> None:
    s = cast(Settings, SimpleNamespace(metrics_sync_enabled=True, metrics_sync_interval_hours=3))
    fake = _FakeScheduler()
    result = start_metrics_scheduler(s, scheduler=fake)
    assert result is fake
    assert fake.started is True
    assert len(fake.jobs) == 1
    job = fake.jobs[0]
    assert job["trigger"] == "interval"
    assert job["hours"] == 3
