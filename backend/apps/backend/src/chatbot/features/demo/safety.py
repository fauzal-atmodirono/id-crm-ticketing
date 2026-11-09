"""What counts as a demo record, and the guard every write goes through.

The demo seeder runs against Proton's LIVE tenant, next to real customers'
contacts and cases. So "is this ours?" is answered by markers on the record
itself, never by an id we remember from an earlier run (after a delete,
Chatwoot can hand that id to someone else's record):

* a contact is ours only if its name starts with exactly ``"[DEMO] "``;
* a conversation is ours only if it carries the ``demo_persona`` label, or
  its contact (``meta.sender``) is a ``[DEMO]`` contact -- the second rule
  covers the calls a rehearsal makes on a demo persona, which the phone code
  creates without our label (deleting the demo contact would take them with
  it anyway);
* an RSA incident is ours only if ``created_by == "demo_persona"`` AND its
  remarks start with ``"[DEMO]"``.

Matching is exact and case-sensitive: ``"DEMO Ahmad"``, ``"Ahmad [DEMO]"``,
``"[demo] Ahmad"`` or a ``demo_persona_old`` label are NOT ours.

Every mutating call in ``crm.DemoCrmClient`` re-reads the record first and
calls one of the ``assert_*`` helpers on that fresh copy, so a stale search
result can never steer a write onto a real record.
"""

from __future__ import annotations

from typing import Any

DEMO_LABEL = "demo_persona"
AUDIT_LABEL = "audit_test"
DEMO_NAME_PREFIX = "[DEMO] "
DEMO_TEXT_PREFIX = "[DEMO]"
DEMO_SOURCE_PREFIX = "demo-"
# Stamped as custom_attributes.demo_seed on every contact/conversation, the
# marker metrics sync already drops with METRICS_EXCLUDE_DEMO_SEED.
DEMO_SEED_MARKER = "demo_persona"
RSA_CREATED_BY = "demo_persona"
# Labels the seeder must never put on a conversation: `escalate` fires the
# EM-7 escalation email (and the reminder ladder) on a live tenant.
FORBIDDEN_SEED_LABELS = frozenset({"escalate"})
# The only account labels the seeder may create (when missing).
CREATABLE_LABELS = frozenset({DEMO_LABEL, AUDIT_LABEL})


class DemoSafetyError(RuntimeError):
    """A write was about to touch a record that is not a demo record."""


def is_demo_contact(contact: Any) -> bool:
    if not isinstance(contact, dict):
        return False
    name = contact.get("name")
    return isinstance(name, str) and name.startswith(DEMO_NAME_PREFIX)


def conversation_labels(conv: Any) -> list[str]:
    if not isinstance(conv, dict):
        return []
    labels = conv.get("labels")
    return [str(x) for x in labels] if isinstance(labels, list) else []


def conversation_sender(conv: Any) -> dict[str, Any] | None:
    if not isinstance(conv, dict):
        return None
    meta = conv.get("meta")
    sender = meta.get("sender") if isinstance(meta, dict) else None
    return sender if isinstance(sender, dict) else None


def is_demo_conversation(conv: Any) -> bool:
    if not isinstance(conv, dict):
        return False
    return DEMO_LABEL in conversation_labels(conv) or is_demo_contact(conversation_sender(conv))


def is_demo_rsa(row: Any) -> bool:
    remarks = getattr(row, "remarks", None)
    return getattr(row, "created_by", None) == RSA_CREATED_BY and (
        isinstance(remarks, str) and remarks.startswith(DEMO_TEXT_PREFIX)
    )


def assert_demo_contact(contact: Any, contact_id: Any) -> None:
    if not is_demo_contact(contact):
        raise DemoSafetyError(f"contact {contact_id} is not a [DEMO] contact; refusing to touch it")


def assert_demo_conversation(conv: Any, conversation_id: Any) -> None:
    if not is_demo_conversation(conv):
        raise DemoSafetyError(
            f"conversation {conversation_id} has no {DEMO_LABEL} label and no [DEMO] contact; "
            "refusing to touch it"
        )


def assert_demo_rsa(row: Any, incident_id: Any) -> None:
    if not is_demo_rsa(row):
        raise DemoSafetyError(
            f"RSA incident {incident_id} is not a demo incident; refusing to touch it"
        )


def assert_demo_text(text: Any) -> None:
    if not isinstance(text, str) or not text.startswith(DEMO_TEXT_PREFIX):
        raise DemoSafetyError("demo message text must start with [DEMO]")
