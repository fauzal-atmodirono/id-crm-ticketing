"""Read-only evaluation of Chatwoot's native per-inbox business hours.

We deliberately do NOT own a business-hours config: the tenant configures
working hours + timezone + the out-of-office auto-reply natively per inbox in
the Chatwoot UI, and Chatwoot posts the out-of-office reply itself. This helper
only *reads* whether "now" is within those hours, so the lifecycle scanner can
pick the right auto-close grace and tag out-of-hours cases.

Chatwoot's `working_hours` uses day_of_week 0=Sunday..6=Saturday. Python's
isoweekday() is Monday=1..Sunday=7, so `isoweekday() % 7` maps Sunday→0.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def is_within_business_hours(inbox: dict, now: datetime | None = None) -> bool:
    if not inbox.get("working_hours_enabled"):
        # No native hours configured → treat as always open (in-hours grace).
        return True

    tz_name = inbox.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.debug("business_hours: unknown timezone %r, using UTC", tz_name)
        tz = timezone.utc

    now = now.astimezone(tz) if now is not None else datetime.now(tz)
    dow = now.isoweekday() % 7  # Sunday=0..Saturday=6, matching Chatwoot

    rows = inbox.get("working_hours") or []
    row = next((r for r in rows if r.get("day_of_week") == dow), None)
    if row is None or row.get("closed_all_day"):
        return False
    if row.get("open_all_day"):
        return True

    open_minute = int(row.get("open_hour", 0)) * 60 + int(row.get("open_minutes", 0))
    close_minute = int(row.get("close_hour", 0)) * 60 + int(row.get("close_minutes", 0))
    now_minute = now.hour * 60 + now.minute
    return open_minute <= now_minute < close_minute


# A pathological config (every day closed) would otherwise walk forever.
_MAX_LOOKAHEAD_DAYS = 14


def next_working_instant(after: datetime, inbox: dict) -> datetime:
    """The first instant at or after *after* that falls inside working hours.

    Used to stamp `attend_after` on an out-of-hours arrival, so the promise in
    the after-hours auto-reply ("our team will reach out on the next business
    day") has a real timestamp behind it.

    Mirrors backend's `features.metrics.business_hours.next_working_instant`.
    Deliberately a second implementation rather than a shared import: `agent`
    and `backend` are separate services with no shared process (see CLAUDE.md),
    exactly as `is_within_business_hours` above mirrors that module's
    `working_minutes_between`. Both read the identical Chatwoot row shape.

    Fails open: returns *after* unchanged when hours are not configured or no
    opening is found within _MAX_LOOKAHEAD_DAYS. A case that is never
    "attendable" must not become a case that is never attended.
    """
    if not inbox.get("working_hours_enabled"):
        return after

    rows = inbox.get("working_hours") or []
    if not rows:
        return after

    tz_name = inbox.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.debug("next_working_instant: unknown timezone %r, using UTC", tz_name)
        tz = timezone.utc

    local = after.astimezone(tz)
    rows_by_dow = {r.get("day_of_week"): r for r in rows}

    cursor = local.date()
    for _ in range(_MAX_LOOKAHEAD_DAYS):
        row = rows_by_dow.get(cursor.isoweekday() % 7)
        if row and not row.get("closed_all_day"):
            day_start = datetime.combine(cursor, time.min, tzinfo=tz)
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
            if local < close_dt:
                return local if local >= open_dt else open_dt

        cursor += timedelta(days=1)

    logger.debug(
        "next_working_instant: no opening within %d days; failing open",
        _MAX_LOOKAHEAD_DAYS,
    )
    return after
