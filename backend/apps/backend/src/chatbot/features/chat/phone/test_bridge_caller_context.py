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


async def test_handoff_labels_the_matched_dealer_and_note_has_who_vehicle_dealer() -> None:
    ctx = CallerContext.from_candidates(PHONE, "caller_id", [AHMAD])
    calls = [
        ToolCall(id="t1", name="save_caller_details", args={"dealer": "Petaling Jaya"}),
        ToolCall(
            id="t2",
            name="request_human_handoff",
            args={"reason": "booking", "summary": "wants service"},
        ),
    ]
    bridge, _, log = _bridge(calls, service=_Service(ctx), ctx=ctx)
    await bridge.handle_twilio(_start())
    await bridge.pump()
    await bridge.finalize()
    assert ("T-1", "dealer_petaling_jaya") in log.tags
    assert log.tags.count(("T-1", "dealer_petaling_jaya")) == 1
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


async def test_no_caller_id_defers_the_conversation_until_the_number_is_told() -> None:
    known = CallerContext.from_candidates(PHONE, "spoken", [AHMAD])
    svc = _Service(known)
    call = ToolCall(id="t1", name="lookup_caller", args={"mobile_number": "000 000 0101"})
    bridge, live, log = _bridge([call], service=svc, ctx=None)
    await bridge.handle_twilio(_start())
    await asyncio.sleep(0)
    assert log.customers == []  # waiting for the number
    await bridge.pump()
    assert svc.lookups == [(PHONE, "spoken")]
    _, name, resp = live.tool_responses[0]
    assert name == "lookup_caller"
    assert resp["status"] == "match"
    assert resp["customers"][0]["first_name"] == "Ahmad"
    assert "say their name first" in resp["guidance"]
    assert PHONE not in repr(resp) and "DMO1101" not in repr(resp)
    await bridge.finalize()
    assert log.customers == [("Ahmad bin Ali", PHONE)]
    assert svc.saves and svc.saves[0]["source"] == "spoken"


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
        ToolCall(id=f"t{i}", name="lookup_caller", args={"mobile_number": n})
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
