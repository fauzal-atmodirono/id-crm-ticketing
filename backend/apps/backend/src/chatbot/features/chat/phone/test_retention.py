"""Tests for call-recording retention (P11 task 8) and its schedule.

Two changes worth knowing about if you are diffing against the previous version:

1. `Settings` comes from real environment variables rather than
   `model_copy(update=...)`, so these tests exercise the flag an operator
   actually sets.
2. Calling the job with **no deleter no longer marks recordings deleted.** It is
   a dry run. Marking a recording `is_deleted` while the audio still exists at
   the provider would make the retrieval endpoint tell an agent "deleted under
   the retention policy" about audio that is still there -- the policy looking
   enforced exactly where it is not. The two tests that relied on that behaviour
   now pass a deleter, which is the only path that can truthfully clear the
   stored attributes.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.phone.retention import (
    RETENTION_INTERVAL_HOURS,
    run_retention_pass,
    run_retention_purge_job,
    run_retention_tick,
    start_recording_retention_job,
)
from chatbot.platform.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    return _settings(monkeypatch, PHONE_RETENTION_JOB_ENABLED="true")


def _old(days: int = 95) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


class _FakeScheduler:
    """Records what was scheduled without starting a thread."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})

    def start(self) -> None:
        self.started = True


async def test_a_recording_older_than_the_window_is_deleted_at_the_provider(
    settings: Settings,
) -> None:
    records = [{"sid": "RE1", "created_at": _old(), "is_deleted": False}]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["status"] == "completed"
    assert res["purged_count"] == 1
    mock_del.assert_called_once_with("RE1")


async def test_the_stored_attributes_are_cleared_only_after_a_successful_delete(
    settings: Settings,
) -> None:
    records = [
        {"sid": "RE1", "recording_url": "https://...", "created_at": _old(), "is_deleted": False}
    ]

    await run_retention_purge_job(settings, records, delete_func=AsyncMock())

    assert records[0]["is_deleted"] is True
    assert records[0]["recording_url"] is None


async def test_with_no_deleter_configured_nothing_is_touched_and_nothing_is_claimed(
    settings: Settings,
) -> None:
    """The dry-run path, and the reason the retention policy is not yet in force.

    This is the state every tenant is in today: flag on, no deleter wired. The
    report must say so, count what it would delete, and leave the recording
    exactly as it found it.
    """
    records = [
        {"sid": "RE1", "recording_url": "https://...", "created_at": _old(), "is_deleted": False}
    ]

    res = await run_retention_purge_job(settings, records)

    assert res["status"] == "dry_run"
    assert res["reason"] == "no_recording_deleter_configured"
    assert res["eligible_count"] == 1
    assert res["purged_count"] == 0
    # Untouched: the audio still exists at the provider, so the stored
    # attributes must keep saying so.
    assert records[0]["is_deleted"] is False
    assert records[0]["recording_url"] == "https://..."


async def test_a_recording_inside_the_window_is_untouched(settings: Settings) -> None:
    records = [
        {"sid": "RE2", "recording_url": "https://...", "created_at": _old(10), "is_deleted": False}
    ]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["purged_count"] == 0
    assert records[0]["is_deleted"] is False
    mock_del.assert_not_called()


async def test_a_recording_of_unknown_age_is_never_deleted(settings: Settings) -> None:
    """ "Unknown age" must not resolve to "delete it" -- the deletion is
    irreversible and the timestamp is the only evidence of eligibility."""
    records = [
        {"sid": "RE3", "created_at": None, "is_deleted": False},
        {"sid": "RE4", "created_at": "not-a-timestamp", "is_deleted": False},
    ]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["purged_count"] == 0
    mock_del.assert_not_called()


async def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing_the_pass(
    settings: Settings,
) -> None:
    """A naive timestamp used to raise TypeError against the aware cutoff, which
    would abandon the pass part-way -- possibly after some deletions."""
    naive = (datetime.now(UTC) - timedelta(days=95)).replace(tzinfo=None).isoformat()
    records = [
        {"sid": "RE1", "created_at": naive, "is_deleted": False},
        {"sid": "RE2", "created_at": _old(), "is_deleted": False},
    ]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["purged_count"] == 2


async def test_a_provider_delete_failure_leaves_the_recording_marked_as_present(
    settings: Settings,
) -> None:
    records = [{"sid": "RE1", "created_at": _old(), "is_deleted": False}]
    mock_del = AsyncMock(side_effect=Exception("Twilio API timeout"))

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["errors"] == 1
    assert res["purged_count"] == 0
    assert records[0]["is_deleted"] is False


async def test_the_job_is_idempotent(settings: Settings) -> None:
    records = [{"sid": "RE1", "created_at": _old(), "is_deleted": False}]
    mock_del = AsyncMock()

    res1 = await run_retention_purge_job(settings, records, delete_func=mock_del)
    res2 = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res1["purged_count"] == 1
    assert res2["purged_count"] == 0
    assert mock_del.call_count == 1


