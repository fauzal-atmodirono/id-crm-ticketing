"""Tests for the audit-log retention purge (P13).

The previous version of this file asserted the disable path with
``settings.model_copy(update={"audit_purge_job_enabled": False})`` -- an
attribute pydantic had never declared, because neither `audit_purge_job_enabled`
nor `audit_log_retention_days` was a `Settings` field. `model_copy` accepts
unknown keys, so the assertion passed while the flag it named did not exist as
configuration. Every test here now goes through a `Settings` built from the
environment, so a removed or renamed field fails these tests rather than falling
back to a default nobody chose.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chatbot.features.authz.audit_purge import (
    PURGE_INTERVAL_HOURS,
    build_audit_row_source,
    run_audit_log_purge_job,
    run_audit_purge_pass,
    run_audit_purge_tick,
    start_audit_purge_job,
)
from chatbot.platform.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """A real `Settings` from real environment variables -- never `model_copy`."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


@pytest.fixture
def enabled_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    return _settings(
        monkeypatch,
        AUDIT_PURGE_JOB_ENABLED="true",
        AUDIT_LOG_RETENTION_DAYS="365",
    )


async def test_audit_records_older_than_retention_days_are_purged(
    enabled_settings: Settings,
) -> None:
    old_time = datetime.now(UTC) - timedelta(days=400)
    entries = [{"id": "audit_1", "timestamp": old_time.isoformat()}]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(enabled_settings, entries, delete_func=mock_del)

    assert res["status"] == "completed"
    assert res["purged_count"] == 1
    mock_del.assert_called_once_with("audit_1")


async def test_recent_audit_records_are_retained(enabled_settings: Settings) -> None:
    recent_time = datetime.now(UTC) - timedelta(days=30)
    entries = [{"id": "audit_2", "timestamp": recent_time.isoformat()}]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(enabled_settings, entries, delete_func=mock_del)

    assert res["purged_count"] == 0
    mock_del.assert_not_called()


async def test_audit_purge_can_be_disabled_via_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replacement for the `model_copy` test: AUDIT_PURGE_JOB_ENABLED=false
    reaches the job through pydantic-settings, which is the only route an
    operator has."""
    settings = _settings(monkeypatch, AUDIT_PURGE_JOB_ENABLED="false")
    old_time = datetime.now(UTC) - timedelta(days=4000)
    entries = [{"id": "audit_1", "timestamp": old_time.isoformat()}]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(settings, entries, delete_func=mock_del)

    assert res["status"] == "skipped"
    assert res["reason"] == "audit_purge_disabled"
    mock_del.assert_not_called()


async def test_the_retention_window_is_configurable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUDIT_LOG_RETENTION_DAYS must change the cutoff, not just parse.

    A 200-day-old entry is inside the 2557-day default and outside a 30-day
    window, so this fails if the env var stops reaching the cutoff -- including
    if someone reinstates the `getattr(..., 365)` default, which would keep it.
    """
    settings = _settings(
        monkeypatch,
        AUDIT_PURGE_JOB_ENABLED="true",
        AUDIT_LOG_RETENTION_DAYS="30",
    )
    assert settings.audit_log_retention_days == 30

    entries = [
        {"id": "audit_1", "timestamp": (datetime.now(UTC) - timedelta(days=200)).isoformat()}
    ]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(settings, entries, delete_func=mock_del)

    assert res["purged_count"] == 1
    mock_del.assert_called_once_with("audit_1")


def test_both_settings_are_declared_fields_so_removing_one_fails_this_test() -> None:
    """Guards the exact defect this file exists for.

    `getattr(settings, "audit_purge_job_enabled", True)` cannot be told apart
    from a declared field by any test that only calls the job. This one can:
    delete either field from `Settings` and this fails, whatever the job does.
    """
    assert "audit_purge_job_enabled" in Settings.model_fields
    assert "audit_log_retention_days" in Settings.model_fields


def test_the_defaults_are_purge_off_and_a_seven_year_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deletes the env vars first and asserts the delete worked.

    `Settings(_env_file=None)` does not stop pydantic-settings reading
    `os.environ`, so under the all-flags-on gate (which sets
    AUDIT_PURGE_JOB_ENABLED=true) a bare defaults assertion would assert the
    opposite of its own name and still pass.
    """
    monkeypatch.delenv("AUDIT_PURGE_JOB_ENABLED", raising=False)
    monkeypatch.delenv("AUDIT_LOG_RETENTION_DAYS", raising=False)
    assert "AUDIT_PURGE_JOB_ENABLED" not in os.environ
    assert "AUDIT_LOG_RETENTION_DAYS" not in os.environ

    settings = Settings(_env_file=None)

    assert settings.audit_purge_job_enabled is False
    # 7*365 + 2 leap days. Not 365: the authorisation trail is operations data
    # and §4.84 requires seven years, so the default window must not be the
    # thing that breaches it.
    assert settings.audit_log_retention_days == 2557


def test_a_window_of_zero_is_refused_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """`=0` reads like "off" and means "delete everything"."""
    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="AUDIT_LOG_RETENTION_DAYS"):
        Settings()


# --- the dry run, the row source and the schedule ---------------------------


class _FakeScheduler:
    """Records what was scheduled without starting a thread."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})

    def start(self) -> None:
        self.started = True


