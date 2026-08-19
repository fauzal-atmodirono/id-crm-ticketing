"""The customer-update clock: it starts when the dealer answers, not when the
case was created, and only an explicit stop-stamp satisfies it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.customer_update import (
    REPLIED_ATTR,
    UPDATED_ATTR,
    compute_customer_update_clock,
)
from chatbot.platform.config import Settings

_REPLIED = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "escalation_customer_update_enabled": True,
        "escalation_customer_update_hours": 4.0,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def _conv(
    *,
    replied_at: datetime | None = _REPLIED,
    updated_at: datetime | None = None,
    status: str = "open",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = dict(extra or {})
    if replied_at is not None:
        attrs[REPLIED_ATTR] = replied_at.isoformat()
    if updated_at is not None:
        attrs[UPDATED_ATTR] = updated_at.isoformat()
    return {"id": 42, "status": status, "custom_attributes": attrs}


def _at(hours: float) -> datetime:
    return _REPLIED + timedelta(hours=hours)


def test_the_clock_starts_at_the_reply_not_at_creation() -> None:
    clock = compute_customer_update_clock(_conv(), _settings(), _at(1))

    assert clock.started_at == _REPLIED
    assert clock.elapsed_minutes == 60
    assert clock.remaining_minutes == 180
    assert not clock.breached


def test_no_dealer_reply_means_no_obligation() -> None:
    clock = compute_customer_update_clock(_conv(replied_at=None), _settings(), _at(99))

    assert clock.started_at is None
    assert not clock.breached
    assert not clock.satisfied


def test_an_update_after_the_reply_satisfies_the_clock() -> None:
    clock = compute_customer_update_clock(
        _conv(updated_at=_at(2)), _settings(), _at(9)
    )

    assert clock.satisfied
    assert not clock.breached


def test_an_update_from_before_the_reply_does_not_satisfy_it() -> None:
    """The customer was updated, then the dealer answered again. That is a new
    obligation, and the old stamp must not discharge it."""
    clock = compute_customer_update_clock(
        _conv(updated_at=_REPLIED - timedelta(hours=3)), _settings(), _at(5)
    )

    assert not clock.satisfied
    assert clock.breached


def test_past_the_window_is_a_breach() -> None:
    clock = compute_customer_update_clock(_conv(), _settings(), _at(4.5))

    assert clock.breached
    assert clock.remaining_minutes is not None and clock.remaining_minutes < 0
    assert not clock.warning_due  # the breach is the signal; a warning would be noise


def test_the_warning_fires_in_the_second_half_of_the_window() -> None:
    assert not compute_customer_update_clock(_conv(), _settings(), _at(1.9)).warning_due
    assert compute_customer_update_clock(_conv(), _settings(), _at(2.1)).warning_due


def test_a_resolved_case_owes_the_customer_nothing() -> None:
    clock = compute_customer_update_clock(
        _conv(status="resolved"), _settings(), _at(48)
    )

    assert clock.started_at is None
    assert not clock.breached


def test_the_flag_off_yields_no_clock_at_all() -> None:
    clock = compute_customer_update_clock(
        _conv(), _settings(escalation_customer_update_enabled=False), _at(99)
    )

    assert clock == compute_customer_update_clock({}, _settings(), _at(99))
    assert clock.started_at is None


def test_a_garbage_timestamp_is_treated_as_absent_not_raised() -> None:
    conv = {"id": 1, "status": "open", "custom_attributes": {REPLIED_ATTR: "yesterday"}}

    assert compute_customer_update_clock(conv, _settings(), _at(1)).started_at is None


def test_a_naive_timestamp_is_read_as_utc() -> None:
    conv = {
        "id": 1,
        "status": "open",
        "custom_attributes": {REPLIED_ATTR: "2026-08-19T09:00:00"},
    }

    assert compute_customer_update_clock(conv, _settings(), _at(1)).elapsed_minutes == 60


def test_working_hours_do_not_run_overnight() -> None:
    """A dealer answering at 16:00 on a 9-17 inbox has not burned the 4-hour
    window by 10:00 the next morning -- only 1 working hour of it."""
    inbox = {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [
            {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 17, "close_minutes": 0}
            for d in range(7)
        ],
    }
    replied = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)
    conv = _conv(replied_at=replied)
    next_morning = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)

    wall = compute_customer_update_clock(conv, _settings(), next_morning)
    working = compute_customer_update_clock(
        conv, _settings(), next_morning, inbox=inbox, working_hours=True
    )

    assert wall.breached  # 18 wall-clock hours
    assert not working.breached  # 1h to close + 1h after open = 2 working hours
    assert working.elapsed_minutes == 120
