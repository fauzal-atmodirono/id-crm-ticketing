"""P2 tasks 6-7 — who is actually on duty, and who tier-2 wakes up.

Escalating to a PIC who logged off two hours ago is escalation in name only.
This adds an on-duty check that WIDENS the recipient list (an offline PIC's
colleagues get told too) and reports `all_offline`, which tier-2 uses to
shorten its timer -- nobody is coming for a while, so waiting the full window
before alerting a manager wastes the only hours that matter.

The safety property, asserted last and worth stating first: **the check can
never return an empty recipient list**. Whatever presence says, whatever the
API does, the PIC still gets the mail. An unescalated complaint is the exact
failure this package exists to eliminate, and a presence feature that could
cause one would be worse than no feature.
"""

from __future__ import annotations

from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
from chatbot.features.chat.pic_store import PicRecord


class _Presence:
    def __init__(self, statuses: dict[str, str] | None = None, raises: bool = False) -> None:
        self._statuses = statuses or {}
        self._raises = raises
        self.calls = 0

    async def fetch_agents(self):
        from chatbot.features.routing.presence import AgentRecord

        self.calls += 1
        if self._raises:
            raise RuntimeError("chatwoot down")
        return [
            AgentRecord(id=i, name=email, availability_status=status, email=email)
            for i, (email, status) in enumerate(self._statuses.items(), start=1)
        ]


class _Store:
    def __init__(self, record: PicRecord | None) -> None:
        self._record = record

    async def get(self, key):
        del key
        return self._record


def _registry(record: PicRecord | None = None) -> PicRegistry:
    return PicRegistry({}, store=_Store(record))


PIC = PicRecord(
    department="sales",
    pic_name="Aduy",
    pic_email="pic@test",
    pic_whatsapp="",
    cc_emails=["colleague@test"],
)


# --- task 6: the on-duty check --------------------------------------------


async def test_an_online_pic_is_notified_normally():
    resolution = await _registry(PIC).resolve(
        "sales", presence=_Presence({"pic@test": "online"})
    )
    assert resolution.recipients == ["pic@test"]
    assert resolution.all_offline is False


async def test_an_offline_pic_with_an_online_colleague_notifies_both():
    """Widening, never narrowing: the PIC is still told."""
    resolution = await _registry(PIC).resolve(
        "sales",
        presence=_Presence({"pic@test": "offline", "colleague@test": "online"}),
    )
    assert "pic@test" in resolution.recipients
    assert "colleague@test" in resolution.recipients


async def test_an_entirely_offline_department_is_still_notified():
    resolution = await _registry(PIC).resolve(
        "sales",
        presence=_Presence({"pic@test": "offline", "colleague@test": "offline"}),
    )
    assert resolution.recipients


async def test_an_entirely_offline_department_sets_all_offline_true():
    resolution = await _registry(PIC).resolve(
        "sales",
        presence=_Presence({"pic@test": "offline", "colleague@test": "offline"}),
    )
    assert resolution.all_offline is True


async def test_a_busy_pic_counts_as_present():
    """Busy means at their desk and loaded, not gone."""
    resolution = await _registry(PIC).resolve(
        "sales", presence=_Presence({"pic@test": "busy"})
    )
    assert resolution.all_offline is False


async def test_a_presence_fetch_failure_falls_back_to_notifying_everyone():
    resolution = await _registry(PIC).resolve("sales", presence=_Presence(raises=True))
    assert "pic@test" in resolution.recipients
    assert resolution.all_offline is False


async def test_an_agent_missing_from_chatwoot_entirely_is_not_assumed_offline():
    """A PIC who is not a Chatwoot agent at all (a dealer principal, say) has
    no presence to read. Unknown is not the same as offline."""
    resolution = await _registry(PIC).resolve(
        "sales", presence=_Presence({"someone-else@test": "online"})
    )
    assert resolution.all_offline is False


async def test_the_flag_off_skips_presence_entirely_and_makes_no_api_call():
    presence = _Presence({"pic@test": "offline"})
    resolution = await _registry(PIC).resolve("sales", presence=None)

    assert presence.calls == 0
    assert resolution.recipients == ["pic@test"]
    assert resolution.all_offline is False


async def test_the_check_can_never_return_an_empty_recipient_list():
    """The safety property. Every degenerate input, one assertion."""
    cases = [
        _Presence({}),
        _Presence({"pic@test": "offline"}),
        _Presence(raises=True),
        None,
    ]
    for presence in cases:
        resolution = await _registry(PIC).resolve("sales", presence=presence)
        assert resolution.recipients, f"empty recipients for {presence}"


async def test_an_unknown_department_resolves_to_nothing_without_raising():
    resolution = await _registry(None).resolve("nosuchdept", presence=_Presence())
    assert resolution.entry is None
    assert resolution.recipients == []


# --- task 7: tier-2 reaches someone different -----------------------------


