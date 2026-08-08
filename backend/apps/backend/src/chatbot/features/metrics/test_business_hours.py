from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from chatbot.features.metrics.business_hours import (
    next_working_instant,
    working_minutes_between,
)

# Monday-Friday 09:00-18:00 UTC (day_of_week: Sunday=0..Saturday=6)
INBOX_9_TO_6 = {
    "working_hours_enabled": True,
    "timezone": "UTC",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18, "close_minutes": 0,
         "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)  # Mon..Fri
    ] + [
        {"day_of_week": d, "closed_all_day": True} for d in (0, 6)  # Sun, Sat
    ],
}

NO_HOURS_CONFIGURED = {"working_hours_enabled": False}


def test_same_day_within_hours():
    start = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)  # Monday
    end = datetime(2026, 7, 6, 12, 30, tzinfo=UTC)
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 150


def test_spans_a_weekend_excludes_it():
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)  # Friday 17:00 (1h left in the day)
    end = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)     # Monday 10:00 (1h into the day)
    # Fri 17:00-18:00 = 60min, Sat/Sun = 0, Mon 09:00-10:00 = 60min
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 120


def test_starts_before_hours_clips_to_open():
    start = datetime(2026, 7, 6, 6, 0, tzinfo=UTC)   # Monday 06:00, before 09:00 open
    end = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 60


def test_ends_after_hours_clips_to_close():
    start = datetime(2026, 7, 6, 17, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 23, 0, tzinfo=UTC)  # well past 18:00 close
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 60


def test_no_hours_configured_falls_back_to_calendar_minutes():
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    total_calendar_minutes = int((end - start).total_seconds() // 60)
    assert working_minutes_between(start, end, NO_HOURS_CONFIGURED) == total_calendar_minutes


def test_end_before_start_returns_zero():
    start = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 0


def test_unknown_timezone_falls_back_to_utc_not_crash():
    inbox = dict(INBOX_9_TO_6, timezone="Not/AZone")
    start = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)
    assert working_minutes_between(start, end, inbox) == 60


# --- next_working_instant -------------------------------------------------
#
# P1 task 1. Answers "when does this inbox next open?", which is what an
# after-hours arrival's `attend_after` stamp needs. Walks the same calendar
# working_minutes_between walks — deliberately no second implementation.

INBOX_WITH_SATURDAY = {
    "working_hours_enabled": True,
    "timezone": "UTC",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18,
         "close_minutes": 0, "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)
    ] + [
        {"day_of_week": 6, "open_hour": 10, "open_minutes": 30, "close_hour": 14,
         "close_minutes": 0, "open_all_day": False, "closed_all_day": False},
        {"day_of_week": 0, "closed_all_day": True},
    ],
}

INBOX_MYT = {
    "working_hours_enabled": True,
    "timezone": "Asia/Kuala_Lumpur",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18,
         "close_minutes": 0, "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)
    ] + [
        {"day_of_week": d, "closed_all_day": True} for d in (0, 6)
    ],
}


def test_an_instant_already_inside_working_hours_is_returned_unchanged():
    at = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)  # Monday 10:00, inside 09:00-18:00
    assert next_working_instant(at, INBOX_9_TO_6) == at


def test_a_friday_evening_instant_moves_to_monday_opening():
    at = datetime(2026, 7, 3, 18, 30, tzinfo=UTC)  # Friday, after 18:00 close
    assert next_working_instant(at, INBOX_9_TO_6) == datetime(2026, 7, 6, 9, 0, tzinfo=UTC)


def test_a_saturday_instant_moves_to_the_saturday_opening_when_saturday_is_open():
    at = datetime(2026, 7, 4, 8, 0, tzinfo=UTC)  # Saturday 08:00, before the 10:30 open
    assert next_working_instant(at, INBOX_WITH_SATURDAY) == datetime(
        2026, 7, 4, 10, 30, tzinfo=UTC
    )


def test_an_inbox_with_working_hours_disabled_returns_the_instant_unchanged():
    at = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)  # Saturday 03:00 — always "open"
    assert next_working_instant(at, NO_HOURS_CONFIGURED) == at


def test_an_inbox_with_no_working_hours_rows_returns_the_instant_unchanged():
    inbox = {"working_hours_enabled": True, "timezone": "UTC", "working_hours": []}
    at = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)
    assert next_working_instant(at, inbox) == at


def test_a_closed_all_day_row_is_skipped_to_the_next_open_day():
    at = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)  # Sunday, closed all day
    assert next_working_instant(at, INBOX_9_TO_6) == datetime(2026, 7, 6, 9, 0, tzinfo=UTC)


def test_an_open_all_day_row_returns_the_instant_unchanged():
    inbox = {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [{"day_of_week": 6, "open_all_day": True, "closed_all_day": False}],
    }
    at = datetime(2026, 7, 4, 3, 0, tzinfo=UTC)  # Saturday 03:00
    assert next_working_instant(at, inbox) == at


def test_the_result_is_computed_in_the_inbox_timezone_not_utc():
    # Friday 18:30 MYT (= 10:30 UTC) is after the MYT close. The next opening is
    # Monday 09:00 *MYT*, i.e. 01:00 UTC — not Monday 09:00 UTC.
    at = datetime(2026, 7, 3, 10, 30, tzinfo=UTC)
    expected = datetime(2026, 7, 6, 9, 0, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))
    assert next_working_instant(at, INBOX_MYT) == expected


def test_an_unknown_timezone_falls_back_to_utc_without_raising():
    inbox = dict(INBOX_9_TO_6, timezone="Not/AZone")
    at = datetime(2026, 7, 3, 18, 30, tzinfo=UTC)
    assert next_working_instant(at, inbox) == datetime(2026, 7, 6, 9, 0, tzinfo=UTC)


def test_an_all_closed_config_returns_the_instant_unchanged_rather_than_looping():
    # Fail open: a case that is never "attendable" must not become a case that is
    # never enforced. The 14-day cap is what makes this terminate.
    inbox = {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [{"day_of_week": d, "closed_all_day": True} for d in range(7)],
    }
    at = datetime(2026, 7, 3, 18, 30, tzinfo=UTC)
    assert next_working_instant(at, inbox) == at
