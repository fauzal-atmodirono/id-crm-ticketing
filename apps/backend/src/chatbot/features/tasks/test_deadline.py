from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from chatbot.features.tasks.deadline import TaskItem, compute_deadlines
from chatbot.platform.config import Settings


_NOW = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "tasks_reminder_warning_minutes": 60,
        "tasks_reminder_whatsapp_enabled": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float) -> int:
    return int((_NOW - timedelta(hours=hours_ago)).timestamp())


def _conv(conv_id: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": conv_id, "status": "open"}
    base.update(fields)
    return base


def test_response_deadline_computed_from_created_at() -> None:
    conv = _conv(1, created_at=_epoch(3))  # created 3h ago
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.conv_id == "1"
    # response_deadline = created + 8h; remaining = 8h - 3h = 5h = 18000s
    assert item.response_remaining_seconds is not None
    assert abs(item.response_remaining_seconds - 18000) < 5  # within 5s of rounding
    assert item.resolution_remaining_seconds is not None
    assert abs(item.resolution_remaining_seconds - (45 * 3600)) < 5  # 48-3 = 45h


def test_response_already_breached_gives_negative_remaining() -> None:
    conv = _conv(2, created_at=_epoch(10))  # 10h old, past 8h response SLA
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.response_remaining_seconds is not None
    assert item.response_remaining_seconds < 0
    assert item.breach_type == "NO_RESPONSE"


def test_resolution_breach_takes_priority_when_both_breached() -> None:
    conv = _conv(3, status="pending", created_at=_epoch(50))  # 50h > 48h
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.resolution_remaining_seconds is not None
    assert item.resolution_remaining_seconds < 0
    assert item.breach_type == "UNRESOLVED"


def test_sla_minutes_label_overrides_resolution_threshold() -> None:
    # sla_480 label means 480 minutes (8h) resolution SLA
    conv = _conv(4, created_at=_epoch(7), labels=["sla_480"])
    item = compute_deadlines(conv, _settings(), _NOW)
    # resolution = 480 min = 8h; 7h elapsed → 1h remaining = 3600s
    assert item.resolution_remaining_seconds is not None
    assert abs(item.resolution_remaining_seconds - 3600) < 5


def test_custom_attributes_sla_minutes_overrides_resolution() -> None:
    conv = _conv(5, created_at=_epoch(7), custom_attributes={"sla_minutes": 480})
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.resolution_remaining_seconds is not None
    assert abs(item.resolution_remaining_seconds - 3600) < 5


def test_resolved_conversation_has_no_breach() -> None:
    conv = _conv(6, status="resolved", created_at=_epoch(50))
    item = compute_deadlines(conv, _settings(), _NOW)
    # Resolved conversations should show remaining as None (ticket closed)
    assert item.breach_type is None


def test_first_reply_clears_response_breach() -> None:
    # Conversation already has a first reply (first_reply_created_at is set)
    conv = _conv(7, created_at=_epoch(10), first_reply_created_at=_epoch(5))
    item = compute_deadlines(conv, _settings(), _NOW)
    # Agent already replied so no NO_RESPONSE breach possible
    assert item.breach_type != "NO_RESPONSE"


def test_agent_id_extracted_from_meta() -> None:
    conv = _conv(8, created_at=_epoch(1), meta={"assignee": {"id": 42, "name": "Bob"}})
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.agent_id == "42"


def test_subject_from_meta_sender_name() -> None:
    conv = _conv(9, created_at=_epoch(1), meta={"sender": {"name": "Alice"}})
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.subject == "Alice"


def test_task_item_is_dataclass_with_expected_fields() -> None:
    conv = _conv(10, created_at=_epoch(1))
    item = compute_deadlines(conv, _settings(), _NOW)
    assert isinstance(item, TaskItem)
    assert hasattr(item, "conv_id")
    assert hasattr(item, "response_deadline_iso")
    assert hasattr(item, "resolution_deadline_iso")
    assert hasattr(item, "response_remaining_seconds")
    assert hasattr(item, "resolution_remaining_seconds")
    assert hasattr(item, "breach_type")