async def test_with_no_deleter_the_pass_is_a_dry_run_that_claims_no_deletions(
    enabled_settings: Settings,
) -> None:
    """Today's real state: the audit-log port has no delete method, so the tick
    can only report. Reporting those rows as `purged_count` would be a fabricated
    measurement of a compliance action."""
    entries = [
        {"id": "audit_1", "timestamp": (datetime.now(UTC) - timedelta(days=400)).isoformat()}
    ]

    res = await run_audit_log_purge_job(enabled_settings, entries)

    assert res["status"] == "dry_run"
    assert res["reason"] == "no_audit_deleter_configured"
    assert res["eligible_count"] == 1
    assert res["purged_count"] == 0


async def test_a_row_with_no_id_is_counted_but_never_deleted(
    enabled_settings: Settings,
) -> None:
    """`AuditEntry` carries no document id, so this is every row the real source
    yields. Deleting by a guessed id is not an option."""
    entries = [{"id": None, "timestamp": (datetime.now(UTC) - timedelta(days=400)).isoformat()}]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(enabled_settings, entries, delete_func=mock_del)

    assert res["undeletable_count"] == 1
    assert res["purged_count"] == 0
    mock_del.assert_not_called()


async def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing_the_pass(
    enabled_settings: Settings,
) -> None:
    naive = (datetime.now(UTC) - timedelta(days=400)).replace(tzinfo=None).isoformat()
    entries = [{"id": "audit_1", "timestamp": naive}]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(enabled_settings, entries, delete_func=mock_del)

    assert res["purged_count"] == 1


async def test_the_row_source_asks_the_audit_log_for_rows_older_than_the_cutoff() -> None:
    """Proves the tick reads the real audit-log port rather than a list someone
    passed by hand -- and that every row it yields is id-less, hence undeletable."""

    class _Entry:
        def __init__(self, at: str) -> None:
            self.at = at

    class _FakeAuditLog:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def list_filtered(self, **kwargs: Any) -> list[Any]:
            self.calls.append(kwargs)
            return [_Entry("2019-01-01T00:00:00+00:00")]

    audit_log = _FakeAuditLog()
    source = build_audit_row_source(audit_log, scan_limit=17)  # type: ignore[arg-type]
    cutoff = datetime.now(UTC) - timedelta(days=2557)

    rows = await source(cutoff)

    assert audit_log.calls == [{"to_ts": cutoff.isoformat(), "limit": 17}]
    assert rows == [{"id": None, "timestamp": "2019-01-01T00:00:00+00:00"}]


async def test_a_pass_with_no_row_source_reports_not_executable(
    enabled_settings: Settings,
) -> None:
    res = await run_audit_purge_pass(enabled_settings)

    assert res["status"] == "not_executable"
    assert res["reason"] == "no_audit_row_source_configured"
    # None, not 0: nothing was measured, so "no rows are past retention" is not
    # a claim this pass is entitled to make.
    assert res["eligible_count"] is None
    assert res["purged_count"] is None


def test_the_flag_off_schedules_nothing_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _settings(monkeypatch, AUDIT_PURGE_JOB_ENABLED="false")
    sched = _FakeScheduler()

    assert start_audit_purge_job(off, scheduler=sched) is None
    assert sched.jobs == []
    assert sched.started is False


def test_the_flag_on_schedules_one_daily_job_that_does_not_run_at_boot(
    enabled_settings: Settings,
) -> None:
    sched = _FakeScheduler()

    assert start_audit_purge_job(enabled_settings, scheduler=sched) is sched
    assert sched.started is True
    assert len(sched.jobs) == 1
    job = sched.jobs[0]
    assert job["id"] == "audit_log_purge"
    assert job["trigger"] == "interval"
    assert job["hours"] == PURGE_INTERVAL_HOURS == 24
    # No `next_run_time=now`: a crash-looping container must not be able to drive
    # a deletion pass per restart.
    assert "next_run_time" not in job


def test_the_tick_never_raises_when_the_source_explodes(enabled_settings: Settings) -> None:
    async def _source(_cutoff: datetime) -> list[dict[str, Any]]:
        raise RuntimeError("firestore unavailable")

    res = run_audit_purge_tick(enabled_settings, source=_source)

    assert res["status"] == "failed"
    assert res["purged_count"] is None
    assert "firestore unavailable" in res["error"]
