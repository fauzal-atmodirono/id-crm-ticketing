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
domain, phone-like numbers keep only the last three digits. The rules live
in ``features/privacy/pii.py`` (shared with the Task 29 PII masking gate).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.ports import AuditEntry
from chatbot.features.privacy.pii import (  # noqa: F401 -- mask_recipient re-exported
    mask_recipient,
    mask_text,
    redact,
)

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
# Call-recording player (phone/recording_router.py): who listened to / read /
# re-transcribed which conversation's recording. target="conversation:<id>".
EVENT_CALL_RECORDING_PLAY = "call_recording_play"
EVENT_CALL_RECORDING_TRANSCRIPT_VIEW = "call_recording_transcript_view"
EVENT_CALL_RECORDING_RETRANSCRIBE = "call_recording_retranscribe"

SYSTEM_ACTOR_USER_ID = None

# The redaction rules live in features/privacy/pii.py (Wave 2 Task 29), shared
# with the display-masking gate; re-exported here so existing imports hold.


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