async def test_the_flag_off_runs_no_deletions(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _settings(monkeypatch, PHONE_RETENTION_JOB_ENABLED="false")
    records = [{"sid": "RE1", "created_at": _old(), "is_deleted": False}]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(off, records, delete_func=mock_del)

    assert res["status"] == "skipped"
    assert records[0]["is_deleted"] is False
    mock_del.assert_not_called()


async def test_the_window_is_configurable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 30-day-old recording is inside the 90-day default and outside a 7-day
    window, so this fails if PHONE_RECORDING_RETENTION_DAYS stops reaching the
    cutoff."""
    settings = _settings(
        monkeypatch,
        PHONE_RETENTION_JOB_ENABLED="true",
        PHONE_RECORDING_RETENTION_DAYS="7",
    )
    records = [{"sid": "RE1", "created_at": _old(30), "is_deleted": False}]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["purged_count"] == 1


def test_a_window_of_zero_is_refused_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """0 days means "delete every recording"; it is also what someone types when
    they mean "off"."""
    monkeypatch.setenv("PHONE_RECORDING_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="PHONE_RECORDING_RETENTION_DAYS"):
        Settings()


def test_the_defaults_are_job_off_and_a_ninety_day_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deletes the env vars first and asserts the delete worked: `_env_file=None`
    does not stop pydantic-settings reading `os.environ`, and the all-flags-on
    gate sets PHONE_RETENTION_JOB_ENABLED."""
    monkeypatch.delenv("PHONE_RETENTION_JOB_ENABLED", raising=False)
    monkeypatch.delenv("PHONE_RECORDING_RETENTION_DAYS", raising=False)
    assert "PHONE_RETENTION_JOB_ENABLED" not in os.environ
    assert "PHONE_RECORDING_RETENTION_DAYS" not in os.environ

    settings = Settings(_env_file=None)

    assert settings.phone_retention_job_enabled is False
    assert settings.phone_recording_retention_days == 90


# --- the schedule -----------------------------------------------------------


def test_the_flag_off_schedules_nothing_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _settings(monkeypatch, PHONE_RETENTION_JOB_ENABLED="false")
    sched = _FakeScheduler()

    assert start_recording_retention_job(off, scheduler=sched) is None
    assert sched.jobs == []
    assert sched.started is False


def test_the_flag_on_schedules_one_daily_job_that_does_not_run_at_boot(
    settings: Settings,
) -> None:
    """No `next_run_time=now`, unlike the metrics scheduler: a crash-looping
    container must not be able to drive a deletion pass per restart."""
    sched = _FakeScheduler()

    assert start_recording_retention_job(settings, scheduler=sched) is sched
    assert sched.started is True
    assert len(sched.jobs) == 1
    job = sched.jobs[0]
    assert job["id"] == "phone_recording_retention"
    assert job["trigger"] == "interval"
    assert job["hours"] == RETENTION_INTERVAL_HOURS == 24
    assert "next_run_time" not in job


async def test_a_pass_with_no_candidate_source_reports_not_executable(
    settings: Settings,
) -> None:
    """Today's real state. The counts are None rather than 0: a 0 would read as
    "no recordings are past retention", which this pass cannot know."""
    res = await run_retention_pass(settings)

    assert res["status"] == "not_executable"
    assert res["reason"] == "no_recording_source_configured"
    assert res["eligible_count"] is None
    assert res["purged_count"] is None


async def test_a_pass_with_a_source_and_a_deleter_deletes_what_the_source_yields(
    settings: Settings,
) -> None:
    """Proves the injected pair is all that stands between the schedule and a
    real purge -- i.e. that wiring the two owed pieces needs no rewrite here."""
    seen: list[datetime] = []

    async def _source(cutoff: datetime) -> list[dict[str, Any]]:
        seen.append(cutoff)
        return [{"sid": "RE1", "created_at": _old(), "is_deleted": False}]

    mock_del = AsyncMock()
    res = await run_retention_pass(settings, source=_source, delete_func=mock_del)

    assert res["status"] == "completed"
    assert res["purged_count"] == 1
    mock_del.assert_called_once_with("RE1")
    # The cutoff handed to the source is derived from the configured window.
    assert seen and seen[0] < datetime.now(UTC) - timedelta(days=89)


def test_the_tick_never_raises_when_the_source_explodes(settings: Settings) -> None:
    """A scheduled run that raises would only produce an unretrieved-exception
    log; APScheduler would keep the job, but the failure would be invisible."""

    async def _source(_cutoff: datetime) -> list[dict[str, Any]]:
        raise RuntimeError("recording store unreachable")

    res = run_retention_tick(settings, source=_source)

    assert res["status"] == "failed"
    assert res["purged_count"] is None
    assert "recording store unreachable" in res["error"]
