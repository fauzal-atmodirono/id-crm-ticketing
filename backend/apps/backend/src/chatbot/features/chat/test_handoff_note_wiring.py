"""Brief 3 wiring: the chat handoff paths call the one handoff-note formatter.

- crm-<id> sessions (every Chatwoot inbox via the agent service's /chat/turn):
  one note per handoff, on conversation <id>, never one per later turn.
- begin_handoff (direct Twilio WhatsApp + email): the structured note replaces
  the old "[Handoff to human agent]\\n<reply>" text when the service writes to
  the same Chatwoot the conversation log uses.
- No service (flag off): today's behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.test_service import _FakeEvent
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def _memory_sessions() -> None:
    get_settings().session_store = "memory"


class _Notes:
    """Records post_for_chat calls (the HandoffNoteService contract)."""

    def __init__(self, chatwoot: Any = None) -> None:
        self.chatwoot = chatwoot
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def post_for_chat(self, conversation_id: str, state: Any, **kw: Any) -> None:
        self.calls.append((conversation_id, dict(state), kw))
        if kw.get("post") is not None:
            await kw["post"]("STRUCTURED NOTE")


class _Runner:
    def __init__(self, svc: OrchestratorService, sid: str, *, handoff: bool) -> None:
        self._svc, self._sid, self._handoff = svc, sid, handoff

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        if self._handoff:
            s = self._svc._adk_sessions.sessions["chatbot"][self._sid][self._sid]
            s.state["handoff_triggered"] = True
            s.state["handoff_reason"] = "help_request"
            s.state["handoff_summary"] = "Wants a person."
            s.state["handoff_seq"] = int(s.state.get("handoff_seq") or 0) + 1
        yield _FakeEvent("partial")


def _svc(notes: Any, sid: str, plan: list[bool], log: Any = None) -> OrchestratorService:
    it = iter(plan)
    holder: dict[str, OrchestratorService] = {}

    def factory(_agent: Any) -> _Runner:
        return _Runner(holder["svc"], sid, handoff=next(it, False))

    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=factory,
        conversation_log_port=log,
        handoff_note_service=notes,
    )
    holder["svc"] = svc
    return svc


async def test_crm_handoff_posts_one_note_on_its_conversation() -> None:
    notes = _Notes()
    svc = _svc(notes, "crm-42", [True, False, True])
    r1 = await svc.handle_turn(session_id="crm-42", text="human please")
    assert r1.handoff is not None
    assert len(notes.calls) == 1
    conv, state, _ = notes.calls[0]
    assert conv == "42"
    assert state["handoff_summary"] == "Wants a person."
    # Later turn: handoff_triggered is still set on a crm- session, but no
    # new handoff happened -> no second note.
    await svc.handle_turn(session_id="crm-42", text="hello?")
    assert len(notes.calls) == 1
    # A genuine new handoff gets its own note.
    await svc.handle_turn(session_id="crm-42", text="again a human")
    assert len(notes.calls) == 2


async def test_crm_handoff_without_service_posts_nothing() -> None:
    svc = _svc(None, "crm-43", [True])
    result = await svc.handle_turn(session_id="crm-43", text="human please")
    assert result.handoff is not None  # today's behaviour


class _Log:
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []
        self.statuses: list[Any] = []

    async def ensure_conversation_ticket(self, **kw: Any) -> str:
        return "77"

    async def append_conversation_comment(self, ticket_id: str, text: str, status: Any = None):
        self.comments.append((ticket_id, text))
        self.statuses.append(status)


async def test_begin_handoff_uses_the_structured_note_on_the_same_chatwoot() -> None:
    log = _Log()
    notes = _Notes(chatwoot=log)
    svc = _svc(notes, "whatsapp-+60000000101", [True], log=log)
    await svc.handle_turn(session_id="whatsapp-+60000000101", text="human")
    await svc.begin_whatsapp_handoff(
        "whatsapp-+60000000101",
        "Customer requested a human agent.",
        customer_name="Demo",
        customer_phone="+60000000101",
    )
    # The old one-line note is replaced by the structured one, still written
    # as the comment that reopens the conversation.
    assert log.comments == [("77", "STRUCTURED NOTE")]
    assert log.statuses == ["open"]
    conv, _, kw = notes.calls[-1]
    assert conv == "77"
    assert kw["channel"] == "WhatsApp"
    assert kw["customer_phone"] == "+60000000101"
    assert kw["fallback_summary"] == "Customer requested a human agent."


async def test_begin_handoff_keeps_the_old_note_when_the_log_is_elsewhere() -> None:
    log = _Log()
    notes = _Notes(chatwoot=object())  # e.g. Zendesk logs, Chatwoot notes
    svc = _svc(notes, "email-9", [True], log=log)
    await svc.handle_turn(session_id="email-9", text="human")
    await svc.begin_handoff("email-9", "s", channel="email")
    assert log.comments == [("77", "[Handoff to human agent]\ns")]
    assert notes.calls == []


async def test_a_later_crm_turn_with_handoff_still_set_posts_no_second_note() -> None:
    notes = _Notes()
    svc = _svc(notes, "crm-44", [True, False, False])
    await svc.handle_turn(session_id="crm-44", text="human please")
    r2 = await svc.handle_turn(session_id="crm-44", text="still there?")
    r3 = await svc.handle_turn(session_id="crm-44", text="hello")
    assert r2.handoff is not None and r3.handoff is not None  # still handed off
    assert len(notes.calls) == 1
