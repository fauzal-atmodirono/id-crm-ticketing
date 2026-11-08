"""Audit events beyond case-state changes (FIX-17, RFP 3.2.6 / 4.38 / 2.2.5).

One place that turns "someone changed X" into an append-only ``AuditEntry``:

* **Who** -- ``actor_user_id`` / ``actor_name`` are the real Chatwoot user for
  admin writes (resolved by ``resolve_actor``), a service name only
  for system actors (SLA engine, ladder sweep, escalation notifier).
* **What** -- ``event_type`` + ``target`` + redacted ``before`` / ``after``.
* **Never raises.** Recording an event matters less than the action itself; a
  dead audit store must not turn an admin save or an escalation into a 500.

Redaction is applied here, once, so no call site can forget it: secret-looking
keys are replaced, e-mail addresses keep only the first character and the
domain, phone-like numbers keep only the last three digits.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.ports import AuditEntry

if TYPE_CHECKING:
    from chatbot.features.chat.ports import AuditLogPort

_log = structlog.get_logger(__name__)

# event_type vocabulary (filterable on GET /admin/audit?event_type=).
EVENT_STATUS_CHANGE = "status_change"
EVENT_ESCALATION_NOTIFY = "escalation_notify"
EVENT_LADDER_STEP = "ladder_step"
EVENT_REPLY_LINK = "reply_link"
EVENT_SLA_ALERT = "sla_alert"
EVENT_ROLE_CHANGE = "role_change"
EVENT_SLA_POLICY_CHANGE = "sla_policy_change"
EVENT_ESCALATION_ROUTING_CHANGE = "escalation_routing_change"
EVENT_LADDER_POLICY_CHANGE = "ladder_policy_change"
EVENT_TAXONOMY_CHANGE = "taxonomy_change"

SYSTEM_ACTOR_USER_ID = None

_SECRET_KEY = re.compile(r"(password|passwd|secret|token|api[_-]?key|credential|authorization)", re.I)
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9-]+)")
# Phone-like structure only: a leading "+" (country code) or "0" (national
# trunk prefix), then digits with optional space/dash/dot/paren separators, 9-15
# digits in all. Free text is full of other digit runs -- ISO timestamps, case
# and ticket numbers, ids -- which must survive untouched, so a bare run of
# digits is NOT treated as a phone. The look-arounds stop a match starting
# inside a longer token ("2026-11-09", "ab0123456789").
_PHONE = re.compile(r"(?<![\w:+-])(?:\+\d|0)[\d\s().-]{7,19}\d(?![\w:-])")
_DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}")
# Keys whose VALUE is a phone number whatever its shape (stored numbers are not
# always written with a leading + or 0).
_PHONE_KEY = re.compile(r"(whatsapp|phone|mobile|msisdn|tel)", re.I)
_MIN_PHONE_DIGITS, _MAX_PHONE_DIGITS = 9, 15
_REDACTED = "[redacted]"


def _mask_digits(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) <= 3:  # noqa: PLR2004 -- nothing worth keeping
        return "*" * len(digits)
    return f"{'*' * (len(digits) - 3)}{digits[-3:]}"


def _mask_phone(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS or _DATE_SHAPE.search(raw):
        return raw
    return _mask_digits(raw)


def mask_recipient(value: str) -> str:
    """Recipient as stored in the trail.

    E-mail addresses are kept in full: RFP 3.2.6 asks the trail to name the
    recipient, and an address is how a delivery is checked. A phone-shaped
    recipient (phone-task contacts) is a different matter -- nothing about
    auditing a call needs the whole number -- so it is masked to the last 3
    digits. Decision recorded here so it is not rediscovered as a bug.
    """
    if "@" in value:
        return value
    return _mask_digits(value) if re.search(r"\d{6,}", value) else value


def mask_text(value: str) -> str:
    """Mask e-mail addresses and phone-like numbers inside a free-text string."""
    value = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value)
    return _PHONE.sub(_mask_phone, value)


def redact(value: Any) -> Any:
    """Deep-copy ``value`` with secrets replaced and personal contact data masked."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _SECRET_KEY.search(str(k)) and v:
                out[str(k)] = _REDACTED
            elif _PHONE_KEY.search(str(k)) and isinstance(v, str):
                out[str(k)] = _mask_digits(v)
            else:
                out[str(k)] = redact(v)
        return out
    if isinstance(value, (list, tuple, set)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


def _snapshot(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {"value": value}
    return redact(value)


def changed_fields(before: dict[str, Any] | None, after: dict[str, Any] | None):
    """Keep only keys whose value differs, so the trail shows the edit, not the row."""
    b, a = dict(before or {}), dict(after or {})
    keys = [k for k in {**b, **a} if b.get(k) != a.get(k)]
    return {k: b[k] for k in keys if k in b}, {k: a[k] for k in keys if k in a}


async def record_event(
    audit: AuditLogPort | None,
    *,
    event_type: str,
    target: str,
    actor_user_id: int | None = None,
    actor_name: str | None = None,
    before: Any = None,
    after: Any = None,
    remarks: str = "",
    sla_status: str | None = None,
    ticket_id: str | None = None,
    to_state: str | None = None,
    from_state: str = "ADMIN",
    recipients: list[str] | None = None,
    transport: str | None = None,
    delivery_status: str | None = None,
    at: datetime | None = None,
    actor_source: str | None = None,
) -> bool:
    """Append one event. Returns whether it was stored; never raises."""
    if audit is None:
        return False
    name = actor_name or (f"user:{actor_user_id}" if actor_user_id is not None else "system")
    try:
        await audit.append(
            AuditEntry(
                ticket_id=ticket_id or f"admin:{event_type}",
                session_id=f"chatwoot-conv-{ticket_id}" if ticket_id else f"audit-{event_type}",
                actor=name,
                from_state=from_state,
                to_state=to_state or event_type.upper(),
                at=(at or datetime.now(UTC)).isoformat(),
                remark=mask_text(remarks),
                recipients=recipients,
                transport=transport,
                delivery_status=delivery_status,
                sla_status=sla_status,
                event_type=event_type,
                actor_user_id=actor_user_id,
                actor_name=name,
                target=target,
                actor_source=actor_source,
                before=_snapshot(before),
                after=_snapshot(after),
            )
        )
    except Exception as exc:
        _log.warning("audit_event_write_failed", event_type=event_type, error=str(exc))
        return False
    return True
