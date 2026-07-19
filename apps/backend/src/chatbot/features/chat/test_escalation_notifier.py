from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "escalation_email_enabled": True,
        "escalation_cc_pic": True,
        "smtp_host": "smtp.example.com",
        "smtp_from": "noreply@proton.my",
        "twilio_account_sid": "AC1",
        "twilio_auth_token": "tok",
        "twilio_whatsapp_number": "whatsapp:+60100000000",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


_APPS_PIC = PicEntry(
    pic_name="Alice Tan",
    pic_email="alice@proton.my",
    pic_whatsapp="+60123456789",
    zammad_group="Apps-Support",
    chatwoot_team_id=3,
)


def _registry(pic: PicEntry | None = _APPS_PIC) -> PicRegistry:
    if pic is None:
        return PicRegistry({})
    return PicRegistry({"apps": pic})


async def test_notify_sends_email_and_wa_when_dept_matched() -> None:
    sent_emails: list[dict[str, Any]] = []
    sent_wa: list[tuple[str, str]] = []
    cw_calls: list[tuple[str, str, Any]] = []

    class _FakeEmailSender:
        def send(self, to: list[str], cc: list[str], subject: str,
                 body: str, attachments: list) -> None:
            sent_emails.append({"to": to, "cc": cc, "subject": subject, "body": body})

    async def _fake_cw(method: str, path: str, payload: Any = None) -> dict:
        cw_calls.append((method, path, payload))
        return {}

    class _FakeTwilio:
        async def send_message(self, conversation_id: str, text: str) -> None:
            sent_wa.append((conversation_id, text))

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=_FakeTwilio(),  # type: ignore[arg-type]
        chatwoot_request=_fake_cw,
    )

    pic = await notifier.notify(
        conv_id="42",
        ticket_id="ZAM-777",
        title="Battery fault",
        body="Customer has a dead battery on X50.",
        department="apps",
        zammad_ticket_number="12034",
    )

    assert pic is not None
    assert pic.pic_name == "Alice Tan"

    # Email: To = PIC email; body contains title + ticket number
    assert len(sent_emails) == 1
    em = sent_emails[0]
    assert em["to"] == ["alice@proton.my"]
    assert "Battery fault" in em["body"]
    assert "12034" in em["body"]

    # WhatsApp alert sent to PIC
    assert len(sent_wa) == 1
    wa_to, wa_text = sent_wa[0]
    assert "alice" in wa_to.lower() or "+60123456789" in wa_to
    assert "Battery fault" in wa_text or "42" in wa_text

    # Chatwoot custom attribute "case_state" set
    attr_calls = [(m, p, pl) for m, p, pl in cw_calls
                  if "/custom_attributes" in p]
    assert len(attr_calls) >= 1
    attrs = attr_calls[0][2]["custom_attributes"]
    assert "case_state" in attrs


async def test_notify_no_op_when_department_not_in_registry() -> None:
    sent_emails: list[Any] = []

    class _FakeEmailSender:
        def send(self, **_: Any) -> None:
            sent_emails.append(True)

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(None),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=AsyncMock(return_value={}),
    )
    pic = await notifier.notify(
        conv_id="1", ticket_id="Z1", title="t", body="b",
        department="charging", zammad_ticket_number=None,
    )
    assert pic is None
    assert sent_emails == []


async def test_notify_skips_email_when_disabled() -> None:
    sent_emails: list[Any] = []

    class _FakeEmailSender:
        def send(self, **_: Any) -> None:
            sent_emails.append(True)

    notifier = EscalationNotifier(
        settings=_settings(escalation_email_enabled=False),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=AsyncMock(return_value={}),
    )
    await notifier.notify(conv_id="1", ticket_id="Z1", title="t", body="b",
                          department="apps", zammad_ticket_number=None)
    assert sent_emails == []


async def test_notify_skips_wa_when_no_twilio_adapter() -> None:
    sent_emails: list[Any] = []

    class _FakeEmailSender:
        def send(self, to: list[str], **_: Any) -> None:
            sent_emails.append(to)

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,  # no Twilio
        chatwoot_request=AsyncMock(return_value={}),
    )
    pic = await notifier.notify(conv_id="1", ticket_id="Z1", title="t", body="b",
                                department="apps", zammad_ticket_number="1")
    # email still sent; no WA error
    assert pic is not None
    assert len(sent_emails) == 1
