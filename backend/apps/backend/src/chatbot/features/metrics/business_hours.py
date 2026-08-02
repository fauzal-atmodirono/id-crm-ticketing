"""Sum the working minutes between two timestamps, per a Chatwoot inbox's
native business-hours config (GET /inboxes/{id}).

NOT a copy of agent/app/services/business_hours.py's is_within_business_hours
(a point-in-time boolean) — this computes a DURATION across a date range, a
capability that module doesn't have. Independently implemented in backend/
per this repo's agent/backend service-decoupling convention; both read the
identical `working_hours` row shape Chatwoot returns (day_of_week 0=Sunday..
6=Saturday, open/close hour+minutes, open_all_day/closed_all_day).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)


def working_minutes_between(start: datetime, end: datetime, inbox: dict) -> int:
    """Minutes between start and end that fall within inbox's working hours.

    Both start/end must be timezone-aware. Falls back to plain calendar
    minutes when the inbox has no working hours configured (mirrors
    is_within_business_hours' "always open" fallback). end <= start -> 0.
    """
    if end <= start:
        return 0
    if not inbox.get("working_hours_enabled"):
        return int((end - start).total_seconds() // 60)

    tz_name = inbox.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        _log.debug("working_minutes_between: unknown timezone %r, using UTC", tz_name)
        tz = timezone.utc

    start_local = start.astimezone(tz)
    end_local = end.astimezone(tz)
    rows_by_dow = {r.get("day_of_week"): r for r in (inbox.get("working_hours") or [])}

    total_minutes = 0
    cursor_date: date = start_local.date()
    while cursor_date <= end_local.date():
        dow = (cursor_date.isoweekday()) % 7  # Python Mon=1..Sun=7 -> Chatwoot Sun=0..Sat=6
        row = rows_by_dow.get(dow)
        day_start = datetime.combine(cursor_date, time.min, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        window_start = max(start_local, day_start)
        window_end = min(end_local, day_end)

        if row and not row.get("closed_all_day"):
            if row.get("open_all_day"):
                open_dt, close_dt = day_start, day_end
            else:
                open_dt = day_start + timedelta(
                    hours=int(row.get("open_hour", 0)), minutes=int(row.get("open_minutes", 0))
                )
                close_dt = day_start + timedelta(
                    hours=int(row.get("close_hour", 0)), minutes=int(row.get("close_minutes", 0))
                )
            overlap_start = max(window_start, open_dt)
            overlap_end = min(window_end, close_dt)
            if overlap_end > overlap_start:
                total_minutes += int((overlap_end - overlap_start).total_seconds() // 60)

        cursor_date += timedelta(days=1)

    return total_minutes
