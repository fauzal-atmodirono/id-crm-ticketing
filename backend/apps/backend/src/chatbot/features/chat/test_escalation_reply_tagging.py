"""Correlation tagging on escalation mail (Reply-To + [CASE-n] subject).

The reply loop's outbound half: a Reply-To header lets a dealer/PIC's raw
email reply route back through SMTP to this tenant's inbox, and a
[CASE-<id>] subject tag lets a human eyeball which conversation a reply
belongs to. The customer thread deliberately never gets the visible tag --
only the invisible Reply-To -- so a customer-facing ack email reads clean.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "escalation_email_enabled": True,
        "escalation_cc_pic": True,
        "email_escalation_ack_enabled": True,
        "email_escalation_ack_template": "ack body",
        "smtp_host": "smtp.example.com",
        "smtp_from": "noreply@proton.my",
        "dealer_email_map_json": "",
        "escalation_reply_to_template": "support+case{conv_id}@test",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


class _Sender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None) -> None:
        self.calls.append({"to": to, "subject": subject, "reply_to": reply_to})


class _DealerStore:
    async def get(self, dealer: str):
        class _R:
            email = "dealer@test"

        return _R()


_PIC = PicEntry(pic_name="Aduy", pic_email="pic@test", pic_whatsapp="", cc_emails=["cc@test"])


def _registry() -> PicRegistry:
    return PicRegistry({"sales": _PIC})


async def _noop_cw(conv_id: str, attrs: dict[str, Any]) -> None:
    return None


def _notifier(sender: _Sender, settings: Settings) -> EscalationNotifier:
    return EscalationNotifier(
        settings,
        _registry(),
        sender,
        None,
        _noop_cw,
        dealer_store=_DealerStore(),
    )


async def test_pic_and_dealer_mail_carry_token_customer_ack_does_not() -> None:
    sender = _Sender()
    await _notifier(sender, _settings()).notify_email_channel_escalation(
        conv_id="42",
        title="my car will not start",
        body="transcript",
        department="sales",
        dealer="komang_motor",
        customer_email="customer@test",
    )

    by_to = {c["to"][0]: c for c in sender.calls}

    assert by_to["customer@test"]["reply_to"] == "support+case42@test"
    assert "[CASE-42]" not in by_to["customer@test"]["subject"]

    assert by_to["pic@test"]["reply_to"] == "support+case42@test"
    assert by_to["pic@test"]["subject"].startswith("[Escalation] [CASE-42]")

    assert by_to["dealer@test"]["reply_to"] == "support+case42@test"
    assert "[CASE-42]" in by_to["dealer@test"]["subject"]


async def test_empty_template_leaves_mail_untagged() -> None:
    sender = _Sender()
    settings = _settings(escalation_reply_to_template="")
    notifier = _notifier(sender, settings)

    await notifier.notify_email_channel_escalation(
        conv_id="42",
        title="t",
        body="b",
        department="sales",
        dealer=None,
        customer_email="customer@test",
    )

    assert all(c["reply_to"] is None for c in sender.calls)
    assert all("[CASE-" not in c["subject"] for c in sender.calls)
