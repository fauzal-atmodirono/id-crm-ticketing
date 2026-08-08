"""P2 task 3 — acknowledging the customer in the conversation thread.

On an Email inbox the customer ack is mail. Everywhere else the only way to
reach the customer is the thread they are already in, so the ack is an
*outgoing message* posted to that conversation.

The first test is the one that matters. Commit `0aa643d` in this repo degraded
a customer-facing escalation reply to a private note, and the customer silently
received nothing while the conversation looked handled. Assert the payload, not
merely that a POST happened.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry


class _Sender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append(
            {"to": to, "cc": cc, "subject": subject, "body": body, "reply_to": reply_to}
        )


class _Poster:
    """Stands in for the Chatwoot messages API."""

    def __init__(self, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self._raises = raises

    async def __call__(self, conv_id: str, payload: dict[str, Any]) -> dict | None:
        self.calls.append({"conv_id": conv_id, **payload})
        if self._raises:
            raise RuntimeError("422 Incoming messages are only allowed in Api inboxes")
        return {"id": 1}


class _Settings:
    escalation_email_enabled = True
    email_escalation_ack_enabled = True
    email_escalation_ack_template = "ack body"
    escalation_ack_chat_template = "We have escalated your case and will follow up."
    escalation_cc_pic = True
    escalation_cc_dealer = False
    escalation_reply_to_template = ""
    dealer_email_map_json = ""


class _DealerStore:
    async def get(self, dealer):  # noqa: ARG002
        class _R:
            emails = ["dealer@test"]
            cc_emails: list[str] = []

        return _R()


class _Registry:
    async def lookup(self, dept):
        del dept
        return PicEntry(
            pic_name="Aduy",
            pic_email="pic@test",
            pic_whatsapp="",
            cc_emails=["cc@test"],
        )


async def _noop_cw(conv_id, attrs):  # noqa: ARG001
    return None


def _notifier(sender, poster, settings=None):
    return EscalationNotifier(
        settings or _Settings(),
        _Registry(),
        sender,
        None,
        _noop_cw,
        dealer_store=_DealerStore(),
        chatwoot_post_message=poster,
    )


async def _notify(sender, poster, *, transport, settings=None, dealer=None):
    await _notifier(sender, poster, settings).notify_escalation(
        conv_id="42",
        title="my car will not start",
        body="transcript",
        department="sales",
        dealer=dealer,
        customer_email="customer@test",
        ack_transport=transport,
    )


# --- the invariant --------------------------------------------------------


async def test_the_chat_ack_posts_an_outgoing_message_not_a_private_note():
    sender, poster = _Sender(), _Poster()
    await _notify(sender, poster, transport="conversation")

    assert poster.calls, "no message was posted at all"
    posted = poster.calls[0]
    assert posted["private"] is False
    assert posted["message_type"] == "outgoing"


async def test_the_chat_ack_posts_to_the_right_conversation():
    sender, poster = _Sender(), _Poster()
    await _notify(sender, poster, transport="conversation")
    assert poster.calls[0]["conv_id"] == "42"


async def test_the_chat_ack_never_sets_cc_or_any_email_field():
    sender, poster = _Sender(), _Poster()
    await _notify(sender, poster, transport="conversation")

    posted = poster.calls[0]
    assert "cc" not in posted
    assert "reply_to" not in posted
    # and no mail went to the customer on this transport
    assert all(c["to"] != ["customer@test"] for c in sender.calls)


async def test_the_chat_ack_uses_the_configured_template():
    sender, poster = _Sender(), _Poster()
    await _notify(sender, poster, transport="conversation")
    assert poster.calls[0]["content"] == _Settings.escalation_ack_chat_template


async def test_a_blank_chat_template_sends_no_chat_ack():
    """An operator emptying the template is an opt-out, not an empty message."""
    settings = _Settings()
    settings.escalation_ack_chat_template = ""
    sender, poster = _Sender(), _Poster()
    await _notify(sender, poster, transport="conversation", settings=settings)
    assert not poster.calls


# --- the other transports -------------------------------------------------


async def test_transport_none_sends_no_customer_ack_but_still_sends_pic_and_dealer():
    sender, poster = _Sender(), _Poster()
    await _notify(sender, poster, transport="none", dealer="komang_motor")

    assert not poster.calls
    recipients = [c["to"][0] for c in sender.calls]
    assert "customer@test" not in recipients
    assert "pic@test" in recipients
    assert "dealer@test" in recipients


async def test_transport_email_is_byte_identical_to_the_previous_implementation():
    sender, poster = _Sender(), _Poster()
    await _notify(sender, poster, transport="email")

    assert not poster.calls
    ack = next(c for c in sender.calls if c["to"] == ["customer@test"])
    assert ack["subject"] == "Update on your case: my car will not start"
    assert ack["body"] == "ack body"
    assert ack["cc"] == []


async def test_a_chatwoot_failure_is_logged_and_does_not_abort_the_pic_and_dealer_legs():
    sender, poster = _Sender(), _Poster(raises=True)
    await _notify(sender, poster, transport="conversation", dealer="komang_motor")

    recipients = [c["to"][0] for c in sender.calls]
    assert "pic@test" in recipients
    assert "dealer@test" in recipients


async def test_no_poster_wired_degrades_to_no_chat_ack_without_raising():
    """A tenant whose composition root predates this feature must not 500."""
    sender = _Sender()
    await _notifier(sender, None).notify_escalation(
        conv_id="42",
        title="t",
        body="b",
        department="sales",
        dealer=None,
        customer_email="customer@test",
        ack_transport="conversation",
    )
    assert any(c["to"] == ["pic@test"] for c in sender.calls)
