"""One pre-filled handoff note for chat and phone (2026-11-09 brief 3).

Fixtures use fake +600000000xx numbers and invented names only.
"""

from __future__ import annotations

import asyncio
from typing import Any

from chatbot.features.chat.handoff_note import (
    DealerLine,
    HandoffNote,
    HandoffNoteService,
    conversation_path,
    format_handoff_note,
    handoff_priority,
    is_phone_conversation,
)
from chatbot.platform.config import Settings

PHONE = "+60000000101"


# --- priority ---------------------------------------------------------------------


def test_breakdown_or_safety_is_urgent_whatever_the_classification() -> None:
    assert handoff_priority(classified="LOW", texts=["My car broke down on the highway"]) == (
        "urgent"
    )
    assert handoff_priority(texts=["kereta rosak di tol"]) == "urgent"
    assert handoff_priority(texts=["there was an accident"]) == "urgent"
    assert handoff_priority(texts=["smoke from the bonnet"]) == "urgent"


def test_classified_priority_is_used_otherwise_and_defaults_to_medium() -> None:
    assert handoff_priority(classified="HIGH", texts=["wants a refund"]) == "high"
    assert handoff_priority(classified="critical", texts=[]) == "urgent"
    assert handoff_priority(classified="normal", texts=[]) == "medium"
    assert handoff_priority(classified="nonsense", texts=["booking"]) == "medium"
    assert handoff_priority(texts=["service booking"]) == "medium"


# --- formatter ----------------------------------------------------------------------


def _note(**over: Any) -> HandoffNote:
    base: dict[str, Any] = {
        "channel": "Phone call",
        "reason": "breakdown",
        "summary": "Car will not start at a petrol station.",
        "priority": "urgent",
        "name": "Ahmad bin Ali",
        "mobile": PHONE,
        "vehicle_model": "Proton X50",
        "vehicle_plate": "DMO1101",
        "dealer": DealerLine(slug="petaling_jaya", confirmed=True),
        "case_type": "Complaint",
        "category": "Aftersales",
        "subcategory": "Aftersales: Breakdown",
        "transcript_path": "/app/accounts/1/conversations/42",
    }
    base.update(over)
    return HandoffNote(**base)


def test_note_has_every_line_and_masks_the_mobile() -> None:
    text = format_handoff_note(_note())
    lines = text.splitlines()
    assert lines[0] == "[Handoff to human agent] Phone call · Priority: URGENT"
    assert "Reason: breakdown" in lines
    assert "Summary: Car will not start at a petrol station." in lines
    assert "Who: Ahmad bin Ali · +60•••••0101" in lines
    assert PHONE not in text
    assert "Vehicle: Proton X50 · DMO1101" in lines
    assert "Case: Complaint · Aftersales: Breakdown" in lines
    assert "Live transcript: [open](/app/accounts/1/conversations/42)" in text


def test_confirmed_dealer_names_the_one_click_escalation() -> None:
    text = format_handoff_note(_note())
    assert (
        "Dealer: Petaling Jaya — escalate by adding the `escalate` label "
        "(the dealer_petaling_jaya label and the dealer email follow)"
    ) in text


def test_history_dealer_is_marked_unconfirmed_and_never_auto_escalated() -> None:
    text = format_handoff_note(_note(dealer=DealerLine(slug="petaling_jaya", confirmed=False)))
    assert "Dealer: Petaling Jaya (usual dealer from history, not confirmed)" in text
    assert "add the `dealer_petaling_jaya` and `escalate` labels" in text


def test_unmatched_dealer_text_and_missing_fields() -> None:
    text = format_handoff_note(
        _note(
            name="",
            mobile="",
            vehicle_model="",
            vehicle_plate="",
            dealer=DealerLine(text="Shah Alam service centre"),
            case_type="",
            category="",
            subcategory="",
            transcript_path="",
            summary="",
        )
    )
    assert "Who: not identified" in text
    assert "Vehicle: not given" in text
    assert "Summary: not given" in text
    assert "Dealer: Shah Alam service centre (not a configured dealer" in text
    assert "Case:" not in text
    assert "Live transcript" not in text


def test_no_dealer_at_all() -> None:
    assert "Dealer: not given" in format_handoff_note(_note(dealer=DealerLine()))


def test_conversation_path() -> None:
    assert conversation_path(3, "42") == "/app/accounts/3/conversations/42"


def test_is_phone_conversation() -> None:
    assert is_phone_conversation({"custom_attributes": {"external_id": "phone-CA1"}})
    assert is_phone_conversation({"additional_attributes": {"session_id": "phone-CA1"}})
    assert is_phone_conversation({"custom_attributes": {"caller_phone_source": "caller_id"}})
    assert not is_phone_conversation({"custom_attributes": {"external_id": "crm-9"}})
    assert not is_phone_conversation(None)


# --- chat service -------------------------------------------------------------------


class _Chatwoot:
    def __init__(self, conv: dict[str, Any] | None, *, fail: bool = False) -> None:
        self.conv = conv
        self.fail = fail
        self.notes: list[tuple[str, str]] = []
        self.priorities: list[tuple[str, str]] = []
        self.attrs: list[tuple[str, dict[str, Any]]] = []
        self.tags: list[tuple[str, str]] = []

    async def get_conversation(self, ticket_id: str) -> dict[str, Any] | None:
        if self.fail:
            raise RuntimeError("chatwoot down")
        return self.conv

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        self.notes.append((ticket_id, text))

    async def set_conversation_priority(self, ticket_id: str, priority: str) -> None:
        self.priorities.append((ticket_id, priority))

    async def set_conversation_attributes(self, ticket_id: str, attributes: dict[str, Any]) -> None:
        self.attrs.append((ticket_id, attributes))

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        self.tags.append((ticket_id, tag))


