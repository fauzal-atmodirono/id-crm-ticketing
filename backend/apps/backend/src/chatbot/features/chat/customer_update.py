"""How long the customer has been waiting since the dealer/PIC answered.

The gap this closes: when a dealer or PIC replies to an escalation, the
agent/ service links the reply onto the case as a private note, stamps
``escalation_replied_at``, and posts an AI-drafted customer reply beside it
(``agent/app/services/escalation_replies.py``). Then nothing happens until a
human acts. Appendix B gives that human 4 working hours (B-EM-05), but no
clock measured it -- the SLA engine's two breaches both run from
``created_at`` and are satisfied by *any* first agent reply, which on an
escalated case happened long before the dealer answered.

So this is a third clock, and it deliberately starts late: at the moment the
answer existed and the customer still did not have it.

Two attributes define it, both written by agent/:

* ``escalation_replied_at`` -- starts the clock (escalation_replies.py)
* ``customer_updated_at``   -- stops it (sync.maybe_stamp_customer_update)

Why an explicit stop-stamp rather than reading the conversation's last
message: the two things that arrive immediately after the dealer's reply are
*private notes* (the linked reply, and the draft). Any rule that inferred
"the customer was updated" from recent activity would be satisfied by the
notes that start the clock, and the clock would clear itself the instant it
started. Only agent/ sees the message stream closely enough to tell an
outgoing public reply from a note, so agent/ owns that judgement and this
module just reads its answer.

Pure: no I/O, no store, no clock of its own. `now` and the working-hours
decision are the caller's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.sla_clock import elapsed_minutes

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# The audit transition an overdue customer update records. Named for what is
# owed, not for a breach: the case has been answered, the customer just has
# not been told yet.
CUSTOMER_UPDATE_DUE_STATE = "CUSTOMER_UPDATE_DUE"
CUSTOMER_UPDATE_WARNING_STATE = "CUSTOMER_UPDATE_WARNING"

REPLIED_ATTR = "escalation_replied_at"
UPDATED_ATTR = "customer_updated_at"

_MINUTES_PER_HOUR = 60


@dataclass(frozen=True)
class CustomerUpdateClock:
    """The state of one conversation's customer-update obligation.

    ``started_at is None`` means there is no obligation at all -- no dealer
    has answered, or the feature is off. Every other field is then None/False,
    so a caller can branch on ``started_at`` alone.
    """

    started_at: datetime | None = None
    elapsed_minutes: float | None = None
    remaining_minutes: float | None = None
    breached: bool = False
    warning_due: bool = False
    satisfied: bool = False


_EMPTY = CustomerUpdateClock()


def _parse(value: Any) -> datetime | None:
    """ISO-8601 custom attribute -> aware datetime, or None.

    Garbage in a custom attribute is an operator or integration error, not a
    reason to abort a scan over every conversation: log once at debug and
    treat the attribute as absent, exactly as `_follow_up_at_from_conv` does
    in features/tasks/deadline.py.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _log.debug("customer_update_unparseable_timestamp", value=raw)
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def compute_customer_update_clock(
    conv: dict[str, Any],
    settings: Settings,
    now: datetime,
    *,
    inbox: dict[str, Any] | None = None,
    working_hours: bool = False,
) -> CustomerUpdateClock:
    """The customer-update clock for one Chatwoot conversation.

    Returns the empty clock when the feature is off, when no dealer/PIC has
    replied, or when the conversation is already resolved -- a resolved case
    owes the customer nothing further, and continuing to count would report a
    permanent breach on work that is finished.

    ``satisfied`` (the customer was told, in time or not) is reported rather
    than collapsed into the empty clock, so a caller can tell "nothing owed"
    from "owed and delivered" -- the second is what reporting wants to count.
    """
    if not getattr(settings, "escalation_customer_update_enabled", False):
        return _EMPTY

    attrs = conv.get("custom_attributes") or {}
    if not isinstance(attrs, dict):
        return _EMPTY

    started_at = _parse(attrs.get(REPLIED_ATTR))
    if started_at is None:
        return _EMPTY

    if str(conv.get("status") or "") == "resolved":
        return _EMPTY

    updated_at = _parse(attrs.get(UPDATED_ATTR))
    # A stamp older than the reply belongs to an earlier round of this case:
    # the customer was updated, then a dealer answered again. The obligation
    # is new, so that old stamp does not satisfy it.
    if updated_at is not None and updated_at >= started_at:
        return CustomerUpdateClock(started_at=started_at, satisfied=True)

    threshold = (
        float(getattr(settings, "escalation_customer_update_hours", 4.0)) * _MINUTES_PER_HOUR
    )
    elapsed = elapsed_minutes(
        started_at, now, inbox or {}, working_hours=working_hours
    )
    remaining = threshold - elapsed

    return CustomerUpdateClock(
        started_at=started_at,
        elapsed_minutes=elapsed,
        remaining_minutes=remaining,
        breached=remaining <= 0,
        # Half-time, and only while still inside the window -- past the
        # threshold the breach is the signal and a warning would be noise.
        warning_due=0 < remaining <= threshold / 2,
    )
