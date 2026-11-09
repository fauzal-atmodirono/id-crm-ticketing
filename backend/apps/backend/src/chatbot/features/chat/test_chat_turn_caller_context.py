"""Brief 6 (R47): /chat/turn and /voice/turn know the customer, once per session.

Drives the real OrchestratorService and asserts on the instruction the ADK
InstructionProvider actually served (same probe idea as
test_turn_instruction_wiring.py). Chatwoot is never touched: the lookup is an
injected fake. All numbers are fake (+600000000xx).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.phone.caller_context import CallerContext
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.contact_context.personal import PRIVACY_FLOOR
from chatbot.platform.config import get_settings

AHMAD = "+60000000101"
NEW = "+60000000105"
STRANGER = "+60000000999"


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    get_settings().session_store = "memory"


class _Event:
    def __init__(self, text: str) -> None:
        self.content = SimpleNamespace(parts=[SimpleNamespace(text=text)])

    def is_final_response(self) -> bool:
        return True


class _Probe:
    def __init__(self, svc: OrchestratorService, served: list[str]) -> None:
        self._svc = svc
        self._served = served

    async def run_async(
        self, *, user_id: str, session_id: str, new_message: Any, **_: Any
    ) -> AsyncIterator[_Event]:
        state = self._svc._adk_sessions.sessions["chatbot"][session_id][session_id].state  # type: ignore[attr-defined]
        ctx = SimpleNamespace(
            session=SimpleNamespace(id=session_id),
            state=MappingProxyType(state),
            user_content=new_message,
            invocation_id="inv-1",
        )
        self._served.append(self._svc._chat_instruction_provider(ctx))
        yield _Event("ok")


class _Lookup:
    def __init__(self, mode: str = "ok") -> None:
        self.calls: list[str] = []
        self.mode = mode

    async def __call__(self, phone: str) -> CallerContext:
        self.calls.append(phone)
        if self.mode == "raise":
            raise RuntimeError("chatwoot down")
        if self.mode == "slow":
            await asyncio.sleep(5)
        if self.mode == "failed":
            return CallerContext(phone=phone, source="caller_id", failed=True)
        if phone == AHMAD:
            return CallerContext.from_candidates(
                phone,
                "caller_id",
                [
                    {
                        "contact_id": 7,
                        "name": "[DEMO] Ahmad bin Example",
                        "vehicles": [{"plate": "DMO1101", "model": "Proton X50"}],
                        "last_dealer": "petaling_jaya",
                    }
                ],
            )
        return CallerContext(phone=phone, source="caller_id")


_P7_FLAGS_OFF = {
    "sentiment_classifier_enabled": False,
    "sentiment_tone_adjustment_enabled": False,
    "media_diagnosis_prompt_enabled": False,
}


def _settings(**kw: Any) -> Any:
    base = {
        **_P7_FLAGS_OFF,
        "chat_turn_caller_context_enabled": True,
        "phone_web_call_as_enabled": True,
        "phone_web_call_as_numbers": f"{AHMAD},{NEW}",
        "phone_caller_lookup_timeout_seconds": 0.05,
    }
    base.update(kw)
    return get_settings().model_copy(update=base)


def _svc(settings: Any, lookup: _Lookup | None) -> tuple[OrchestratorService, list[str]]:
    served: list[str] = []
    holder: dict[str, Any] = {}
    svc = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _Probe(holder["svc"], served),
        caller_context_lookup=lookup,
    )
    holder["svc"] = svc
    return svc, served


async def _baseline(session_id: str = "sim-1") -> str:
    svc, served = _svc(_settings(chat_turn_caller_context_enabled=False), None)
    await svc.handle_turn(session_id=session_id, text="hello")
    return served[-1]


# --- chat --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whatsapp_sender_gets_the_personal_paragraph() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await svc.handle_turn(session_id=f"whatsapp-{AHMAD}", text="hi", customer_phone=AHMAD)
    assert lookup.calls == [AHMAD]
    assert "Hi Ahmad" in served[-1] and "Proton X50" in served[-1]
    assert PRIVACY_FLOOR in served[-1]
    for secret in (AHMAD, "60000000101", "DMO1101", "Example"):
        assert secret not in served[-1]


@pytest.mark.asyncio
async def test_a_session_id_alone_never_claims_a_number() -> None:
    """The browser picks /chat/turn session ids: "whatsapp-<number>" from it
    must not unlock that customer's record."""
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await svc.handle_turn(session_id=f"whatsapp-{AHMAD}", text="hi")
    assert lookup.calls == []
    assert served[-1] == await _baseline(f"whatsapp-{AHMAD}")


@pytest.mark.asyncio
async def test_allowlisted_console_persona_gets_context() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone="0000000101")
    assert lookup.calls == [AHMAD]
    assert "Hi Ahmad" in served[-1]


@pytest.mark.asyncio
async def test_unknown_persona_number_is_served_as_a_new_customer() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone=NEW)
    assert "NEW customer" in served[-1]


@pytest.mark.asyncio
async def test_non_allowlisted_number_is_ignored() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone=STRANGER)
    assert lookup.calls == []
    assert served[-1] == await _baseline()


@pytest.mark.asyncio
async def test_persona_needs_the_web_call_as_flag() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(phone_web_call_as_enabled=False), lookup)
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone=AHMAD)
    assert lookup.calls == []
    assert served[-1] == await _baseline()


@pytest.mark.asyncio
async def test_flag_off_is_byte_identical() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(chat_turn_caller_context_enabled=False), lookup)
    await svc.handle_turn(
        session_id="sim-1", text="hello", customer_phone=AHMAD, persona_phone=AHMAD
    )
    assert lookup.calls == []
    assert served[-1] == await _baseline()


