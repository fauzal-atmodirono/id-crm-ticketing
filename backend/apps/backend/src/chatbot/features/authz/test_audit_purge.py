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

from datetime import UTC, datetime, timedelta
import os
from unittest.mock import AsyncMock

import pytest

from chatbot.features.authz.audit_purge import run_audit_log_purge_job
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

    entries = [{"id": "audit_1", "timestamp": (datetime.now(UTC) - timedelta(days=200)).isoformat()}]
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
