"""SLA alerts reach the department PIC group by email and the conversation as a note.

Task 15: gives an SLA breach two more delivery legs beyond the single global
WhatsApp ping -- an email to the department PIC group (resolved from the
conversation's own ``dept_<slug>`` label) and a private Chatwoot note. Every
leg is independent and best-effort: a failure in one must never suppress the
others, since these are the only signals an operator gets that a case is
breaching.
"""

from __future__ import annotations

from chatbot.features.chat.pic_registry import PicEntry
from chatbot.features.chat.sla import _build_pic_alert


class _Settings:
    sla_pic_whatsapp = ""
    sla_alert_email_enabled = True
    sla_alert_note_enabled = True


class _Registry:
    async def lookup(self, dept):
        if dept != "sales":
            return None
        return PicEntry(
            pic_name="Aduy",
            pic_email="pic@test",
            pic_whatsapp="",
            cc_emails=["cc@test"],
        )


class _Sender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to, "cc": cc, "subject": subject})


class _Notes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, conv_id: str, text: str) -> None:
        self.calls.append((conv_id, text))


async def test_emails_the_department_group_and_posts_a_note():
    sender, notes = _Sender(), _Notes()
    alert = _build_pic_alert(
        _Settings(), None, pic_registry=_Registry(), email_sender=sender, note_poster=notes
    )

    await alert("42", "SLA_BREACH_NO_RESPONSE", "no first reply after 8h", ["dept_sales"])

    assert sender.calls[0]["to"] == ["pic@test"]
    assert sender.calls[0]["cc"] == ["cc@test"]
    assert "42" in sender.calls[0]["subject"]
    assert notes.calls[0][0] == "42"
    assert "SLA" in notes.calls[0][1]


async def test_posts_note_even_when_department_is_unmapped():
    sender, notes = _Sender(), _Notes()
    alert = _build_pic_alert(
        _Settings(), None, pic_registry=_Registry(), email_sender=sender, note_poster=notes
    )

    await alert("42", "SLA_BREACH_UNRESOLVED", "still open", ["dept_unknown"])

    assert sender.calls == []
    assert notes.calls and notes.calls[0][0] == "42"


async def test_disabled_flags_produce_no_alert_at_all():
    settings = _Settings()
    settings.sla_alert_email_enabled = False
    settings.sla_alert_note_enabled = False
    assert (
        _build_pic_alert(settings, None, pic_registry=_Registry(), email_sender=_Sender())
        is None
    )


async def test_email_failure_does_not_stop_the_note():
    class _Boom(_Sender):
        def send(self, *a, **kw):
            raise RuntimeError("smtp down")

    notes = _Notes()
    alert = _build_pic_alert(
        _Settings(), None, pic_registry=_Registry(), email_sender=_Boom(), note_poster=notes
    )

    await alert("42", "SLA_BREACH_NO_RESPONSE", "r", ["dept_sales"])

    assert notes.calls


async def test_note_failure_does_not_stop_the_email():
    """The reverse of the email-failure case: a broken note poster must not
    swallow the email leg -- both are independent, best-effort deliveries."""

    class _BoomNotes(_Notes):
        async def __call__(self, conv_id: str, text: str) -> None:
            raise RuntimeError("chatwoot down")

    sender = _Sender()
    alert = _build_pic_alert(
        _Settings(), None, pic_registry=_Registry(), email_sender=sender, note_poster=_BoomNotes()
    )

    await alert("42", "SLA_BREACH_NO_RESPONSE", "r", ["dept_sales"])

    assert sender.calls


async def test_whatsapp_failure_does_not_stop_email_or_note():
    """A broken WhatsApp leg must not swallow the email/note legs either --
    all three are independent, best-effort deliveries."""

    class _Settings2(_Settings):
        sla_pic_whatsapp = "+60123456789"

    class _BoomTwilio:
        async def send_message(self, conversation_id, text):  # noqa: ARG002
            raise RuntimeError("twilio down")

    sender, notes = _Sender(), _Notes()
    alert = _build_pic_alert(
        _Settings2(),
        _BoomTwilio(),
        pic_registry=_Registry(),
        email_sender=sender,
        note_poster=notes,
    )

    await alert("42", "SLA_BREACH_NO_RESPONSE", "r", ["dept_sales"])

    assert sender.calls
    assert notes.calls