@pytest.mark.parametrize("mode", ["raise", "slow", "failed"])
@pytest.mark.asyncio
async def test_lookup_failure_or_timeout_adds_nothing(mode: str) -> None:
    lookup = _Lookup(mode)
    svc, served = _svc(_settings(), lookup)
    await asyncio.wait_for(
        svc.handle_turn(session_id="sim-1", text="hello", customer_phone=AHMAD), timeout=3
    )
    assert served[-1] == await _baseline()


@pytest.mark.asyncio
async def test_resolved_once_per_session() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone=AHMAD)
    await svc.handle_turn(session_id="sim-1", text="and again", persona_phone=AHMAD)
    assert lookup.calls == [AHMAD]
    assert "Hi Ahmad" in served[-1]
    # a new session (the console resets it on a persona change) looks up again
    await svc.handle_turn(session_id="sim-2", text="hi", persona_phone=NEW)
    assert lookup.calls == [AHMAD, NEW]
    assert "NEW customer" in served[-1]


# --- voice -------------------------------------------------------------------


async def _voice(svc: OrchestratorService, **kw: Any) -> None:
    async def _transcribe(**_: Any) -> str:
        return "hello"

    svc._transcribe_audio = _transcribe  # type: ignore[method-assign,assignment]
    await svc.handle_voice_turn(session_id="voice-1", audio_bytes=b"\x00" * 64, **kw)


@pytest.mark.asyncio
async def test_voice_allowlisted_persona_gets_context() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await _voice(svc, persona_phone=AHMAD)
    assert lookup.calls == [AHMAD]
    assert "Hi Ahmad" in served[-1]


@pytest.mark.asyncio
async def test_voice_non_allowlisted_or_flag_off_gets_nothing() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    await _voice(svc, persona_phone=STRANGER)
    off, off_served = _svc(_settings(chat_turn_caller_context_enabled=False), lookup)
    await _voice(off, persona_phone=AHMAD)
    assert lookup.calls == []
    assert served[-1] == off_served[-1]
    assert "CUSTOMER CONTEXT" not in served[-1]


# --- fix round 1: bookings (R51), cache TTL (R52) ------------------------------


class _StateProbe(_Probe):
    def __init__(self, svc: OrchestratorService, served: list[str], deltas: list[Any]) -> None:
        super().__init__(svc, served)
        self._deltas = deltas

    async def run_async(self, **kw: Any) -> AsyncIterator[_Event]:  # type: ignore[override]
        self._deltas.append(kw.get("state_delta"))
        async for ev in super().run_async(**kw):
            yield ev


def _svc_deltas(settings: Any, lookup: _Lookup) -> tuple[OrchestratorService, list[str], list[Any]]:
    served: list[str] = []
    deltas: list[Any] = []
    holder: dict[str, Any] = {}
    svc = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _StateProbe(holder["svc"], served, deltas),
        caller_context_lookup=lookup,
    )
    holder["svc"] = svc
    return svc, served, deltas


@pytest.mark.asyncio
async def test_recognised_trusted_session_hands_the_number_to_bookings() -> None:
    svc, served, deltas = _svc_deltas(_settings(), _Lookup())
    await svc.handle_turn(session_id="sim-1", text="book a test drive", persona_phone=AHMAD)
    assert deltas[-1] == {"caller_trusted_phone": AHMAD}
    assert "already have their phone number" in served[-1]
    assert AHMAD not in served[-1]


@pytest.mark.asyncio
async def test_anonymous_and_unknown_sessions_hand_over_no_number() -> None:
    svc, served, deltas = _svc_deltas(_settings(), _Lookup())
    await svc.handle_turn(session_id="sim-1", text="book a test drive")
    await svc.handle_turn(session_id="sim-2", text="book a test drive", persona_phone=NEW)
    assert deltas == [None, None]
    assert "already have their phone number" not in served[-1]


@pytest.mark.asyncio
async def test_book_test_drive_fills_an_empty_phone_from_the_trusted_number() -> None:

    svc, _, _ = _svc_deltas(_settings(), _Lookup())
    tool = next(
        getattr(t, "func", t)
        for t in svc._support_agent.tools
        if getattr(getattr(t, "func", t), "__name__", "") == "book_test_drive_tool"
    )
    state: dict[str, Any] = {"caller_trusted_phone": AHMAD}
    await tool(SimpleNamespace(state=state), "Ahmad", "", "a@example.test", "X50", "PJ")
    assert state["lead_details"]["customer_phone"] == AHMAD
    # what the customer gave wins; nothing trusted -> unchanged
    await tool(SimpleNamespace(state=state), "Ahmad", "+60000000199", "", "X50", "PJ")
    assert state["lead_details"]["customer_phone"] == "+60000000199"
    anon: dict[str, Any] = {}
    await tool(SimpleNamespace(state=anon), "Ahmad", "", "", "X50", "PJ")
    assert anon["lead_details"]["customer_phone"] == ""


@pytest.mark.asyncio
async def test_cache_entry_expires_after_thirty_minutes() -> None:
    lookup = _Lookup()
    svc, served = _svc(_settings(), lookup)
    now = [1000.0]
    svc._caller_clock = lambda: now[0]  # type: ignore[method-assign]
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone=AHMAD)
    now[0] += 29 * 60
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone=AHMAD)
    assert lookup.calls == [AHMAD]
    now[0] += 2 * 60
    await svc.handle_turn(session_id="sim-1", text="hi", persona_phone=AHMAD)
    assert lookup.calls == [AHMAD, AHMAD]
    assert "Hi Ahmad" in served[-1]
