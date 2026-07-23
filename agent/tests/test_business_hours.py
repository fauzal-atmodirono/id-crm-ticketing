from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.business_hours import is_within_business_hours

# Monday 09:00–17:00 open; Sunday closed all day.
INBOX = {
    "working_hours_enabled": True,
    "timezone": "Asia/Kuala_Lumpur",
    "working_hours": [
        {"day_of_week": 1, "closed_all_day": False, "open_all_day": False,
         "open_hour": 9, "open_minutes": 0, "close_hour": 17, "close_minutes": 0},
        {"day_of_week": 0, "closed_all_day": True, "open_all_day": False,
         "open_hour": 0, "open_minutes": 0, "close_hour": 0, "close_minutes": 0},
    ],
}

TZ = ZoneInfo("Asia/Kuala_Lumpur")


def test_within_hours_on_monday_midday():
    assert is_within_business_hours(INBOX, datetime(2026, 7, 20, 12, 0, tzinfo=TZ)) is True


def test_before_open_on_monday():
    assert is_within_business_hours(INBOX, datetime(2026, 7, 20, 8, 0, tzinfo=TZ)) is False


def test_closed_all_day_sunday():
    assert is_within_business_hours(INBOX, datetime(2026, 7, 19, 12, 0, tzinfo=TZ)) is False


def test_day_with_no_row_is_closed():
    # Tuesday has no working_hours row → treated as closed.
    assert is_within_business_hours(INBOX, datetime(2026, 7, 21, 12, 0, tzinfo=TZ)) is False


def test_disabled_working_hours_is_always_open():
    assert is_within_business_hours({"working_hours_enabled": False}) is True
