"""P2 tasks 8-9 — record what was delivered, and say so when it wasn't.

Today an escalation that fails to send disappears. The SMTP error is logged and
nothing else happens: the operator sees the `escalate` label sitting on the
case and has no way to know the PIC was never told. §4.39 asks for that signal.

Priority order, stated plainly and asserted: recording the escalation matters
less than making it. An audit-store outage, or a failure to post the warning
note, must never stop a complaint reaching a dealer.

Scope note: this closes the SMTP *send-failure* half of §4.39. Bounce and
invalid-recipient DSN handling needs a bounce mailbox (client question Q6) and
is NOT covered here.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.escalation_notifier import (
    ESCALATION_DELIVERED,
    ESCALATION_FAILED,
    EscalationNotifier,
)
from chatbot.features.chat.pic_registry import PicEntry
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.pic_store import DealerRecord


class _Sender:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.calls: list[dict] = []
        self._fail_for = fail_for or set()

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to})
        if to and to[0] in self._fail_for:
            raise RuntimeError("smtp: connection refused")


class _Poster:
    def __init__(self, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self._raises = raises

    async def __call__(self, conv_id: str, payload: dict[str, Any]):
        self.calls.append({"conv_id": conv_id, **payload})
        if self._raises:
            raise RuntimeError("chatwoot down")
        return {"id": 1}


class _BrokenAudit:
    async def append(self, entry):
        raise RuntimeError("firestore down")

    async def list_for_ticket(self, ticket_id):
        raise RuntimeError("firestore down")


class _Settings:
    escalation_email_enabled = True
    email_escalation_ack_enabled = True
    email_escalation_ack_template = "ack body"
    escalation_ack_chat_template = ""
    escalation_cc_pic = True
    escalation_cc_dealer = False
    escalation_reply_to_template = ""
    dealer_email_map_json = ""
    escalation_attachment_budget_bytes = 0
    escalation_failure_note_enabled = True


class _DealerStore:
    async def get(self, dealer):
        return DealerRecord(dealer=dealer, emails=["dealer@test"])


class _Registry:
    async def lookup(self, dept):
        del dept
        return PicEntry(pic_name="Aduy", pic_email="pic@test", pic_whatsapp="")


async def _noop_cw(conv_id, attrs):
    return None


async def _notify(sender, *, audit=None, poster=None, settings=None, dealer="komang_motor"):
    notifier = EscalationNotifier(
        settings or _Settings(),
        _Registry(),
        sender,
        None,
        _noop_cw,
        dealer_store=_DealerStore(),
        chatwoot_post_message=poster,
        audit=audit,
    )
    await notifier.notify_escalation(
        conv_id="42",
        title="t",
        body="b",
        department="sales",
        dealer=dealer,
        customer_email="customer@test",
        ack_transport="email",
    )


def _states(entries: list[AuditEntry]) -> list[str]:
    return [e.to_state for e in entries]


# --- task 8: the audit trail ----------------------------------------------


async def test_a_successful_pic_send_records_recipients_and_transport():
    audit = InMemoryAuditLog()
    await _notify(_Sender(), audit=audit)

    pic_entry = next(
        e for e in await audit.list_for_ticket("42") if e.recipients == ["pic@test"]
    )
    assert pic_entry.transport == "email"
    assert pic_entry.delivery_status == ESCALATION_DELIVERED


async def test_a_failed_send_records_delivery_status_failed():
    audit = InMemoryAuditLog()
    await _notify(_Sender(fail_for={"pic@test"}), audit=audit)

    pic_entry = next(
        e for e in await audit.list_for_ticket("42") if e.recipients == ["pic@test"]
    )
    assert pic_entry.delivery_status == ESCALATION_FAILED


async def test_all_three_legs_produce_three_distinct_entries():
    audit = InMemoryAuditLog()
    await _notify(_Sender(), audit=audit)

    entries = await audit.list_for_ticket("42")
    recipients = [e.recipients for e in entries]
    assert ["customer@test"] in recipients
    assert ["pic@test"] in recipients
    assert ["dealer@test"] in recipients


async def test_the_customer_ack_entry_does_not_record_the_dealer_recipients():
    audit = InMemoryAuditLog()
    await _notify(_Sender(), audit=audit)

    ack = next(e for e in await audit.list_for_ticket("42") if e.recipients == ["customer@test"])
    assert "dealer@test" not in (ack.recipients or [])


async def test_every_existing_audit_entry_still_deserialises():
    """The four new fields are nullable, so a row written before P2 loads."""
    legacy = AuditEntry(
        ticket_id="1", session_id="s", actor="a", from_state="OPEN",
        to_state="X", at="2026-01-01T00:00:00+00:00", remark="",
    )
    assert legacy.recipients is None
    assert legacy.transport is None
    assert legacy.delivery_status is None
    assert legacy.sla_status is None


async def test_the_audit_write_failing_does_not_abort_the_send():
    """Recording the escalation matters less than making it."""
    sender = _Sender()
    await _notify(sender, audit=_BrokenAudit())

    assert [c["to"][0] for c in sender.calls] == ["customer@test", "pic@test", "dealer@test"]


async def test_no_audit_wired_still_sends():
    sender = _Sender()
    await _notify(sender, audit=None)
    assert sender.calls


# --- task 9: the failure signal -------------------------------------------


async def test_a_send_failure_posts_a_private_note_naming_the_recipient():
    poster = _Poster()
    await _notify(_Sender(fail_for={"pic@test"}), poster=poster)

    notes = [c for c in poster.calls if c.get("private")]
    assert notes, "a failed escalation must leave a visible trace"
    assert "pic@test" in notes[0]["content"]


async def test_a_successful_send_posts_no_note():
    poster = _Poster()
    await _notify(_Sender(), poster=poster)
    assert not [c for c in poster.calls if c.get("private")]


async def test_the_failure_note_is_private_and_never_reaches_the_customer():
    poster = _Poster()
    await _notify(_Sender(fail_for={"dealer@test"}), poster=poster)

    for call in poster.calls:
        if "could not be delivered" in call.get("content", ""):
            assert call["private"] is True


async def test_the_note_write_failing_is_swallowed():
    sender = _Sender(fail_for={"pic@test"})
    await _notify(sender, poster=_Poster(raises=True))
    # the dealer leg still ran despite both the send AND the note failing
    assert "dealer@test" in [c["to"][0] for c in sender.calls]


async def test_the_note_can_be_switched_off():
    settings = _Settings()
    settings.escalation_failure_note_enabled = False
    poster = _Poster()
    await _notify(_Sender(fail_for={"pic@test"}), poster=poster, settings=settings)
    assert not [c for c in poster.calls if c.get("private")]