def _conv(**attrs: Any) -> dict[str, Any]:
    return {
        "id": 42,
        "labels": [],
        "custom_attributes": attrs,
        "meta": {
            "channel": "Channel::Whatsapp",
            "sender": {
                "name": "Siti Demo",
                "phone_number": PHONE,
                "custom_attributes": {"vehicle_no": "DMO1102"},
            },
        },
    }


def _service(cw: _Chatwoot, slugs: list[str] | None = None) -> HandoffNoteService:
    async def source() -> list[str]:
        return slugs if slugs is not None else ["petaling_jaya", "caroline_motor"]

    return HandoffNoteService(cw, Settings(_env_file=None), dealer_source=source)


async def test_chat_note_is_built_from_the_conversation_and_session_state() -> None:
    cw = _Chatwoot(_conv())
    state = {
        "handoff_reason": "negative_sentiment",
        "handoff_summary": "Customer unhappy with a repeated aircond fault.",
        "handoff_dealer": "Proton Petaling Jaya",
        "priority": "HIGH",
        "vehicle_model": "Proton Saga",
        "case_type": "Complaint",
        "category": "Aftersales",
        "subcategory": "Aftersales: Aircond",
    }
    await _service(cw).post_for_chat("42", state)
    (tid, text) = cw.notes[0]
    assert tid == "42"
    assert text.startswith("[Handoff to human agent] WhatsApp · Priority: HIGH")
    assert "Who: Siti Demo · +60•••••0101" in text
    assert "Vehicle: Proton Saga · DMO1102" in text
    assert "Dealer: Petaling Jaya — escalate by adding the `escalate` label" in text
    assert "Customer unhappy" in text
    assert "Case: Complaint · Aftersales: Aircond" in text
    assert cw.priorities == [("42", "high")]
    assert cw.attrs == [
        ("42", {"caller_dealer_text": "Proton Petaling Jaya", "caller_dealer": "petaling_jaya"})
    ]
    # A handoff is not a dealer escalation: no dealer_<slug> label (ruling).
    assert cw.tags == []


async def test_chat_breakdown_is_urgent_and_unmatched_dealer_is_text_only() -> None:
    cw = _Chatwoot(_conv())
    state = {
        "handoff_reason": "help_request",
        "handoff_summary": "Car broke down on the way to work.",
        "handoff_dealer": "Motor",  # matches two "... motor"? only one here, but too vague
        "priority": "MEDIUM",
    }
    await _service(cw, ["caroline_motor", "komang_motor"]).post_for_chat("42", state)
    assert cw.priorities == [("42", "urgent")]
    assert cw.attrs == [("42", {"caller_dealer_text": "Motor"})]
    assert "not a configured dealer" in cw.notes[0][1]


async def test_chat_dealer_from_existing_attribute_and_lead_details() -> None:
    cw = _Chatwoot(_conv(caller_dealer="caroline_motor"))
    await _service(cw).post_for_chat("42", {"handoff_reason": "help_request"})
    assert "Dealer: Caroline Motor — escalate" in cw.notes[0][1]
    assert cw.attrs == []  # already stored

    cw2 = _Chatwoot(_conv())
    await _service(cw2).post_for_chat(
        "42", {"lead_details": {"preferred_dealer": "Caroline Motor"}}
    )
    assert cw2.attrs[0][1]["caller_dealer"] == "caroline_motor"


async def test_chat_falls_back_to_the_given_summary_and_customer() -> None:
    cw = _Chatwoot(None)
    await _service(cw).post_for_chat(
        "7",
        {},
        channel="Email",
        customer_name="Demo Customer",
        fallback_summary="Customer requested a human agent.",
    )
    text = cw.notes[0][1]
    assert text.startswith("[Handoff to human agent] Email · Priority: MEDIUM")
    assert "Who: Demo Customer" in text
    assert "Summary: Customer requested a human agent." in text


async def test_chat_note_never_raises_and_still_posts_when_the_read_fails() -> None:
    cw = _Chatwoot(None, fail=True)
    await _service(cw).post_for_chat("42", {"handoff_reason": "help_request"})
    assert cw.notes and cw.notes[0][0] == "42"


async def test_chat_note_is_bounded() -> None:
    class _Slow(_Chatwoot):
        async def get_conversation(self, ticket_id: str) -> dict[str, Any] | None:
            await asyncio.sleep(5)
            return None

    cw = _Slow(None)
    svc = HandoffNoteService(cw, Settings(_env_file=None, handoff_note_timeout_seconds=0.05))
    await svc.post_for_chat("42", {})  # returns, does not raise


async def test_dealer_list_failure_means_no_match() -> None:
    async def boom() -> list[str]:
        raise RuntimeError("firestore down")

    cw = _Chatwoot(_conv())
    svc = HandoffNoteService(cw, Settings(_env_file=None), dealer_source=boom)
    await svc.post_for_chat("42", {"handoff_dealer": "Petaling Jaya"})
    assert cw.attrs == [("42", {"caller_dealer_text": "Petaling Jaya"})]


async def test_post_callable_replaces_the_private_note_write() -> None:
    cw = _Chatwoot(_conv())
    posted: list[str] = []

    async def post(text: str) -> None:
        posted.append(text)

    await _service(cw).post_for_chat("42", {"handoff_reason": "help_request"}, post=post)
    assert cw.notes == []
    assert posted and posted[0].startswith("[Handoff to human agent]")
