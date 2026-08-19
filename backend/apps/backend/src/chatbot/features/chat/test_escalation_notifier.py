from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.features.chat.escalation_notifier import EscalationNotifier, build_dealer_email_map
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
from chatbot.features.chat.pic_store import DealerRecord
from chatbot.platform.config import Settings


def test_build_dealer_email_map_parses_json() -> None:
    settings = _settings(
        dealer_email_map_json='{"kl_pj": "kl-pj@dealer.example", "JB": "jb@dealer.example"}'
    )
    result = build_dealer_email_map(settings)
    assert result == {"kl_pj": ["kl-pj@dealer.example"], "jb": ["jb@dealer.example"]}


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
    merge_calls: list[tuple[str, dict[str, Any]]] = []

    class _FakeEmailSender:
        def send(
            self,
            to: list[str],
            cc: list[str],
            subject: str,
            body: str,
            attachments: list,
            *,
            reply_to: str | None = None,
        ) -> None:
            sent_emails.append({"to": to, "cc": cc, "subject": subject, "body": body})

    async def _fake_merge(ticket_id: str, attributes: dict[str, Any]) -> None:
        merge_calls.append((ticket_id, attributes))

    class _FakeTwilio:
        async def send_message(self, conversation_id: str, text: str) -> None:
            sent_wa.append((conversation_id, text))

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=_FakeTwilio(),  # type: ignore[arg-type]
        chatwoot_request=_fake_merge,
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

    # Chatwoot custom attribute "case_state" set, via the merge-safe writer
    assert len(merge_calls) >= 1
    ticket_id, attrs = merge_calls[0]
    assert ticket_id == "42"
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
    merge_calls: list[str] = []

    class _ExplodingEmailSender:
        def send(self, **_: Any) -> None:
            raise RuntimeError("SMTP server down")

    class _FakeTwilio:
        async def send_message(self, conversation_id: str, text: str) -> None:
            wa_calls.append(conversation_id)  # text not inspected in this test

    async def _fake_merge(ticket_id: str, _attributes: dict[str, Any]) -> None:
        merge_calls.append(ticket_id)

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_ExplodingEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=_FakeTwilio(),  # type: ignore[arg-type]
        chatwoot_request=_fake_merge,
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
    assert merge_calls == ["99"]


async def test_notify_does_not_raise_when_chatwoot_request_raises() -> None:
    """If the Chatwoot case_state write raises, notify() swallows it and returns normally."""
    sent_emails: list[str] = []

    class _FakeEmailSender:
        def send(self, to: list[str], **_: Any) -> None:
            sent_emails.append(to[0])

    async def _exploding_merge(_ticket_id: str, _attributes: dict[str, Any]) -> None:
        raise RuntimeError("Chatwoot unreachable")

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=_exploding_merge,
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
            self,
            to: list[str],
            cc: list[str],
            subject: str,
            body: str,
            attachments: list,
            *,
            reply_to: str | None = None,
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
            self,
            to: list[str],
            cc: list[str],
            subject: str,
            body: str,
            attachments: list,
            *,
            reply_to: str | None = None,
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
            self,
            to: list[str],
            cc: list[str],
            subject: str,
            body: str,
            attachments: list,
            *,
            reply_to: str | None = None,
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
# EM-7: notify_escalation (two-thread email escalation)
# ---------------------------------------------------------------------------


def _notifier(
    *,
    pic: PicEntry | None = _APPS_PIC,
    dealer_map: dict[str, list[str]] | None = None,
    email_sender=None,
    settings_kw: dict[str, Any] | None = None,
    tenant_settings_store: Any = None,
) -> tuple[EscalationNotifier, list[dict[str, Any]]]:
    sent_emails: list[dict[str, Any]] = []

    class _FakeEmailSender:
        def send(self, to, cc, subject, body, attachments, *, reply_to=None) -> None:
            sent_emails.append({"to": to, "cc": cc, "subject": subject, "body": body})

    # notify_escalation (what every caller of this helper
    # exercises) never calls _write_case_state -- only notify() does -- so
    # this is never actually invoked here; kept shaped like the real
    # merge-safe callable purely so nothing here looks like it's still on
    # the old raw-_request contract.
    async def _fake_merge(ticket_id: str, attributes: dict[str, Any]) -> None:
        return None

    notifier = EscalationNotifier(
        settings=_settings(**(settings_kw or {})),
        pic_registry=_registry(pic),
        email_sender=email_sender or _FakeEmailSender(),
        twilio_adapter=None,
        chatwoot_request=_fake_merge,
        dealer_email_map=dealer_map or {},
        tenant_settings_store=tenant_settings_store,
    )
    return notifier, sent_emails


