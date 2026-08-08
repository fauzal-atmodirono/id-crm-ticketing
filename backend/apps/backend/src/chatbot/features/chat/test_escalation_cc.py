"""P2 task 5 — CC on the dealer leg, and the customer-ack privacy invariant.

CC already existed on the PIC leg. The dealer leg had none, so a dealer's
service manager could not be kept in the loop without editing the group.

The invariant matters more than the feature. The customer acknowledgement CCs
NOBODY, under every combination of flags, forever. It is the one message in
this flow that leaves the company, and the PIC/dealer mail it sits beside
carries the full conversation transcript. `test_the_customer_ack_cc_is_empty_
with_every_flag_combination` is a permanent guard, not a one-off check.
"""

from __future__ import annotations

import itertools
from typing import Any

from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry
from chatbot.features.chat.pic_store import DealerRecord, _dealer_record_from_dict

CUSTOMER = "customer@test"


class _Sender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to, "cc": cc, "subject": subject})

    def by_to(self, address: str) -> dict | None:
        return next((c for c in self.calls if c["to"] and c["to"][0] == address), None)


class _Settings:
    escalation_email_enabled = True
    email_escalation_ack_enabled = True
    email_escalation_ack_template = "ack body"
    escalation_ack_chat_template = "chat ack"
    escalation_cc_pic = True
    escalation_cc_dealer = False
    escalation_reply_to_template = ""
    dealer_email_map_json = ""


class _DealerStore:
    def __init__(self, cc: list[str] | None = None) -> None:
        self._cc = cc or []

    async def get(self, dealer):
        return DealerRecord(dealer=dealer, emails=["dealer@test"], cc_emails=self._cc)


class _Registry:
    async def lookup(self, dept):
        del dept
        return PicEntry(
            pic_name="Aduy", pic_email="pic@test", pic_whatsapp="", cc_emails=["cc@test"]
        )


async def _noop_cw(conv_id, attrs):  # noqa: ARG001
    return None


async def _post(conv_id, payload):  # noqa: ARG001
    return None


async def _notify(sender, *, settings, dealer_cc=None, transport="email"):
    notifier = EscalationNotifier(
        settings,
        _Registry(),
        sender,
        None,
        _noop_cw,
        dealer_store=_DealerStore(dealer_cc),
        chatwoot_post_message=_post,
    )
    await notifier.notify_escalation(
        conv_id="42",
        title="t",
        body="transcript",
        department="sales",
        dealer="komang_motor",
        customer_email=CUSTOMER,
        ack_transport=transport,
    )


# --- the store ------------------------------------------------------------


def test_a_legacy_dealer_record_without_cc_emails_still_loads():
    rec = _dealer_record_from_dict({"dealer": "komang", "emails": ["a@t"]}, "komang")
    assert rec.emails == ["a@t"]
    assert rec.cc_emails == []


def test_a_record_with_cc_emails_loads_them():
    rec = _dealer_record_from_dict(
        {"dealer": "komang", "emails": ["a@t"], "cc_emails": ["mgr@t"]}, "komang"
    )
    assert rec.cc_emails == ["mgr@t"]


# --- the dealer leg -------------------------------------------------------


async def test_the_dealer_forward_sends_no_cc_when_the_flag_is_off():
    sender, settings = _Sender(), _Settings()
    settings.escalation_cc_dealer = False
    await _notify(sender, settings=settings, dealer_cc=["mgr@test"])
    assert sender.by_to("dealer@test")["cc"] == []


async def test_the_dealer_forward_sends_cc_when_the_flag_is_on_and_cc_emails_exist():
    sender, settings = _Sender(), _Settings()
    settings.escalation_cc_dealer = True
    await _notify(sender, settings=settings, dealer_cc=["mgr@test"])
    assert sender.by_to("dealer@test")["cc"] == ["mgr@test"]


async def test_the_pic_leg_cc_behaviour_is_unchanged():
    sender, settings = _Sender(), _Settings()
    settings.escalation_cc_dealer = True
    await _notify(sender, settings=settings, dealer_cc=["mgr@test"])
    assert sender.by_to("pic@test")["cc"] == ["cc@test"]


async def test_a_cc_address_equal_to_the_customer_address_is_dropped_from_the_dealer_cc():
    """The dealer forward carries the full transcript. If a dealer's CC list
    happens to hold the customer's own address, that transcript would go
    straight back to them."""
    sender, settings = _Sender(), _Settings()
    settings.escalation_cc_dealer = True
    await _notify(sender, settings=settings, dealer_cc=["mgr@test", CUSTOMER])
    assert sender.by_to("dealer@test")["cc"] == ["mgr@test"]


async def test_the_customer_address_is_matched_case_insensitively():
    sender, settings = _Sender(), _Settings()
    settings.escalation_cc_dealer = True
    await _notify(sender, settings=settings, dealer_cc=["Customer@TEST"])
    assert sender.by_to("dealer@test")["cc"] == []


# --- the invariant --------------------------------------------------------


async def test_the_customer_ack_cc_is_empty_with_every_flag_combination():
    for cc_pic, cc_dealer, ack_on in itertools.product([True, False], repeat=3):
        sender, settings = _Sender(), _Settings()
        settings.escalation_cc_pic = cc_pic
        settings.escalation_cc_dealer = cc_dealer
        settings.email_escalation_ack_enabled = ack_on
        await _notify(sender, settings=settings, dealer_cc=["mgr@test"])

        ack = sender.by_to(CUSTOMER)
        if ack is None:
            continue  # ack disabled: nothing to leak
        assert ack["cc"] == [], (
            f"customer ack CC'd somebody with cc_pic={cc_pic} "
            f"cc_dealer={cc_dealer} ack={ack_on}"
        )
