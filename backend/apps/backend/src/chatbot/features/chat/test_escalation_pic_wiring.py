# features/chat/test_escalation_pic_wiring.py
"""Tests for Task 5: EscalationNotifier wired into ChatwootAdapter (Phase 2).

Covers:
- PIC-resolved team_id used for Chatwoot team assignment
- pic_<slug> label applied on escalation
- WA alert sent to PIC's WhatsApp number
- Escalation notification fires in Chatwoot-only mode (no external ticketing)
"""

from __future__ import annotations

import json
from typing import Any

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.models import HandoffOpenPayload, Message
from chatbot.features.chat.pic_registry import build_pic_registry
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "chatwoot_account_id": 1,
        "chatwoot_inbox_id": 7,
        "chatwoot_escalation_label": "ai-escalation",
        "chatwoot_complaint_label": "escalate",
        "escalation_email_enabled": False,  # email off so only WA + attr tested here
        "escalation_cc_pic": False,
        "pic_map_json": json.dumps(
            {
                "apps": {
                    "pic_name": "Alice Tan",
                    "pic_email": "alice@proton.my",
                    "pic_whatsapp": "+60123456789",
                    "chatwoot_team_id": 3,
                }
            }
        ),
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


class _FakeCW:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/conversations"):
            return {"id": 99}
        return {}


class _FakeTwilio:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, conversation_id: str, text: str) -> None:
        self.sent.append((conversation_id, text))


async def test_open_handoff_complaint_uses_pic_team_assignment() -> None:
    s = _settings()
    fake_cw = _FakeCW()
    fake_twilio = _FakeTwilio()

    registry = build_pic_registry(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,  # type: ignore[arg-type]
        chatwoot_request=fake_cw._request,
    )

    adapter = ChatwootAdapter(
        settings=s,
        pic_registry=registry,
        escalation_notifier=notifier,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Budi",
        customer_email="",
        ai_summary="App crashes on login",
        transcript=(Message(role="user", text="the app crashes"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Chatwoot team assignment uses the PIC's team_id
    team_calls = [
        pl for _, p, pl in fake_cw.calls if "/assignments" in p and pl and pl.get("team_id") == 3
    ]
    assert len(team_calls) == 1

    # WA alert sent to PIC
    assert len(fake_twilio.sent) == 1
    wa_to, _ = fake_twilio.sent[0]
    assert "+60123456789" in wa_to


async def test_open_handoff_applies_pic_label_on_escalation() -> None:
    """Fix 2: open_handoff must apply a ``pic_<slug>`` label when a PIC is resolved.

    The label ``pic_alice_tan`` must appear in the labels POST alongside the
    escalation labels so agents can filter/route by PIC in Chatwoot.
    """
    s = _settings()
    fake_cw = _FakeCW()
    fake_twilio = _FakeTwilio()

    registry = build_pic_registry(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,
        chatwoot_request=fake_cw._request,
    )

    adapter = ChatwootAdapter(
        settings=s,
        pic_registry=registry,
        escalation_notifier=notifier,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-pic-label",
        customer_name="Budi",
        customer_email="",
        ai_summary="App crashes on login",
        transcript=(Message(role="user", text="the app crashes"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Find the labels POST call and assert pic_alice_tan is present.
    label_calls = [
        pl["labels"] for _, path, pl in fake_cw.calls if "/labels" in path and pl and "labels" in pl
    ]
    assert label_calls, "Expected at least one labels POST"
    all_labels = label_calls[-1]  # the final labels merge call
    assert any(lbl.startswith("pic_") for lbl in all_labels), (
        f"Expected a pic_* label in {all_labels}"
    )
    assert "pic_alice_tan" in all_labels


class _FakeEmail:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(
        self, to: list[str], cc: list[str], subject: str, body: str, attachments: list
    ) -> None:
        self.sent.append({"to": to, "cc": cc, "subject": subject, "body": body})


async def test_escalation_notifies_pic_in_chatwoot_only_mode() -> None:
    """A complaint handoff must email + WhatsApp the PIC purely off the Chatwoot
    conversation — the escalation notification has no dependency on any external
    ticketing system."""
    s = _settings(
        escalation_email_enabled=True,
        escalation_cc_pic=True,
        pic_map_json=json.dumps(
            {
                "apps": {
                    "pic_name": "Alice Tan",
                    "pic_email": "alice@proton.my",
                    "pic_whatsapp": "+60123456789",
                    "chatwoot_team_id": 3,
                    "cc_emails": ["manager@proton.my"],
                }
            }
        ),
    )
    fake_cw = _FakeCW()
    fake_twilio = _FakeTwilio()
    fake_email = _FakeEmail()

    registry = build_pic_registry(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=fake_email,  # type: ignore[arg-type]
        twilio_adapter=fake_twilio,  # type: ignore[arg-type]
        chatwoot_request=fake_cw._request,
    )

    adapter = ChatwootAdapter(
        settings=s,
        pic_registry=registry,
        escalation_notifier=notifier,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-cwonly",
        customer_name="Budi",
        customer_email="",
        ai_summary="App crashes on login",
        transcript=(Message(role="user", text="the app crashes"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Email fired to the PIC (To) + CC'd the relevant personnel.
    assert len(fake_email.sent) == 1
    assert fake_email.sent[0]["to"] == ["alice@proton.my"]
    assert fake_email.sent[0]["cc"] == ["manager@proton.my"]
    assert "Chatwoot conversation #99" in fake_email.sent[0]["body"]

    # WhatsApp alert still fired to the PIC.
    assert len(fake_twilio.sent) == 1
    assert "+60123456789" in fake_twilio.sent[0][0]
