"""2026-11-09 brief 3: with handoff_note_enabled the handoff tool also takes
a summary for the agent and the dealer the customer named; flag off keeps
the one-argument tool exactly as it was."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from chatbot.features.chat.adapters.mock import (
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
)
from chatbot.features.chat.agents import build_ai_agent
from chatbot.features.chat.test_flag_for_ticket_tool import _find_tool
from chatbot.platform.config import Settings


def _tool(**kw):
    agent = build_ai_agent(
        Settings(_env_file=None, **kw), InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter()
    )
    return _find_tool(agent, "emit_handoff_tool")


def test_flag_off_tool_is_unchanged() -> None:
    params = list(inspect.signature(_tool()).parameters)
    assert params == ["tool_context", "reason"]


async def test_flag_on_tool_records_summary_and_dealer() -> None:
    tool = _tool(handoff_note_enabled=True)
    assert list(inspect.signature(tool).parameters) == [
        "tool_context",
        "reason",
        "summary",
        "dealer",
    ]
    ctx = SimpleNamespace(state={})
    out = await tool(ctx, reason="help_request", summary="Wants a callback.", dealer="PJ")
    assert ctx.state == {
        "handoff_triggered": True,
        "handoff_reason": "help_request",
        "handoff_summary": "Wants a callback.",
        "handoff_dealer": "PJ",
        "handoff_seq": 1,
    }
    assert "help_request" in out


async def test_flag_on_tool_works_with_reason_only() -> None:
    ctx = SimpleNamespace(state={"handoff_summary": "old", "handoff_seq": 2})
    await _tool(handoff_note_enabled=True)(ctx, reason="help_request")
    assert ctx.state == {
        "handoff_triggered": True,
        "handoff_reason": "help_request",
        "handoff_seq": 3,
    }
