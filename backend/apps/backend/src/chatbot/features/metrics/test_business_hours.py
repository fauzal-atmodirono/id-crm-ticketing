from datetime import UTC, datetime

from chatbot.features.metrics.business_hours import working_minutes_between

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
