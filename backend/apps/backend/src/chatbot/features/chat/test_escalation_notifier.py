from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from chatbot.features.chat.escalation_notifier import EscalationNotifier, build_dealer_email_map
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
from chatbot.features.chat.pic_store import DealerRecord
from chatbot.platform.config import Settings


def test_build_dealer_email_map_parses_json() -> None:
    settings = _settings(
        dealer_email_map_json='{"kl_pj": "kl-pj@dealer.example", "JB": "jb@dealer.example"}'
    )
    result = build_dealer_email_map(settings)
    assert result == {"kl_pj": "kl-pj@dealer.example", "jb": "jb@dealer.example"}


def test_build_dealer_email_map_empty_on_blank_or_bad_json() -> None:
    assert build_dealer_email_map(_settings(dealer_email_map_json="")) == {}
    assert build_dealer_email_map(_settings(dealer_email_map_json="not json")) == {}
    assert build_dealer_email_map(_settings(dealer_email_map_json='["a", "b"]')) == {}


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
        def send(
            self, to: list[str], cc: list[str], subject: str, body: str, attachments: list
        ) -> None:
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
        title="Battery fault",
        body="Customer has a dead battery on X50.",
        department="apps",
    )

    assert pic is not None
    assert pic.pic_name == "Alice Tan"

    # Email: To = PIC email; body contains title + Chatwoot conversation reference
    assert len(sent_emails) == 1
    em = sent_emails[0]
    assert em["to"] == ["alice@proton.my"]
    assert "Battery fault" in em["body"]
    assert "Chatwoot conversation #42" in em["body"]

    # WhatsApp alert sent to PIC
    assert len(sent_wa) == 1
    wa_to, wa_text = sent_wa[0]
    assert "alice" in wa_to.lower() or "+60123456789" in wa_to
    assert "Battery fault" in wa_text or "42" in wa_text

    # Chatwoot custom attribute "case_state" set
    attr_calls = [(m, p, pl) for m, p, pl in cw_calls if "/custom_attributes" in p]
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
        conv_id="1",
        title="t",
        body="b",
        department="charging",
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
    await notifier.notify(conv_id="1", title="t", body="b", department="apps")
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
    pic = await notifier.notify(conv_id="1", title="t", body="b", department="apps")
    # email still sent; no WA error
    assert pic is not None
    assert len(sent_emails) == 1


# ---------------------------------------------------------------------------
# Fix 4 — fail-open safety contract: notify() must NEVER raise
# ---------------------------------------------------------------------------


async def test_notify_does_not_raise_when_email_sender_raises() -> None:
    """If email_sender.send raises, notify() swallows it and returns normally.

    The WA send and case_state write must still be attempted.
    """
    wa_calls: list[str] = []
    cw_calls: list[str] = []

    class _ExplodingEmailSender:
        def send(self, **_: Any) -> None:
            raise RuntimeError("SMTP server down")

    class _FakeTwilio:
        async def send_message(self, conversation_id: str, text: str) -> None:
            wa_calls.append(conversation_id)  # text not inspected in this test

    async def _fake_cw(_method: str, path: str, _payload: Any = None) -> dict:
        cw_calls.append(path)
        return {}

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_ExplodingEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=_FakeTwilio(),  # type: ignore[arg-type]
        chatwoot_request=_fake_cw,
    )

    # Must not raise
    pic = await notifier.notify(
        conv_id="99",
        title="Crash test",
        body="email will explode",
        department="apps",
    )

    # PIC was resolved
    assert pic is not None
    # WA was still attempted
    assert len(wa_calls) == 1
    # case_state write was still attempted
    assert any("/custom_attributes" in p for p in cw_calls)


