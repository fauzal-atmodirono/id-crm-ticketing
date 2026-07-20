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
from chatbot.features.chat.service import (
    _EMPTY_REPLY_FALLBACK,
    _TECH_ERROR_FALLBACK,
    OrchestratorService,
)
from chatbot.features.metrics.events import TurnEvent
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    get_settings().session_store = "memory"


class _CapturingMetrics:
    def __init__(self) -> None:
        self.events: list[TurnEvent] = []

    async def emit_turn(self, event: TurnEvent) -> None:
        self.events.append(event)


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)

    def is_final_response(self) -> bool:
        return True


class _FakeRunner:
    def __init__(
        self, reply: str, session_service: Any, session_id: str, trigger_handoff: bool = False
    ) -> None:
        self._reply = reply
        self._session_service = session_service
        self._session_id = session_id
        self._trigger_handoff = trigger_handoff

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        if self._trigger_handoff:
            session = self._session_service.sessions["chatbot"][self._session_id][self._session_id]
            session.state["handoff_triggered"] = True
            session.state["handoff_reason"] = "help_request"
        yield _FakeEvent(self._reply)


class _RaisingRunner:
    """Runner whose run_async raises before yielding — simulates ADK hard failure."""

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        raise RuntimeError("adk boom")
        yield _FakeEvent("")  # type: ignore[unreachable]


def _service(
    metrics: _CapturingMetrics, reply: str, session_id: str, *, handoff: bool = False
) -> OrchestratorService:
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        metrics_port=metrics,
        runner_factory=lambda _a: _FakeRunner(
            reply=reply,
            session_service=svc._adk_sessions,
            session_id=session_id,
            trigger_handoff=handoff,
        ),
    )
    return svc


async def test_normal_turn_emits_non_fallback_first_turn() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "Here is your answer.", "sim-a")
    await svc.handle_turn(session_id="sim-a", text="hello")
    assert len(metrics.events) == 1
    ev = metrics.events[0]
    assert ev.channel == "Web"
    assert ev.is_fallback is False
    assert ev.handed_off is False
    assert ev.is_first_turn is True
    assert ev.latency_ms >= 0


async def test_empty_reply_emits_fallback() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "", "sim-b")  # empty reply → retry → _EMPTY_REPLY_FALLBACK
    result = await svc.handle_turn(session_id="sim-b", text="hello")
    assert result.reply == _EMPTY_REPLY_FALLBACK
    assert metrics.events[-1].is_fallback is True


async def test_handoff_turn_emits_handed_off() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "ok", "sim-c", handoff=True)
    await svc.handle_turn(session_id="sim-c", text="I want a human")
    ev = metrics.events[-1]
    assert ev.handed_off is True
    assert ev.is_fallback is False


async def test_second_turn_is_not_first() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "answer", "sim-d")
    await svc.handle_turn(session_id="sim-d", text="one")
    await svc.handle_turn(session_id="sim-d", text="two")
    assert metrics.events[0].is_first_turn is True
    assert metrics.events[1].is_first_turn is False


async def test_tech_error_emits_fallback_event() -> None:
    """handle_turn except-branch: ADK raises → TurnEvent(is_fallback=True, handed_off=False)."""
    metrics = _CapturingMetrics()
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        metrics_port=metrics,
        runner_factory=lambda _a: _RaisingRunner(),
    )
    result = await svc.handle_turn(session_id="sim-e", text="trigger error")
    assert result.reply == _TECH_ERROR_FALLBACK
    assert len(metrics.events) == 1
    ev = metrics.events[0]
    assert ev.is_fallback is True
    assert ev.handed_off is False
    assert ev.is_first_turn is True
