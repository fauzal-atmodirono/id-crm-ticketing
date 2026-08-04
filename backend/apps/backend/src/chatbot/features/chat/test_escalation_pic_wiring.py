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
    """Stateful enough to matter: tracks each conversation's custom
    attributes across calls (real Chatwoot's custom-attributes POST
    REPLACES the whole object; GET /conversations/{id} returns whatever was
    last POSTed) -- a stateless canned-response fake can't catch the
    Package C Task 5 review-round-2 bug this exists to prevent (the
    escalation notifier's case_state write silently erasing whatever
    open_handoff's own custom_attrs write had just set, or vice versa)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self._custom_attrs: dict[str, dict] = {}

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/conversations"):
            return {"id": 99}
        if method == "POST" and path.endswith("/custom_attributes"):
            conv_id = path.split("/")[-2]
            self._custom_attrs[conv_id] = dict((payload or {}).get("custom_attributes", {}))
            return {}
        if (
            method == "GET"
            and path.startswith("/conversations/")
            and path.rsplit("/", 1)[-1].isdigit()
        ):
            conv_id = path.rsplit("/", 1)[-1]
            return {"custom_attributes": dict(self._custom_attrs.get(conv_id, {}))}
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
    # Adapter constructed FIRST (no escalation_notifier yet) so the notifier
    # can be injected the REAL ChatwootAdapter._merge_custom_attributes
    # bound method -- not a hand-rolled reimplementation of its GET/union/
    # POST logic -- exactly mirroring main.py's own post-construction
    # wiring ("Post-construction injection is safe: escalation only fires
    # inside async request handlers").
    adapter = ChatwootAdapter(settings=s, pic_registry=registry)
    adapter._request = fake_cw._request  # type: ignore[method-assign]
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,  # type: ignore[arg-type]
        chatwoot_request=adapter._merge_custom_attributes,
    )
    adapter._escalation_notifier = notifier

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
    adapter = ChatwootAdapter(settings=s, pic_registry=registry)
    adapter._request = fake_cw._request  # type: ignore[method-assign]
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,
        chatwoot_request=adapter._merge_custom_attributes,
    )
    adapter._escalation_notifier = notifier

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
    adapter = ChatwootAdapter(settings=s, pic_registry=registry)
    adapter._request = fake_cw._request  # type: ignore[method-assign]
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=fake_email,  # type: ignore[arg-type]
        twilio_adapter=fake_twilio,  # type: ignore[arg-type]
        chatwoot_request=adapter._merge_custom_attributes,
    )
    adapter._escalation_notifier = notifier

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


async def test_open_handoff_case_state_and_classification_both_survive() -> None:
    """Package C Task 5 review round 2, Critical 1: this is the regression
    the missing test let through -- and (round 3 review correction) it must
    exercise a REUSED conversation to actually discriminate fixed from
    unfixed. On a FRESH conversation there is nothing pre-existing for
    ``_write_case_state`` to wipe, and round-1's already-merge-safe
    ``open_handoff`` write (which runs SECOND) papers over a bare-assign
    ``_write_case_state`` regardless -- a prior version of this test used a
    fresh conversation and kept passing even with ``_write_case_state``
    reverted to its pre-fix bare assign.

    So: seed ``external_id`` on a conversation the adapter already has
    CACHED (i.e. reused, not created by this call), simulating a case
    where an earlier write already put something on the conversation
    before this escalation runs. ``open_handoff`` -> ``_fire_escalation``
    -> ``notifier.notify()`` -> ``_write_case_state`` fires FIRST (writing
    ``case_state``); ``open_handoff`` writes its OWN custom attributes
    (``case_category``/``case_type``/``vehicle_model``) SECOND. With
    ``_write_case_state`` bare-assigning, it wipes the seeded
    ``external_id`` before ``open_handoff``'s own (merge-safe) write ever
    reads it back -- so the loss is permanent regardless of what runs
    after it. Asserts every attribute -- the seeded one, the notifier's,
    and open_handoff's own -- is present together at the end.

    Verified by actually reverting the fix (temporarily bare-assigning
    ``_write_case_state`` again) and confirming this test fails -- see the
    task-5-report.md fix-report entry for the command and output.
    """
    s = _settings()
    fake_cw = _FakeCW()
    fake_twilio = _FakeTwilio()

    registry = build_pic_registry(s)
    adapter = ChatwootAdapter(settings=s, pic_registry=registry)
    adapter._request = fake_cw._request  # type: ignore[method-assign]
    # Simulate a REUSED conversation: already known to the adapter (cache
    # hit -> _find_or_create_conversation returns it directly, no create),
    # already carrying an attribute from some earlier write.
    adapter._conv_by_session["sim-both-survive"] = "99"
    fake_cw._custom_attrs["99"] = {"external_id": "phone-CA1"}
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,  # type: ignore[arg-type]
        chatwoot_request=adapter._merge_custom_attributes,
    )
    adapter._escalation_notifier = notifier

    payload = HandoffOpenPayload(
        session_id="sim-both-survive",
        customer_name="Budi",
        customer_email="",
        ai_summary="App crashes on login",
        transcript=(Message(role="user", text="the app crashes"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
        category="sales",
        case_type="Complaint",
        vehicle_model="e.MAS 7",
    )
    await adapter.open_handoff(payload)

    final = fake_cw._custom_attrs.get("99", {})
    assert final.get("external_id") == "phone-CA1"  # seeded, pre-existing -- must survive
    assert final.get("case_state") == "WIP"  # written by the notifier, first
    assert final.get("case_category") == "sales"  # written by open_handoff, second
    assert final.get("case_type") == "Complaint"
    assert final.get("vehicle_model") == "e.MAS 7"
