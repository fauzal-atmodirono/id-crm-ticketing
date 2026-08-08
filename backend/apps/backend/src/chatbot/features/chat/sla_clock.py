"""The single entry point every SLA threshold comparison goes through.

Why this module exists: SLA *enforcement* measured wall-clock seconds while SLA
*reporting* already stored `first_response_working_minutes`, so the engine that
pages a PIC and the dashboard that grades them disagreed by construction. Both
now read the same clock.

The `working_hours=False` path reproduces the arithmetic of
``sla._conversation_age_seconds`` exactly — asserted in
``test_sla_clock.py::test_working_hours_false_matches_the_old_age_seconds_arithmetic_exactly``.
That equivalence is what lets the working-hours clock ship dark behind a flag on
a tenant whose live breach alerts wake a real person up.

There is deliberately no second working-hours implementation here: the calendar
walk is ``metrics.business_hours.working_minutes_between``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from chatbot.features.metrics.business_hours import working_minutes_between

_log = logging.getLogger(__name__)


def _as_aware(value: datetime) -> datetime:
    """Chatwoot epochs arrive aware; hand-built datetimes sometimes do not."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def elapsed_minutes(
    start: datetime,
    now: datetime,
    inbox: dict[str, Any],
    *,
    working_hours: bool,
) -> float:
    """Minutes elapsed between `start` and `now`, on whichever clock is enabled.

    With ``working_hours=False`` this is plain calendar minutes, to the same
    precision the old seconds-based arithmetic produced (a float, not a
    truncated int — truncating here would shift every existing threshold
    comparison by up to a minute).

    With ``working_hours=True`` it is the inbox's configured working minutes.
    An inbox with no working-hours config — including ``{}`` from a failed fetch
    — falls through to calendar minutes, so a Chatwoot outage degrades to
    today's behaviour rather than to "nothing ever breaches".

    ``now <= start`` is 0, never negative.
    """
    start = _as_aware(start)
    now = _as_aware(now)

    if now <= start:
        return 0.0

    if not working_hours or not inbox.get("working_hours_enabled"):
        return (now - start).total_seconds() / 60

    return float(working_minutes_between(start, now, inbox))


class InboxCache:
    """Per-scan memo of the conversation log's ``get_inbox_working_hours``.

    An SLA scan walks every open conversation, and resolving working hours
    per conversation would turn one API call into a call per conversation — a
    ~100x amplification on a real tenant. One instance per scan, one fetch per
    distinct inbox.

    Deliberately reuses the existing ``ConversationLogPort.get_inbox_working_hours``
    rather than adding a second inbox fetch: that method already returns the raw
    Chatwoot inbox record in the row shape ``working_minutes_between`` reads, and
    already fails open by returning ``None``.

    A failed fetch is memoised as ``{}`` rather than retried: the caller falls
    back to wall-clock minutes either way, and re-asking a Chatwoot that is
    already failing just multiplies the outage.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._cache: dict[Any, dict[str, Any]] = {}

    async def get(self, inbox_id: Any) -> dict[str, Any]:
        if inbox_id is None or self._client is None:
            return {}
        if inbox_id in self._cache:
            return self._cache[inbox_id]

        try:
            inbox = await self._client.get_inbox_working_hours(inbox_id) or {}
        except Exception:  # noqa: BLE001 — fail open; never abort a scan
            _log.warning(
                "InboxCache: could not fetch inbox %r; falling back to wall-clock "
                "minutes for its conversations",
                inbox_id,
                exc_info=True,
            )
            inbox = {}

        if not isinstance(inbox, dict):
            inbox = {}

        self._cache[inbox_id] = inbox
        return inbox