async def test_notify_escalation_sends_customer_ack_when_enabled() -> None:
    notifier, sent = _notifier(pic=None, settings_kw={"email_escalation_ack_enabled": True})
    await notifier.notify_escalation(
        conv_id="9",
        title="Late delivery",
        body="details",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
    )
    assert len(sent) == 1
    assert sent[0]["to"] == ["alex@customer.example"]
    assert "specialist team" in sent[0]["body"]


async def test_notify_escalation_skips_ack_when_disabled() -> None:
    notifier, sent = _notifier(pic=None, settings_kw={"email_escalation_ack_enabled": False})
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
    )
    assert sent == []


async def test_customer_ack_uses_tenant_store_override_when_present() -> None:
    """Task 18: an operator-edited template in the tenant store wins over the
    env default, resolved via the same get_effective_value helper the assist
    router already uses."""
    store = InMemoryTenantSettingsStore()
    await store.set_overrides({"email_escalation_ack_template": "Custom stored ack body"})
    notifier, sent = _notifier(
        pic=None,
        settings_kw={"email_escalation_ack_enabled": True},
        tenant_settings_store=store,
    )
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
    )
    assert len(sent) == 1
    assert sent[0]["body"] == "Custom stored ack body"


async def test_customer_ack_falls_back_to_env_when_store_has_no_override() -> None:
    """An unset store value means "not configured" -- env template text goes
    out byte-identically, never an empty body."""
    store = InMemoryTenantSettingsStore()
    notifier, sent = _notifier(
        pic=None,
        settings_kw={"email_escalation_ack_enabled": True},
        tenant_settings_store=store,
    )
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
    )
    assert len(sent) == 1
    assert "specialist team" in sent[0]["body"]  # unchanged env default


async def test_customer_ack_falls_back_to_env_when_store_unreachable() -> None:
    """Fail-open: a broken/unreachable tenant store must not stop the
    acknowledgement -- fall back to env rather than sending nothing."""

    class _BoomStore:
        async def get_overrides(self) -> dict[str, Any]:
            raise RuntimeError("store unreachable")

        async def set_overrides(self, partial: dict[str, Any]) -> None:
            raise RuntimeError("store unreachable")

    notifier, sent = _notifier(
        pic=None,
        settings_kw={"email_escalation_ack_enabled": True},
        tenant_settings_store=_BoomStore(),
    )
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
    )
    assert len(sent) == 1
    assert "specialist team" in sent[0]["body"]


async def test_notify_escalation_sends_dealer_forward_when_mapped() -> None:
    notifier, sent = _notifier(
        pic=None,
        dealer_map={"kl_pj": ["kl-pj@dealer.example"]},
    )
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer="kl_pj",
        customer_email=None,
    )
    assert len(sent) == 1
    assert sent[0]["to"] == ["kl-pj@dealer.example"]


async def test_notify_escalation_sends_to_every_group_member() -> None:
    """Task 5's headline requirement, asserted end-to-end: a dealer mapped to
    several addresses (a group, not a single contact) must have its
    escalation forward reach every one of them in a single send -- not just
    round-trip through storage. Covers the env-var (DEALER_EMAIL_MAP_JSON)
    path; the store-backed path is covered by
    test_dealer_forward_uses_store_record_when_present below (extended to a
    multi-member record)."""
    notifier, sent = _notifier(
        pic=None,
        dealer_map={"kl_pj": ["a@dealer.example", "b@dealer.example", "c@dealer.example"]},
    )
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer="kl_pj",
        customer_email=None,
    )
    assert len(sent) == 1
    assert sent[0]["to"] == ["a@dealer.example", "b@dealer.example", "c@dealer.example"]


async def test_notify_escalation_skips_dealer_when_unmapped() -> None:
    notifier, sent = _notifier(pic=None, dealer_map={})
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer="unknown_slug",
        customer_email=None,
    )
    assert sent == []


async def test_notify_escalation_sends_pic_and_dealer_together() -> None:
    notifier, sent = _notifier(
        pic=_APPS_PIC,
        dealer_map={"kl_pj": ["kl-pj@dealer.example"]},
        settings_kw={"escalation_email_enabled": True},
    )
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department="dept_apps",
        dealer="kl_pj",
        customer_email=None,
    )
    recipients = {tuple(e["to"]) for e in sent}
    assert ("alice@proton.my",) in recipients
    assert ("kl-pj@dealer.example",) in recipients


async def test_notify_escalation_noop_when_everything_off() -> None:
    notifier, sent = _notifier(
        pic=None,
        dealer_map={},
        settings_kw={"escalation_email_enabled": False, "email_escalation_ack_enabled": False},
    )
    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
    )
    assert sent == []


# ---------------------------------------------------------------------------
# Task 2: dealer resolution is store-first, dict fallback
# ---------------------------------------------------------------------------


