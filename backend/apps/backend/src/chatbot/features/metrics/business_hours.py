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


def _resolve_tz(inbox: dict, *, caller: str):
    tz_name = inbox.get("timezone") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        _log.debug("%s: unknown timezone %r, using UTC", caller, tz_name)
        return timezone.utc


# A pathological config (every day closed) would otherwise walk forever. 14 days
# is comfortably past any real weekly cycle plus a public-holiday run.
_MAX_LOOKAHEAD_DAYS = 14


def next_working_instant(after: datetime, inbox: dict) -> datetime:
    """The first instant at or after `after` that falls inside working hours.

    Returns `after` unchanged when it is already inside working hours, when the
    inbox has no working-hours config (the "always open" fallback
    working_minutes_between uses), or when no opening can be found within
    _MAX_LOOKAHEAD_DAYS.

    That last case is a deliberate fail-open: a case that is never "attendable"
    must not become a case that is never enforced. Callers stamp the result as
    `attend_after`, so returning `after` means "attend now" rather than "never".

    Walks the same day-by-day calendar as working_minutes_between and reads the
    identical row shape (day_of_week 0=Sunday..6=Saturday).
    """
    if not inbox.get("working_hours_enabled"):
        return after

    rows_by_dow = {r.get("day_of_week"): r for r in (inbox.get("working_hours") or [])}
    if not rows_by_dow:
        return after

    tz = _resolve_tz(inbox, caller="next_working_instant")
    after_local = after.astimezone(tz)

    cursor_date: date = after_local.date()
    for _ in range(_MAX_LOOKAHEAD_DAYS):
        dow = (cursor_date.isoweekday()) % 7
        row = rows_by_dow.get(dow)
        if row and not row.get("closed_all_day"):
            day_start = datetime.combine(cursor_date, time.min, tzinfo=tz)
            if row.get("open_all_day"):
                open_dt, close_dt = day_start, day_start + timedelta(days=1)
            else:
                open_dt = day_start + timedelta(
                    hours=int(row.get("open_hour", 0)),
                    minutes=int(row.get("open_minutes", 0)),
                )
                close_dt = day_start + timedelta(
                    hours=int(row.get("close_hour", 0)),
                    minutes=int(row.get("close_minutes", 0)),
                )
            # Already inside today's window -> now. Before it -> the opening.
            # After it -> fall through to the next day.
            if after_local < close_dt:
                return after_local if after_local >= open_dt else open_dt

        cursor_date += timedelta(days=1)

    _log.debug(
        "next_working_instant: no opening within %d days for inbox tz=%r; "
        "failing open and returning the original instant",
        _MAX_LOOKAHEAD_DAYS,
        inbox.get("timezone"),
    )
    return after