async def test_notify_does_not_raise_when_chatwoot_request_raises() -> None:
    """If the Chatwoot case_state write raises, notify() swallows it and returns normally."""
    sent_emails: list[str] = []

    class _FakeEmailSender:
        def send(self, to: list[str], **_: Any) -> None:
            sent_emails.append(to[0])

    async def _exploding_cw(_method: str, _path: str, _payload: Any = None) -> dict:
        raise RuntimeError("Chatwoot unreachable")

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=_exploding_cw,
    )

    # Must not raise
    pic = await notifier.notify(
        conv_id="88",
        title="CW crash test",
        body="chatwoot will explode",
        department="apps",
    )

    # Returns normally; PIC resolved; email attempted
    assert pic is not None
    assert len(sent_emails) == 1


# ---------------------------------------------------------------------------
# Per-department CC (relevant personnel) + Chatwoot-first email reference
# ---------------------------------------------------------------------------

_APPS_PIC_WITH_CC = PicEntry(
    pic_name="Alice Tan",
    pic_email="alice@proton.my",
    pic_whatsapp="+60123456789",
    chatwoot_team_id=3,
    cc_emails=["manager@proton.my", "team-dl@proton.my"],
)


async def test_notify_ccs_pic_cc_emails_when_enabled() -> None:
    sent: list[dict[str, Any]] = []

    class _FakeEmailSender:
        def send(
            self, to: list[str], cc: list[str], subject: str, body: str, attachments: list
        ) -> None:
            sent.append({"to": to, "cc": cc})

    notifier = EscalationNotifier(
        settings=_settings(escalation_cc_pic=True),
        pic_registry=PicRegistry({"apps": _APPS_PIC_WITH_CC}),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=AsyncMock(return_value={}),
    )
    await notifier.notify(conv_id="1", title="t", body="b", department="apps")

    assert len(sent) == 1
    assert sent[0]["to"] == ["alice@proton.my"]
    assert sent[0]["cc"] == ["manager@proton.my", "team-dl@proton.my"]


async def test_notify_omits_cc_when_escalation_cc_pic_disabled() -> None:
    sent: list[dict[str, Any]] = []

    class _FakeEmailSender:
        def send(
            self, to: list[str], cc: list[str], subject: str, body: str, attachments: list
        ) -> None:
            sent.append({"cc": cc})

    notifier = EscalationNotifier(
        settings=_settings(escalation_cc_pic=False),
        pic_registry=PicRegistry({"apps": _APPS_PIC_WITH_CC}),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=AsyncMock(return_value={}),
    )
    await notifier.notify(conv_id="1", title="t", body="b", department="apps")

    assert len(sent) == 1
    assert sent[0]["cc"] == []


async def test_email_reference_uses_chatwoot_conversation() -> None:
    """Chatwoot-only deploy: the email always references the Chatwoot
    conversation."""
    bodies: list[str] = []

    class _FakeEmailSender:
        def send(
            self, to: list[str], cc: list[str], subject: str, body: str, attachments: list
        ) -> None:
            bodies.append(body)

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=AsyncMock(return_value={}),
    )
    await notifier.notify(conv_id="42", title="t", body="b", department="apps")

    assert len(bodies) == 1
    assert "Chatwoot conversation #42" in bodies[0]


# ---------------------------------------------------------------------------
# EM-7: notify_email_channel_escalation (two-thread email escalation)
# ---------------------------------------------------------------------------


def _notifier(
    *,
    pic: PicEntry | None = _APPS_PIC,
    dealer_map: dict[str, str] | None = None,
    email_sender=None,
    settings_kw: dict[str, Any] | None = None,
) -> tuple[EscalationNotifier, list[dict[str, Any]]]:
    sent_emails: list[dict[str, Any]] = []

    class _FakeEmailSender:
        def send(self, to, cc, subject, body, attachments) -> None:
            sent_emails.append({"to": to, "cc": cc, "subject": subject, "body": body})

    async def _fake_cw(method: str, path: str, payload: Any = None) -> dict:
        return {}

    notifier = EscalationNotifier(
        settings=_settings(**(settings_kw or {})),
        pic_registry=_registry(pic),
        email_sender=email_sender or _FakeEmailSender(),
        twilio_adapter=None,
        chatwoot_request=_fake_cw,
        dealer_email_map=dealer_map or {},
    )
    return notifier, sent_emails


