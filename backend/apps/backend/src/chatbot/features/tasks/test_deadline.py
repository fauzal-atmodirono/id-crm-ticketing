from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

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


# --- P6 task 10: per-ticket follow-up date -----------------------------
#
# A follow-up date is an agent's own reminder note ("look at this again
# Thursday"), never a policy commitment. `sla_minutes`/`resolution_deadline_iso`/
# `breach_type` are the SLA engine's; `follow_up_at_iso`/`follow_up_remaining_seconds`
# are new, separate fields so the two can never be read as the same thing.


def test_the_reminder_fires_at_the_follow_up_date() -> None:
    """Once `now` passes `follow_up_at`, `follow_up_remaining_seconds` goes
    negative -- that is the reminder having fired."""
    conv = _conv(
        20,
        created_at=_epoch(1),
        custom_attributes={"follow_up_at": (_NOW - timedelta(hours=1)).isoformat()},
    )
    item = compute_deadlines(conv, _settings(follow_up_date_enabled=True), _NOW)
    assert item.follow_up_at_iso is not None
    assert item.follow_up_remaining_seconds is not None
    assert item.follow_up_remaining_seconds < 0


def test_a_follow_up_date_is_not_treated_as_an_sla_deadline() -> None:
    """A follow-up date well in the past on a conversation that is nowhere
    near either SLA threshold must NOT become a breach, and the SLA fields
    must compute identically to a conversation with no follow_up_at at all --
    proving the two are read independently, not merged."""
    conv_with_follow_up = _conv(
        21,
        created_at=_epoch(1),  # nowhere near the 8h/48h SLA thresholds
        custom_attributes={"follow_up_at": (_NOW - timedelta(days=3)).isoformat()},
    )
    conv_without_follow_up = _conv(21, created_at=_epoch(1))

    settings = _settings(follow_up_date_enabled=True)
    item = compute_deadlines(conv_with_follow_up, settings, _NOW)
    baseline = compute_deadlines(conv_without_follow_up, settings, _NOW)

    assert item.follow_up_remaining_seconds is not None
    assert item.follow_up_remaining_seconds < 0  # the reminder is overdue
    assert item.breach_type is None  # but that is not an SLA breach
    assert item.breach_type == baseline.breach_type
    assert item.resolution_deadline_iso == baseline.resolution_deadline_iso
    assert item.resolution_remaining_seconds == baseline.resolution_remaining_seconds
    assert item.response_deadline_iso == baseline.response_deadline_iso


def test_clearing_the_date_cancels_the_reminder() -> None:
    """An empty string or an absent `follow_up_at` must both compute to no
    active reminder -- clearing genuinely cancels it, it isn't just ignored
    on write and left dangling in the computed view."""
    cleared = _conv(22, created_at=_epoch(1), custom_attributes={"follow_up_at": ""})
    absent = _conv(23, created_at=_epoch(1))

    settings = _settings(follow_up_date_enabled=True)
    item_cleared = compute_deadlines(cleared, settings, _NOW)
    item_absent = compute_deadlines(absent, settings, _NOW)

    assert item_cleared.follow_up_at_iso is None
    assert item_cleared.follow_up_remaining_seconds is None
    assert item_absent.follow_up_at_iso is None
    assert item_absent.follow_up_remaining_seconds is None


def test_sla_minutes_behaviour_is_completely_unchanged() -> None:
    """`sla_minutes`-driven resolution deadline/breach must compute exactly
    as before this feature existed, whether or not a `follow_up_at` also
    happens to be present, and whether or not the flag is even on. This is
    the regression baseline for the separation guarantee: adding a follow-up
    date must never perturb SLA arithmetic by so much as a second."""
    conv_with_follow_up = _conv(
        24,
        created_at=_epoch(7),
        custom_attributes={
            "sla_minutes": 480,
            "follow_up_at": (_NOW + timedelta(days=2)).isoformat(),
        },
    )
    conv_sla_only = _conv(24, created_at=_epoch(7), custom_attributes={"sla_minutes": 480})

    item_on = compute_deadlines(conv_with_follow_up, _settings(follow_up_date_enabled=True), _NOW)
    item_off = compute_deadlines(conv_with_follow_up, _settings(follow_up_date_enabled=False), _NOW)
    baseline = compute_deadlines(conv_sla_only, _settings(), _NOW)

    # sla_480 label/attr means 480 minutes (8h) resolution SLA; 7h elapsed ->
    # 1h = 3600s remaining, exactly as before this feature existed.
    assert item_on.resolution_remaining_seconds is not None
    assert abs(item_on.resolution_remaining_seconds - 3600) < 5
    assert item_on.resolution_remaining_seconds == baseline.resolution_remaining_seconds
    assert item_on.resolution_deadline_iso == baseline.resolution_deadline_iso
    assert item_on.breach_type == baseline.breach_type

    # Flag off: no follow-up fields populated at all, everything else
    # byte-for-byte identical to the flag being on.
    assert item_off.follow_up_at_iso is None
    assert item_off.follow_up_remaining_seconds is None
    assert item_off.resolution_remaining_seconds == item_on.resolution_remaining_seconds
    assert item_off.breach_type == item_on.breach_type


# ---------------------------------------------------------------------------
# B-EM-04 / B-EM-05: the two promises My-Tasks now shows (2026-08-19)
# ---------------------------------------------------------------------------


def _attr_conv(**attrs: Any) -> dict[str, Any]:
    return {
        "id": 77,
        "status": "open",
        "created_at": int(_NOW.timestamp()) - 3600,
        "first_reply_created_at": int(_NOW.timestamp()) - 3000,
        "custom_attributes": dict(attrs),
    }


def test_attend_after_is_surfaced_and_never_sets_breach_type() -> None:
    """The next-business-hour promise says when work may START. An agent
    looking at one in the future is early, not late."""
    item = compute_deadlines(
        _attr_conv(attend_after="2026-08-20T09:00:00+08:00"), _settings(), _NOW
    )

    assert item.attend_after_iso == "2026-08-20T09:00:00+08:00"
    assert item.breach_type is None


def test_a_malformed_attend_after_is_ignored_rather_than_raised() -> None:
    item = compute_deadlines(_attr_conv(attend_after="tomorrow morning"), _settings(), _NOW)

    assert item.attend_after_iso is None


def test_the_customer_update_deadline_runs_from_the_dealer_reply() -> None:
    replied = (_NOW - timedelta(hours=1)).isoformat()
    settings = _settings(
        escalation_customer_update_enabled=True, escalation_customer_update_hours=4.0
    )

    item = compute_deadlines(_attr_conv(escalation_replied_at=replied), settings, _NOW)

    assert item.customer_update_at_iso == (_NOW + timedelta(hours=3)).isoformat()
    assert item.customer_update_remaining_seconds == 3 * 3600
    assert item.breach_type is None  # an owed update is not an SLA breach


def test_no_customer_update_deadline_once_the_customer_was_told() -> None:
    settings = _settings(escalation_customer_update_enabled=True)
    conv = _attr_conv(
        escalation_replied_at=(_NOW - timedelta(hours=3)).isoformat(),
        customer_updated_at=(_NOW - timedelta(hours=2)).isoformat(),
    )

    assert compute_deadlines(conv, settings, _NOW).customer_update_at_iso is None


def test_the_customer_update_field_is_absent_while_the_flag_is_off() -> None:
    conv = _attr_conv(escalation_replied_at=(_NOW - timedelta(hours=9)).isoformat())

    assert compute_deadlines(conv, _settings(), _NOW).customer_update_at_iso is None
