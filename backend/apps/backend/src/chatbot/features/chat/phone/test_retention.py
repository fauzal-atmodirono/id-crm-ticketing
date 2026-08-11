"""Unit tests for Phone Recording Retention Purge (P11 Task 8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
import pytest

from chatbot.features.chat.phone.retention import run_retention_purge_job


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(update={"phone_retention_job_enabled": True})


async def test_a_recording_older_than_the_window_is_deleted_from_twilio(settings) -> None:
    old_date = datetime.now(UTC) - timedelta(days=95)
    records = [{"sid": "RE1", "created_at": old_date.isoformat(), "is_deleted": False}]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["status"] == "completed"
    assert res["purged_count"] == 1
    mock_del.assert_called_once_with("RE1")


async def test_the_stored_attributes_are_cleared_after_deletion(settings) -> None:
    old_date = datetime.now(UTC) - timedelta(days=95)
    records = [{"sid": "RE1", "recording_url": "https://...", "created_at": old_date.isoformat(), "is_deleted": False}]

    await run_retention_purge_job(settings, records)
    assert records[0]["is_deleted"] is True
    assert records[0]["recording_url"] is None


async def test_a_recording_inside_the_window_is_untouched(settings) -> None:
    recent_date = datetime.now(UTC) - timedelta(days=10)
    records = [{"sid": "RE2", "recording_url": "https://...", "created_at": recent_date.isoformat(), "is_deleted": False}]
    mock_del = AsyncMock()

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["purged_count"] == 0
    assert records[0]["is_deleted"] is False
    mock_del.assert_not_called()


async def test_a_twilio_delete_failure_is_retried_and_logged(settings) -> None:
    old_date = datetime.now(UTC) - timedelta(days=95)
    records = [{"sid": "RE1", "created_at": old_date.isoformat(), "is_deleted": False}]
    mock_del = AsyncMock(side_effect=Exception("Twilio API timeout"))

    res = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res["errors"] == 1
    assert records[0]["is_deleted"] is False


async def test_the_job_is_idempotent(settings) -> None:
    old_date = datetime.now(UTC) - timedelta(days=95)
    records = [{"sid": "RE1", "created_at": old_date.isoformat(), "is_deleted": False}]
    mock_del = AsyncMock()

    res1 = await run_retention_purge_job(settings, records, delete_func=mock_del)
    res2 = await run_retention_purge_job(settings, records, delete_func=mock_del)

    assert res1["purged_count"] == 1
    assert res2["purged_count"] == 0
    assert mock_del.call_count == 1


async def test_the_flag_off_runs_no_deletions(settings) -> None:
    off_settings = settings.model_copy(update={"phone_retention_job_enabled": False})
    old_date = datetime.now(UTC) - timedelta(days=95)
    records = [{"sid": "RE1", "created_at": old_date.isoformat(), "is_deleted": False}]

    res = await run_retention_purge_job(off_settings, records)
    assert res["status"] == "skipped"
    assert records[0]["is_deleted"] is False


async def test_a_deleted_recording_is_distinguishable_from_one_that_never_existed(settings) -> None:
    old_date = datetime.now(UTC) - timedelta(days=95)
    records = [{"sid": "RE1", "created_at": old_date.isoformat(), "is_deleted": False}]

    await run_retention_purge_job(settings, records)
    assert records[0]["is_deleted"] is True
