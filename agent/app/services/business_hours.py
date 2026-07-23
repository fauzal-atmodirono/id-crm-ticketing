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
from datetime import datetime, timezone
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