async def test_a_legacy_pic_entry_without_manager_fields_still_loads():
    assert PIC.escalation_manager_email == ""
    assert PicEntry(pic_name="a", pic_email="b", pic_whatsapp="").escalation_manager_email == ""


async def test_tier2_goes_to_the_manager_contact_when_configured():
    record = PicRecord(
        department="sales",
        pic_name="Aduy",
        pic_email="pic@test",
        pic_whatsapp="",
        escalation_manager_email="manager@test",
    )
    resolution = await _registry(record).resolve("sales", presence=None)
    assert resolution.tier2_recipients == ["manager@test"]


async def test_tier2_falls_back_to_the_original_group_when_unconfigured():
    """Better the same people twice than nobody."""
    resolution = await _registry(PIC).resolve("sales", presence=None)
    assert resolution.tier2_recipients == ["pic@test"]


# --- task 7: the tier-2 alert actually routes ------------------------------


from chatbot.features.chat.sla import _build_level2_alert  # noqa: E402


class _EmailSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to, "subject": subject})


class _Twilio:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_message(self, conversation_id, text):
        self.calls.append(conversation_id)


class _Tier2Settings:
    escalation_level2_whatsapp = "+60112345678"


async def test_tier2_emails_the_department_manager_when_configured():
    sender = _EmailSender()
    registry = _registry(
        PicRecord(
            department="sales",
            pic_name="Aduy",
            pic_email="pic@test",
            pic_whatsapp="",
            escalation_manager_email="manager@test",
        )
    )
    alert = _build_level2_alert(
        _Tier2Settings(), _Twilio(), pic_registry=registry, email_sender=sender
    )

    await alert("42", "TIER2_ESCALATION", "unanswered", ["dept_sales"])

    assert [c["to"] for c in sender.calls] == [["manager@test"]]


async def test_tier2_without_a_manager_still_pings_the_global_number():
    sender, twilio = _EmailSender(), _Twilio()
    alert = _build_level2_alert(
        _Tier2Settings(), twilio, pic_registry=_registry(PIC), email_sender=sender
    )

    await alert("42", "TIER2_ESCALATION", "unanswered", ["dept_sales"])

    assert sender.calls == []
    assert twilio.calls, "an unconfigured manager must not mean silence"


async def test_a_manager_email_failure_does_not_suppress_the_whatsapp_leg():
    class _Broken(_EmailSender):
        def send(self, *a, **k):
            raise RuntimeError("smtp down")

    twilio = _Twilio()
    registry = _registry(
        PicRecord(
            department="sales",
            pic_name="Aduy",
            pic_email="pic@test",
            pic_whatsapp="",
            escalation_manager_email="manager@test",
        )
    )
    alert = _build_level2_alert(
        _Tier2Settings(), twilio, pic_registry=registry, email_sender=_Broken()
    )

    await alert("42", "TIER2_ESCALATION", "unanswered", ["dept_sales"])

    assert twilio.calls


# --- task 6 wired into the notifier ----------------------------------------


from chatbot.features.chat.escalation_notifier import EscalationNotifier  # noqa: E402


class _NotifySender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to})


class _NotifySettings:
    escalation_email_enabled = True
    email_escalation_ack_enabled = False
    email_escalation_ack_template = ""
    escalation_ack_chat_template = ""
    escalation_cc_pic = False
    escalation_cc_dealer = False
    escalation_reply_to_template = ""
    dealer_email_map_json = ""
    escalation_attachment_budget_bytes = 0
    escalation_failure_note_enabled = False
    escalation_presence_check_enabled = True


async def _cw(conv_id, attrs):
    return None


async def _notify_with(presence, settings):
    sender = _NotifySender()
    notifier = EscalationNotifier(
        settings, _registry(PIC), sender, None, _cw, presence=presence
    )
    await notifier.notify_escalation(
        conv_id="42", title="t", body="b", department="dept_sales",
        dealer=None, customer_email=None, ack_transport="none",
    )
    return sender


async def test_the_pic_leg_widens_to_an_online_colleague_when_the_pic_is_offline():
    sender = await _notify_with(
        _Presence({"pic@test": "offline", "colleague@test": "online"}),
        _NotifySettings(),
    )
    assert set(sender.calls[0]["to"]) == {"pic@test", "colleague@test"}


async def test_the_pic_leg_is_unchanged_when_the_check_is_off():
    settings = _NotifySettings()
    settings.escalation_presence_check_enabled = False
    presence = _Presence({"pic@test": "offline", "colleague@test": "online"})

    sender = await _notify_with(presence, settings)

    assert sender.calls[0]["to"] == ["pic@test"]
    assert presence.calls == 0, "the check must cost nothing when off"


async def test_a_presence_outage_still_mails_the_pic():
    sender = await _notify_with(_Presence(raises=True), _NotifySettings())
    assert sender.calls[0]["to"] == ["pic@test"]
