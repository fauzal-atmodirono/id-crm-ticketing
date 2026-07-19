"""Integration smoke tests: Phase-2 pieces import and construct without error.

These tests verify that PicRegistry, SmtpEmailSender, and EscalationNotifier
can all be constructed from a minimal Settings object, and that the key
constants from case_state are stable.  No network calls, no SMTP, no Twilio.
"""
from __future__ import annotations

from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR, CaseState
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry, build_pic_registry
from chatbot.features.metrics.email_sender import SmtpEmailSender
from chatbot.platform.config import Settings


def test_imports_and_constructs_without_error() -> None:
    s = Settings(_env_file=None, pic_map_json="", escalation_email_enabled=False)
    registry = build_pic_registry(s)
    sender = SmtpEmailSender(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=sender,
        twilio_adapter=None,
        chatwoot_request=None,  # type: ignore[arg-type]
    )
    assert notifier is not None
    assert CaseState.TEMP_CLOSED == "TEMP_CLOSED"
    assert CHATWOOT_CASE_STATE_ATTR == "case_state"


def test_pic_entry_full_construction() -> None:
    entry = PicEntry(
        pic_name="Zaid",
        pic_email="zaid@proton.my",
        pic_whatsapp="+60199999999",
        zammad_group="Sales-Support",
        chatwoot_team_id=7,
    )
    assert entry.zammad_group == "Sales-Support"
    assert entry.chatwoot_team_id == 7
