"""Pure deadline computation for the My-Tasks agent view.

No I/O. Takes a raw Chatwoot conversation dict (same shape returned by
``fetch_conversations``), the current ``Settings``, and a ``datetime`` clock,
and returns a ``TaskItem`` with all deadline and remaining-time fields populated.

Deadline sources (mirrors the SLA engine's logic in ``features/chat/sla.py``):
- Response threshold: always ``settings.sla_response_hours * 3600`` seconds from
  ``created_at``. Cleared once ``first_reply_created_at`` is set on the conv.
- Resolution threshold: ``sla_<int>`` label minutes OR
  ``custom_attributes.sla_minutes`` OR ``settings.sla_resolution_hours * 3600``.
- A RESOLVED conversation has no active deadlines.

P6 task 10 adds a per-ticket follow-up REMINDER DATE
(``custom_attributes.follow_up_at``, gated on ``settings.follow_up_date_enabled``).
It is an agent's own note ("look at this again Thursday"), never a policy
commitment, so it is deliberately surfaced on its own ``follow_up_at_iso`` /
``follow_up_remaining_seconds`` fields rather than folded into
``resolution_deadline_iso``/``breach_type``: a follow-up date going overdue
must never read as an SLA breach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_SLA_LABEL = re.compile(r"^sla_(\d+)$")
_SECONDS_PER_HOUR = 3600


@dataclass
class TaskItem:
    conv_id: str
    subject: str
    status: str
    agent_id: str | None
    created_at_iso: str | None
    response_deadline_iso: str | None
    resolution_deadline_iso: str | None
    response_remaining_seconds: float | None
    resolution_remaining_seconds: float | None
    breach_type: str | None  # "NO_RESPONSE" | "UNRESOLVED" | None
    # P6 task 10: an agent-set follow-up reminder date, entirely separate
    # from the SLA fields above. `follow_up_remaining_seconds` going negative
    # means the reminder is overdue -- it never sets breach_type.
    follow_up_at_iso: str | None
    follow_up_remaining_seconds: float | None


def _labels(conv: dict[str, Any]) -> list[str]:
    raw = conv.get("labels")
    return [str(t) for t in raw] if isinstance(raw, list) else []


def _sla_minutes_from_conv(conv: dict[str, Any], labels: list[str]) -> int | None:
    """Read per-conv SLA override: label first, then custom_attributes fallback."""
    for tag in labels:
        m = _SLA_LABEL.match(tag)
        if m:
            return int(m.group(1))
    ca = conv.get("custom_attributes")
    if isinstance(ca, dict) and ca.get("sla_minutes") is not None:
        try:
            return int(ca["sla_minutes"])
        except (ValueError, TypeError):
            return None
    return None


def _epoch_to_dt(value: Any) -> datetime | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None


def _agent_id_from(conv: dict[str, Any]) -> str | None:
    meta = conv.get("meta")
    if not isinstance(meta, dict):
        return None
    assignee = meta.get("assignee")
    if isinstance(assignee, dict) and assignee.get("id") is not None:
        return str(assignee["id"])
    return None


def _follow_up_at_from_conv(conv: dict[str, Any], settings: Settings) -> datetime | None:
    """Read the operator-set follow-up reminder date, if any.

    Gated on ``settings.follow_up_date_enabled`` so the flag being off means
    this always returns ``None`` -- today's ``TaskItem`` shape, byte-identical
    -- even if a stray value already sits in Chatwoot's custom_attributes.

    Deliberately separate from ``_sla_minutes_from_conv``: reads a different
    key, never falls back to it, and never feeds the SLA fields. An empty
    string or a missing key both mean "no reminder", matching the write-side
    behaviour in ``agent/app/services/sync.py::maybe_validate_follow_up_date``.
    An unparseable value is treated the same as absent -- the write boundary
    already rejects those before they can reach here, so a value we do see
    here has already passed validation, but a foreign write is still handled
    gracefully rather than raising.
    """
    if not settings.follow_up_date_enabled:
        return None
    ca = conv.get("custom_attributes")
    if not isinstance(ca, dict):
        return None
    raw = ca.get("follow_up_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _subject_from(conv: dict[str, Any]) -> str:
    meta = conv.get("meta")
    if isinstance(meta, dict):
        sender = meta.get("sender")
        if isinstance(sender, dict) and sender.get("name"):
            return str(sender["name"])
    return f"Conversation #{conv.get('id', '?')}"


def compute_deadlines(
    conv: dict[str, Any],
    settings: Settings,
    now: datetime,
) -> TaskItem:
    """Compute SLA deadlines and remaining time for one Chatwoot conversation."""
    conv_id = str(conv.get("id", ""))
    status = str(conv.get("status") or "")
    labels = _labels(conv)

    created_dt = _epoch_to_dt(conv.get("created_at"))
    created_at_iso = created_dt.isoformat() if created_dt else None

    # A resolved conversation has no active deadlines.
    if status == "resolved" or created_dt is None:
        return TaskItem(
            conv_id=conv_id,
            subject=_subject_from(conv),
            status=status,
            agent_id=_agent_id_from(conv),
            created_at_iso=created_at_iso,
            response_deadline_iso=None,
            resolution_deadline_iso=None,
            response_remaining_seconds=None,
            resolution_remaining_seconds=None,
            breach_type=None,
            follow_up_at_iso=None,
            follow_up_remaining_seconds=None,
        )

    # --- Response deadline ---
    response_threshold_sec = settings.sla_response_hours * _SECONDS_PER_HOUR
    response_deadline = created_dt + timedelta(seconds=response_threshold_sec)
    response_remaining = (response_deadline - now).total_seconds()
    has_first_reply = bool(conv.get("first_reply_created_at"))

    # --- Resolution deadline ---
    sla_minutes = _sla_minutes_from_conv(conv, labels)
    if sla_minutes is not None:
        resolution_threshold_sec = sla_minutes * 60
    else:
        resolution_threshold_sec = settings.sla_resolution_hours * _SECONDS_PER_HOUR
    resolution_deadline = created_dt + timedelta(seconds=resolution_threshold_sec)
    resolution_remaining = (resolution_deadline - now).total_seconds()

    # --- Breach classification (worst-case active breach) ---
    breach_type: str | None = None
    if resolution_remaining < 0:
        breach_type = "UNRESOLVED"
    elif not has_first_reply and response_remaining < 0:
        breach_type = "NO_RESPONSE"

    # --- Follow-up reminder (P6 task 10) --- computed independently of the
    # SLA fields above and never allowed to influence breach_type: an
    # overdue follow-up is a fired reminder, not a breach.
    follow_up_dt = _follow_up_at_from_conv(conv, settings)
    follow_up_at_iso = follow_up_dt.isoformat() if follow_up_dt else None
    follow_up_remaining = (follow_up_dt - now).total_seconds() if follow_up_dt else None

    return TaskItem(
        conv_id=conv_id,
        subject=_subject_from(conv),
        status=status,
        agent_id=_agent_id_from(conv),
        created_at_iso=created_at_iso,
        response_deadline_iso=response_deadline.isoformat() if not has_first_reply else None,
        resolution_deadline_iso=resolution_deadline.isoformat(),
        response_remaining_seconds=response_remaining if not has_first_reply else None,
        resolution_remaining_seconds=resolution_remaining,
        breach_type=breach_type,
        follow_up_at_iso=follow_up_at_iso,
        follow_up_remaining_seconds=follow_up_remaining,
    )
