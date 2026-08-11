"""Unit tests for Audit Log Retention Purge (P13 Task 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
import pytest

from chatbot.features.authz.audit_purge import run_audit_log_purge_job


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy()


async def test_audit_records_older_than_retention_days_are_purged(settings) -> None:
    old_time = datetime.now(UTC) - timedelta(days=400)
    entries = [{"id": "audit_1", "timestamp": old_time.isoformat()}]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(settings, entries, delete_func=mock_del)

    assert res["status"] == "completed"
    assert res["purged_count"] == 1
    mock_del.assert_called_once_with("audit_1")


async def test_recent_audit_records_are_retained(settings) -> None:
    recent_time = datetime.now(UTC) - timedelta(days=30)
    entries = [{"id": "audit_2", "timestamp": recent_time.isoformat()}]
    mock_del = AsyncMock()

    res = await run_audit_log_purge_job(settings, entries, delete_func=mock_del)

    assert res["purged_count"] == 0
    mock_del.assert_not_called()


async def test_audit_purge_can_be_disabled_via_flag(settings) -> None:
    off_settings = settings.model_copy(update={"audit_purge_job_enabled": False})
    old_time = datetime.now(UTC) - timedelta(days=400)
    entries = [{"id": "audit_1", "timestamp": old_time.isoformat()}]

    res = await run_audit_log_purge_job(off_settings, entries)
    assert res["status"] == "skipped"