async def test_notify_email_channel_escalation_sends_customer_ack_when_enabled() -> None:
    notifier, sent = _notifier(
        pic=None, settings_kw={"email_escalation_ack_enabled": True}
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="Late delivery", body="details",
        department=None, dealer=None, customer_email="alex@customer.example",
    )
    assert len(sent) == 1
    assert sent[0]["to"] == ["alex@customer.example"]
    assert "specialist team" in sent[0]["body"]


async def test_notify_email_channel_escalation_skips_ack_when_disabled() -> None:
    notifier, sent = _notifier(
        pic=None, settings_kw={"email_escalation_ack_enabled": False}
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer=None, customer_email="alex@customer.example",
    )
    assert sent == []


async def test_notify_email_channel_escalation_sends_dealer_forward_when_mapped() -> None:
    notifier, sent = _notifier(
        pic=None, dealer_map={"kl_pj": "kl-pj@dealer.example"},
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer="kl_pj", customer_email=None,
    )
    assert len(sent) == 1
    assert sent[0]["to"] == ["kl-pj@dealer.example"]


async def test_notify_email_channel_escalation_skips_dealer_when_unmapped() -> None:
    notifier, sent = _notifier(pic=None, dealer_map={})
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer="unknown_slug", customer_email=None,
    )
    assert sent == []


async def test_notify_email_channel_escalation_sends_pic_and_dealer_together() -> None:
    notifier, sent = _notifier(
        pic=_APPS_PIC,
        dealer_map={"kl_pj": "kl-pj@dealer.example"},
        settings_kw={"escalation_email_enabled": True},
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department="dept_apps", dealer="kl_pj", customer_email=None,
    )
    recipients = {tuple(e["to"]) for e in sent}
    assert ("alice@proton.my",) in recipients
    assert ("kl-pj@dealer.example",) in recipients


async def test_notify_email_channel_escalation_noop_when_everything_off() -> None:
    notifier, sent = _notifier(
        pic=None,
        dealer_map={},
        settings_kw={"escalation_email_enabled": False, "email_escalation_ack_enabled": False},
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer=None, customer_email="alex@customer.example",
    )
    assert sent == []


# ---------------------------------------------------------------------------
# Task 2: dealer resolution is store-first, dict fallback
# ---------------------------------------------------------------------------


async def test_dealer_forward_uses_store_record_when_present() -> None:
    """DealerStore.get() wins over the legacy dealer_email_map dict."""
    dealer_store = AsyncMock()
    dealer_store.get.return_value = DealerRecord(dealer="kl_pj", email="store@dealer.example")

    notifier, sent = _notifier(pic=None, dealer_map={"kl_pj": "legacy@dealer.example"})
    notifier._dealer_store = dealer_store

    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer="kl_pj", customer_email=None,
    )

    assert len(sent) == 1
    assert sent[0]["to"] == ["store@dealer.example"]
    dealer_store.get.assert_awaited_once_with("kl_pj")


async def test_dealer_forward_falls_back_to_dict_when_store_has_no_entry() -> None:
    dealer_store = AsyncMock()
    dealer_store.get.return_value = None

    notifier, sent = _notifier(pic=None, dealer_map={"kl_pj": "legacy@dealer.example"})
    notifier._dealer_store = dealer_store

    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer="kl_pj", customer_email=None,
    )

    assert len(sent) == 1
    assert sent[0]["to"] == ["legacy@dealer.example"]


async def test_dealer_forward_works_without_a_store_configured() -> None:
    """dealer_store=None (default) -> unchanged legacy dict-only behaviour."""
    notifier, sent = _notifier(pic=None, dealer_map={"kl_pj": "legacy@dealer.example"})

    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer="kl_pj", customer_email=None,
    )

    assert len(sent) == 1
    assert sent[0]["to"] == ["legacy@dealer.example"]
