"""What this seeder owns, and the rules every write is checked against.

The demo seeder runs against Proton's LIVE tenant, next to real customers
AND next to other "[DEMO]" data it does not own: contacts made by
``deploy/scripts/seed_demo_data`` ("[DEMO] <first> <last>", ``demo_seed=
<batch>``), real handsets renamed by ``agent/app/services/demo_persona.py``,
contacts someone named "[DEMO] ..." by hand. A "[DEMO]" name alone therefore
proves nothing. Ownership is decided by markers this seeder stamps, checked
on the record itself (never an id remembered from an earlier run -- after a
delete Chatwoot can hand that id to someone else):

* a **contact** is ours only if ALL hold: its name starts with exactly
  ``"[DEMO] "``, ``custom_attributes.demo_seed == "demo_persona"`` (stamped
  at create), and its phone number is one of the seeded personas' numbers;
* a **conversation** is ours only if it has the ``demo_persona`` label AND
  ``custom_attributes.demo_seed == "demo_persona"`` (both stamped at
  create), OR it is on the demo inbox AND its contact passes the contact
  rule -- the second case covers the calls a rehearsal makes on a persona,
  which the phone code creates without our markers. A persona contact's
  conversation on any OTHER inbox (WhatsApp, email) is never ours;
* an **RSA incident** is ours only if ``created_by == "demo_persona"`` AND
  its remarks start with ``"[DEMO]"`` (the seeder additionally requires the
  persona's phone, or its plate when the row has no phone).

Matching is exact and case-sensitive. Every mutating call in
``crm.DemoCrmClient`` re-reads the target first and applies these rules to
the fresh copy, so a stale search result can never steer a write.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from chatbot.features.chat.customer360_router import _phones_match

DEMO_LABEL = "demo_persona"
AUDIT_LABEL = "audit_test"
DEMO_NAME_PREFIX = "[DEMO] "
DEMO_TEXT_PREFIX = "[DEMO]"
DEMO_SOURCE_PREFIX = "demo-"
# custom_attributes.demo_seed on every contact/conversation this seeder
# creates. The BigQuery metrics sync drops this marker when
# METRICS_EXCLUDE_DEMO_SEED=true -- that is the ONLY report it affects:
# native Chatwoot reports count the seeded cases until Reset removes them.
DEMO_SEED_MARKER = "demo_persona"
RSA_CREATED_BY = "demo_persona"
# Labels the seeder never applies on a live tenant: `escalate` fires the
# EM-7 escalation email + reminder ladder; a `dealer_<slug>` label drives
# dealer routing/escalation (ruling R29: the dealer is seeded as
# custom_attributes.caller_dealer instead, which the caller lookup reads).
FORBIDDEN_SEED_LABELS = frozenset({"escalate"})
FORBIDDEN_LABEL_PREFIXES = ("dealer_",)
# The only account labels the seeder may create (when missing).
CREATABLE_LABELS = frozenset({DEMO_LABEL, AUDIT_LABEL})


class DemoSafetyError(RuntimeError):
    """A write was about to touch a record this seeder does not own."""


def _attrs(record: Any) -> dict[str, Any]:
    attrs = record.get("custom_attributes") if isinstance(record, dict) else None
    return attrs if isinstance(attrs, dict) else {}


def has_demo_name(contact: Any) -> bool:
    name = contact.get("name") if isinstance(contact, dict) else None
    return isinstance(name, str) and name.startswith(DEMO_NAME_PREFIX)


def is_seeder_contact(contact: Any, numbers: Iterable[str]) -> bool:
    if not isinstance(contact, dict) or not has_demo_name(contact):
        return False
    if _attrs(contact).get("demo_seed") != DEMO_SEED_MARKER:
        return False
    phone = contact.get("phone_number")
    return any(_phones_match(n, phone) for n in numbers)


def conversation_labels(conv: Any) -> list[str]:
    labels = conv.get("labels") if isinstance(conv, dict) else None
    return [str(x) for x in labels] if isinstance(labels, list) else []


def conversation_sender(conv: Any) -> dict[str, Any] | None:
    meta = conv.get("meta") if isinstance(conv, dict) else None
    sender = meta.get("sender") if isinstance(meta, dict) else None
    return sender if isinstance(sender, dict) else None


def has_seeder_markers(conv: Any) -> bool:
    return (
        DEMO_LABEL in conversation_labels(conv)
        and _attrs(conv).get("demo_seed") == DEMO_SEED_MARKER
    )


def is_seeder_conversation(
    conv: Any,
    *,
    inbox_id: int,
    numbers: Iterable[str],
    sender: dict[str, Any] | None = None,
) -> bool:
    """``sender`` (a FRESH read of the conversation's contact) replaces the
    ``meta.sender`` copy embedded in the conversation when given."""
    if not isinstance(conv, dict):
        return False
    if has_seeder_markers(conv):
        return True
    if not inbox_id or conv.get("inbox_id") != inbox_id:
        return False
    return is_seeder_contact(sender if sender is not None else conversation_sender(conv), numbers)


def is_demo_rsa(row: Any) -> bool:
    remarks = getattr(row, "remarks", None)
    return getattr(row, "created_by", None) == RSA_CREATED_BY and (
        isinstance(remarks, str) and remarks.startswith(DEMO_TEXT_PREFIX)
    )


def forbidden_labels(labels: Iterable[str]) -> list[str]:
    return sorted(
        x for x in labels if x in FORBIDDEN_SEED_LABELS or x.startswith(FORBIDDEN_LABEL_PREFIXES)
    )


def assert_demo_rsa(row: Any, incident_id: Any) -> None:
    if not is_demo_rsa(row):
        raise DemoSafetyError(
            f"RSA incident {incident_id} is not a demo incident; refusing to touch it"
        )


def assert_demo_text(text: Any) -> None:
    if not isinstance(text, str) or not text.startswith(DEMO_TEXT_PREFIX):
        raise DemoSafetyError("demo message text must start with [DEMO]")
