"""PhoneBridge with caller context on (2026-11-09 brief items 4-6, 9).

Flag-off behaviour is pinned by test_bridge.py, which never passes a
caller_service and must keep passing unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from chatbot.features.chat.phone.bridge import PhoneBridge
from chatbot.features.chat.phone.caller_context import CallerContext, CallerDetails
from chatbot.features.chat.phone.live_events import ToolCall
from chatbot.features.chat.phone.test_bridge import _FakeKnowledge, _FakeLive, _FakeLog
from chatbot.platform.config import Settings

PHONE = "+60000000101"
AHMAD = {
    "contact_id": 11,
    "name": "Ahmad bin Ali",
    "vehicles": [{"plate": "DMO1101", "model": "Proton X50"}],
    "last_dealer": "",
    "open_cases": 0,
}


class _Log(_FakeLog):
    def __init__(self) -> None:
        super().__init__()
        self.customers: list[tuple[str | None, str | None]] = []

    async def ensure_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> str:
        self.customers.append((customer_name, customer_phone))
        return await super().ensure_conversation_ticket(
            session_id, subject, customer_name, customer_phone
        )


class _Service:
    def __init__(self, ctx: CallerContext | None = None) -> None:
        self.ctx = ctx
        self.lookups: list[tuple[str, str]] = []
        self.saves: list[dict[str, Any]] = []
        self.allow = True

    def allow_spoken_lookup(self, phone: str) -> bool:
        return self.allow

    async def lookup(self, phone: str, source: str) -> CallerContext:
        self.lookups.append((phone, source))
        return self.ctx or CallerContext(phone=phone, source=source)

    async def save(self, **kw: Any) -> None:
        self.saves.append(kw)


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "phone_caller_context_enabled": True,
        "phone_transcript_live_enabled": True,
        "chatwoot_enabled": True,
    }
    base.update(kw)
    return Settings(**base)


def _bridge(
    scripted: list[Any],
    *,
    service: _Service,
    ctx: CallerContext | None,
    log: _Log | None = None,
    settings: Settings | None = None,
) -> tuple[PhoneBridge, _FakeLive, _Log]:
    live = _FakeLive(scripted)
    log = log or _Log()

    async def send(_: dict[str, object]) -> None:
        return None

    bridge = PhoneBridge(
        live,
        _FakeKnowledge(),
        log,
        send,
        settings or _settings(),
        caller_service=service,  # type: ignore[arg-type]
        caller_context=ctx,
        dealer_slugs=["petaling_jaya", "caroline_motor"],
    )
    return bridge, live, log


def _start(call_sid: str = "CA1") -> dict[str, object]:
    return {"event": "start", "start": {"streamSid": "MZ1", "callSid": call_sid}}


async def test_known_caller_conversation_is_created_on_their_contact() -> None:
    ctx = CallerContext.from_candidates(PHONE, "caller_id", [AHMAD])
    bridge, _, log = _bridge([], service=_Service(ctx), ctx=ctx)
    await bridge.handle_twilio(_start())
    await bridge.finalize()
    assert log.customers[0] == ("Ahmad bin Ali", PHONE)


async def test_save_caller_details_replies_at_once_and_writes_in_background() -> None:
    ctx = CallerContext.from_candidates(PHONE, "caller_id", [])
    svc = _Service(ctx)
    call = ToolCall(
        id="t1",
        name="save_caller_details",
        args={
            "name": "Hafiz",
            "vehicle_registration": "DMO 1106",
            "vehicle_model": "Proton Saga",
            "dealer": "Proton Petaling Jaya",
        },
    )
    bridge, live, _ = _bridge([call], service=svc, ctx=ctx)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    assert live.tool_responses[0] == (
        "t1",
        "save_caller_details",
        {"status": "saved", "dealer_matched": True},
    )
    await bridge.finalize()
    last = svc.saves[-1]
    assert last["ticket_id"] == "T-1"
    assert last["phone"] == PHONE
    assert last["details"] == CallerDetails(
        name="Hafiz", plate="DMO1106", model="Proton Saga", dealer_text="Proton Petaling Jaya"
    )
    assert last["dealer_slug"] == "petaling_jaya"
    assert last["contact_writes"] is True  # caller ID is the network's word


async def test_unmatched_dealer_is_text_only_and_never_labelled() -> None:
    ctx = CallerContext.from_candidates(PHONE, "caller_id", [])
    svc = _Service(ctx)
    calls = [
        ToolCall(id="t1", name="save_caller_details", args={"dealer": "Shah Alam service centre"}),
        ToolCall(id="t2", name="request_human_handoff", args={"reason": "r", "summary": "s"}),
    ]
    bridge, live, log = _bridge(calls, service=svc, ctx=ctx)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await bridge.finalize()
    assert live.tool_responses[0][2] == {"status": "saved", "dealer_matched": False}
    assert not any(tag.startswith("dealer_") for _, tag in log.tags)
    assert svc.saves[-1]["dealer_slug"] is None


async def test_handoff_never_labels_the_dealer_and_note_has_who_vehicle_dealer() -> None:
    """Controller ruling 9 Nov 2026: a handoff to customer service is not a
    dealer escalation, so no dealer_<slug> label; the confirmed dealer stays
    on the conversation (caller_dealer, via save)."""
    ctx = CallerContext.from_candidates(PHONE, "caller_id", [AHMAD])
    calls = [
        ToolCall(id="t1", name="save_caller_details", args={"dealer": "Petaling Jaya"}),
        ToolCall(
            id="t2",
            name="request_human_handoff",
            args={"reason": "booking", "summary": "wants service"},
        ),
    ]
    svc = _Service(ctx)
    bridge, _, log = _bridge(calls, service=svc, ctx=ctx)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await bridge.finalize()
    assert not any(tag.startswith("dealer_") for _, tag in log.tags)
    assert svc.saves[-1]["dealer_slug"] == "petaling_jaya"
    note = next(text for _, text, _ in log.comments if text.startswith("[Handoff"))
    assert "booking" in note and "wants service" in note
    assert "Caller: Ahmad bin Ali" in note
    assert "Vehicle: Proton X50 DMO1101" in note
    assert "Dealer: Petaling Jaya" in note


async def test_resolved_call_with_a_dealer_gets_no_dealer_label() -> None:
    ctx = CallerContext.from_candidates(
        PHONE, "caller_id", [{**AHMAD, "last_dealer": "petaling_jaya"}]
    )
    call = ToolCall(id="t1", name="save_caller_details", args={"dealer": "Petaling Jaya"})
    bridge, _, log = _bridge([call], service=_Service(ctx), ctx=ctx)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await bridge.finalize()
    assert not any(tag.startswith("dealer_") for _, tag in log.tags)


def test_ring_parameters_carry_caller_name_vehicle_and_dealer() -> None:
    ctx = CallerContext.from_candidates(
        PHONE, "caller_id", [{**AHMAD, "last_dealer": "petaling_jaya"}]
    )
    bridge, _, _ = _bridge([], service=_Service(ctx), ctx=ctx)
    bridge.ticket_id = "T-1"
    bridge.handoff = {"reason": "r", "summary": "s"}
    params = bridge._handoff_parameters()
    assert params["conversation_id"] == "T-1"
    assert params["caller"] == PHONE
    assert params["caller_name"] == "Ahmad bin Ali"
    assert params["vehicle"] == "Proton X50 DMO1101"
    assert params["dealer"] == "Petaling Jaya"


def test_ring_parameters_without_caller_service_are_unchanged() -> None:
    live = _FakeLive([])

    async def send(_: dict[str, object]) -> None:
        return None

    bridge = PhoneBridge(live, _FakeKnowledge(), _Log(), send, Settings())
    bridge.ticket_id = "T-1"
    bridge.handoff = {"reason": "r", "summary": "s"}
    assert bridge._handoff_parameters() == {"conversation_id": "T-1", "reason": "r", "summary": "s"}


# --- item 9: no caller ID ---------------------------------------------------


def _lookup(i: int, number: str, name: str | None = "Ahmad") -> ToolCall:
    args: dict[str, Any] = {"mobile_number": number}
    if name is not None:
        args["caller_name"] = name
    return ToolCall(id=f"l{i}", name="lookup_caller", args=args)


async def test_spoken_number_attaches_only_after_a_server_side_name_match() -> None:
    known = CallerContext.from_candidates(PHONE, "spoken", [AHMAD])
    svc = _Service(known)
    bridge, live, log = _bridge([_lookup(1, "000 000 0101", "ahmad")], service=svc, ctx=None)
    await bridge.handle_twilio(_start())
    await asyncio.sleep(0)
    assert log.customers == []  # waiting for the number
    await bridge.pump()
    assert svc.lookups == [(PHONE, "spoken")]
    _, name, resp = live.tool_responses[0]
    assert name == "lookup_caller"
    assert resp["status"] == "match"
    assert resp["vehicles"] == [{"model": "Proton X50", "plate_ending": "1101"}]
    # never the record's name, number or full plate for a spoken number
    text = repr(resp)
    assert "Ahmad" not in text and "first_name" not in text
    assert PHONE not in text and "DMO1101" not in text
    await bridge.finalize()
    assert log.customers == [("Ahmad bin Ali", PHONE)]
    assert svc.saves[0]["source"] == "spoken" and svc.saves[0]["contact_writes"] is True


async def test_spoken_number_with_the_wrong_name_stays_anonymous() -> None:
    known = CallerContext.from_candidates(PHONE, "spoken", [AHMAD])
    svc = _Service(known)
    bridge, live, log = _bridge([_lookup(1, "0000000101", "Siti")], service=svc, ctx=None)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    resp = live.tool_responses[0][2]
    assert resp["status"] == "no_match"
    assert set(resp) == {"status", "guidance"}  # nothing about the record leaks
    await bridge.finalize()
    assert log.customers == [(None, None)]
    # the told number is kept on the case as unverified, the contact untouched
    assert svc.saves[-1]["phone"] == PHONE
    assert svc.saves[-1]["contact_writes"] is False


async def test_unknown_number_and_wrong_name_look_the_same() -> None:
    svc = _Service()  # no record for the number
    bridge, live, _ = _bridge([_lookup(1, "0000000101")], service=svc, ctx=None)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    assert set(live.tool_responses[0][2]) == {"status", "guidance"}
    assert live.tool_responses[0][2]["status"] == "no_match"
    await bridge.finalize()


async def test_a_later_matching_name_confirms_the_told_number() -> None:
    known = CallerContext.from_candidates(PHONE, "spoken", [AHMAD])
    svc = _Service(known)
    calls = [
        _lookup(1, "0000000101", "Hmm"),
        ToolCall(id="s1", name="save_caller_details", args={"name": "Ahmad"}),
    ]
    bridge, _, log = _bridge(calls, service=svc, ctx=None)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await bridge.finalize()
    assert log.customers == [("Ahmad bin Ali", PHONE)]


async def test_a_later_lookup_drops_an_unattached_confirmation() -> None:
    known = CallerContext.from_candidates(PHONE, "spoken", [AHMAD])
    calls = [_lookup(1, "0000000101", "Ahmad"), _lookup(2, "0000000102", "Ahmad")]

    class _Two(_Service):
        async def lookup(self, phone: str, source: str) -> CallerContext:
            self.lookups.append((phone, source))
            return known if phone == PHONE else CallerContext(phone=phone, source=source)

    svc = _Two()
    bridge, live, log = _bridge(
        calls, service=svc, ctx=None, settings=_settings(phone_caller_number_wait_seconds=5)
    )
    # no "start" yet: the conversation is created only at finalize
    await bridge.pump()
    assert [r[2]["status"] for r in live.tool_responses] == ["match", "no_match"]
    await bridge.handle_twilio(_start())
    await bridge.finalize()
    assert log.customers == [(None, None)]


async def test_save_with_a_different_name_undoes_an_unattached_confirmation() -> None:
    known = CallerContext.from_candidates(PHONE, "spoken", [AHMAD])
    calls = [
        _lookup(1, "0000000101", "Ahmad"),
        ToolCall(id="s1", name="save_caller_details", args={"name": "Siti"}),
    ]
    bridge, _, log = _bridge(
        calls,
        service=_Service(known),
        ctx=None,
        settings=_settings(phone_caller_number_wait_seconds=5),
    )
    await bridge.pump()
    await bridge.handle_twilio(_start())
    await bridge.finalize()
    assert log.customers == [(None, None)]


async def test_lookup_caller_needs_the_callers_name() -> None:
    svc = _Service()
    bridge, live, _ = _bridge([_lookup(1, "0000000101", None)], service=svc, ctx=None)
    await bridge.pump()
    assert live.tool_responses[0][2]["status"] == "need_name"
    assert svc.lookups == []


async def test_rate_limited_number_answers_no_match_without_a_lookup() -> None:
    known = CallerContext.from_candidates(PHONE, "spoken", [AHMAD])
    svc = _Service(known)
    svc.allow = False
    bridge, live, _ = _bridge([_lookup(1, "0000000101")], service=svc, ctx=None)
    await bridge.pump()
    assert live.tool_responses[0][2]["status"] == "no_match"
    assert svc.lookups == []


def test_ring_marks_a_spoken_number_unverified() -> None:
    bridge, _, _ = _bridge([], service=_Service(), ctx=CallerContext(PHONE, "spoken"))
    bridge.handoff = {"reason": "r", "summary": "s"}
    assert bridge._handoff_parameters()["caller_unverified"] == "1"
    verified, _, _ = _bridge([], service=_Service(), ctx=CallerContext(PHONE, "caller_id"))
    verified.handoff = {"reason": "r", "summary": "s"}
    assert "caller_unverified" not in verified._handoff_parameters()


async def test_no_caller_id_creates_anonymously_after_the_wait() -> None:
    bridge, _, log = _bridge(
        [], service=_Service(), ctx=None, settings=_settings(phone_caller_number_wait_seconds=0.01)
    )
    await bridge.handle_twilio(_start())
    await asyncio.sleep(0.05)
    assert log.customers == [(None, None)]
    await bridge.finalize()


async def test_lookup_caller_rejects_a_non_number_and_caps_attempts() -> None:
    calls = [
        _lookup(i, n)
        for i, n in enumerate(["hello", "0000000101", "0000000102", "0000000103", "0000000104"])
    ]
    svc = _Service()
    bridge, live, _ = _bridge(calls, service=svc, ctx=None)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    statuses = [r[2]["status"] for r in live.tool_responses]
    assert statuses == ["invalid_number", "no_match", "no_match", "limit_reached", "limit_reached"]
    assert len(svc.lookups) == 2
    await bridge.finalize()


async def test_caller_tools_are_unknown_without_a_caller_service() -> None:
    live = _FakeLive(
        [ToolCall(id="t1", name="lookup_caller", args={"mobile_number": "0000000101"})]
    )

    async def send(_: dict[str, object]) -> None:
        return None

    bridge = PhoneBridge(live, _FakeKnowledge(), _Log(), send, Settings())
    await bridge.pump()
    assert live.tool_responses[0][2] == {"error": "unknown tool: lookup_caller"}


# --- brief 3: the structured handoff note (HANDOFF_NOTE_ENABLED) -------------


class _NoteLog(_Log):
    def __init__(self) -> None:
        super().__init__()
        self.priorities: list[tuple[str, str]] = []

    async def set_conversation_priority(self, ticket_id: str, priority: str) -> None:
        self.priorities.append((ticket_id, priority))


def _handoff_notes(log: _Log) -> list[str]:
    return [text for _, text, _ in log.comments if text.startswith("[Handoff")]


async def test_flag_on_posts_one_structured_note_at_the_handoff() -> None:
    ctx = CallerContext.from_candidates(PHONE, "caller_id", [AHMAD])
    calls = [
        ToolCall(id="t1", name="save_caller_details", args={"dealer": "Petaling Jaya"}),
        ToolCall(
            id="t2",
            name="request_human_handoff",
            args={"reason": "breakdown", "summary": "Car broke down on the highway."},
        ),
    ]
    log = _NoteLog()
    bridge, _, _ = _bridge(
        calls,
        service=_Service(ctx),
        ctx=ctx,
        log=log,
        settings=_settings(handoff_note_enabled=True),
    )
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await asyncio.sleep(0.01)
    # Posted during the call, before hang-up, with the transcript link.
    notes = _handoff_notes(log)
    assert len(notes) == 1
    note = notes[0]
    assert note.startswith("[Handoff to human agent] Phone call · Priority: URGENT")
    assert "Who: Ahmad bin Ali · +60•••••0101" in note
    assert PHONE not in note
    assert "Vehicle: Proton X50 · DMO1101" in note
    assert "Dealer: Petaling Jaya — escalate by adding the `escalate` label" in note
    assert "Live transcript: [open](/app/accounts/1/conversations/T-1)" in note
    assert log.priorities == [("T-1", "urgent")]
    assert ("T-1", "phone-CA1") in log.external_ids
    await bridge.finalize()
    assert len(_handoff_notes(log)) == 1  # finalize does not repeat it
    assert not any(tag.startswith("dealer_") for _, tag in log.tags)


async def test_flag_on_history_dealer_is_shown_unconfirmed() -> None:
    ctx = CallerContext.from_candidates(
        PHONE, "caller_id", [{**AHMAD, "last_dealer": "caroline_motor"}]
    )
    call = ToolCall(id="t1", name="request_human_handoff", args={"reason": "r", "summary": "s"})
    log = _NoteLog()
    bridge, _, _ = _bridge(
        [call],
        service=_Service(ctx),
        ctx=ctx,
        log=log,
        settings=_settings(handoff_note_enabled=True),
    )
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await bridge.finalize()
    note = _handoff_notes(log)[0]
    assert "Dealer: Caroline Motor (usual dealer from history, not confirmed)" in note
    assert log.priorities == [("T-1", "medium")]
    assert not any(tag.startswith("dealer_") for _, tag in log.tags)


async def test_flag_on_without_caller_context_still_uses_the_formatter() -> None:
    live = _FakeLive(
        [ToolCall(id="t1", name="request_human_handoff", args={"reason": "r", "summary": "s"})]
    )
    log = _NoteLog()

    async def send(_: dict[str, object]) -> None:
        return None

    bridge = PhoneBridge(
        live,
        _FakeKnowledge(),
        log,
        send,
        Settings(handoff_note_enabled=True, phone_transcript_live_enabled=True),
    )
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await bridge.finalize()
    notes = _handoff_notes(log)
    assert len(notes) == 1
    assert "Who: not identified" in notes[0]