async def test_dealer_forward_uses_store_record_when_present() -> None:
    """DealerStore.get() wins over the legacy dealer_email_map dict."""
    dealer_store = AsyncMock()
    dealer_store.get.return_value = DealerRecord(dealer="kl_pj", emails=["store@dealer.example"])

    notifier, sent = _notifier(pic=None, dealer_map={"kl_pj": ["legacy@dealer.example"]})
    notifier._dealer_store = dealer_store

    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer="kl_pj",
        customer_email=None,
    )

    assert len(sent) == 1
    assert sent[0]["to"] == ["store@dealer.example"]
    dealer_store.get.assert_awaited_once_with("kl_pj")


async def test_dealer_forward_store_record_sends_to_every_group_member() -> None:
    """Task 5's headline requirement via the store-backed path (the one the
    admin UI actually writes through): a multi-member DealerRecord must
    reach every member, not just its first."""
    dealer_store = AsyncMock()
    dealer_store.get.return_value = DealerRecord(
        dealer="kl_pj", emails=["a@dealer.example", "b@dealer.example"]
    )

    notifier, sent = _notifier(pic=None, dealer_map={})
    notifier._dealer_store = dealer_store

    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer="kl_pj",
        customer_email=None,
    )

    assert len(sent) == 1
    assert sent[0]["to"] == ["a@dealer.example", "b@dealer.example"]


async def test_dealer_forward_falls_back_to_dict_when_store_has_no_entry() -> None:
    dealer_store = AsyncMock()
    dealer_store.get.return_value = None

    notifier, sent = _notifier(pic=None, dealer_map={"kl_pj": ["legacy@dealer.example"]})
    notifier._dealer_store = dealer_store

    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer="kl_pj",
        customer_email=None,
    )

    assert len(sent) == 1
    assert sent[0]["to"] == ["legacy@dealer.example"]


async def test_dealer_forward_works_without_a_store_configured() -> None:
    """dealer_store=None (default) -> unchanged legacy dict-only behaviour."""
    notifier, sent = _notifier(pic=None, dealer_map={"kl_pj": ["legacy@dealer.example"]})

    await notifier.notify_escalation(
        conv_id="9",
        title="t",
        body="b",
        department=None,
        dealer="kl_pj",
        customer_email=None,
    )

    assert len(sent) == 1
    assert sent[0]["to"] == ["legacy@dealer.example"]


# ---------------------------------------------------------------------------
# The customer acknowledgement's subject (2026-08-19)
# ---------------------------------------------------------------------------

_REAL_FIRST_EMAIL = (
    "Hi, I bought an e.MAS 7 from Proton e.MAS Petaling Jaya last month, "
    "plate VAB 3271. The home charger"
)


async def test_customer_ack_subject_never_carries_the_message_body() -> None:
    """A live run on 2026-08-19 mailed the customer their own first email,
    cut mid-word, as the subject. The internal legs want that text; the
    customer must never be quoted back at himself."""
    notifier, sent = _notifier(pic=None, settings_kw={"email_escalation_ack_enabled": True})

    await notifier.notify_escalation(
        conv_id="42",
        title=_REAL_FIRST_EMAIL,
        body="b",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
        customer_subject="Update on your case (#42)",
    )

    assert len(sent) == 1
    assert sent[0]["subject"] == "Update on your case (#42)"
    assert "e.MAS 7" not in sent[0]["subject"]
    assert sent[0]["cc"] == []


async def test_customer_ack_subject_falls_back_to_the_old_shape_when_absent() -> None:
    """An agent service that predates this change sends no customer_subject;
    the mail it produces must stay byte-identical."""
    notifier, sent = _notifier(pic=None, settings_kw={"email_escalation_ack_enabled": True})

    await notifier.notify_escalation(
        conv_id="42",
        title="Late delivery",
        body="b",
        department=None,
        dealer=None,
        customer_email="alex@customer.example",
    )

    assert sent[0]["subject"] == "Update on your case: Late delivery"


async def test_internal_legs_keep_the_descriptive_title() -> None:
    """The PIC leg is triaged from an inbox, so its subject keeps the case
    text and the [CASE-n] correlation tag -- customer_subject must not leak
    into it."""
    notifier, sent = _notifier(
        dealer_map={"kl_pj": ["dealer@kl.example"]},
        settings_kw={"email_escalation_ack_enabled": True},
    )

    await notifier.notify_escalation(
        conv_id="42",
        title=_REAL_FIRST_EMAIL,
        body="b",
        department="apps",
        dealer="kl_pj",
        customer_email="alex@customer.example",
        customer_subject="Update on your case (#42)",
    )

    internal = [m for m in sent if m["to"] != ["alex@customer.example"]]
    assert len(internal) == 2
    for message in internal:
        assert "e.MAS 7" in message["subject"]
        assert "Update on your case (#42)" != message["subject"]
