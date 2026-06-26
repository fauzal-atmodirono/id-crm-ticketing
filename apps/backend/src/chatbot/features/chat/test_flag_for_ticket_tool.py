from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
)
from chatbot.features.chat.agents import build_ai_agent
from chatbot.platform.config import get_settings


def _find_tool(agent: object, name: str) -> object:
    for tool in agent.tools:  # type: ignore[attr-defined]
        func = getattr(tool, "func", tool)
        if getattr(func, "__name__", "") == name:
            return func
    raise AssertionError(f"tool {name} not registered")


@pytest.mark.asyncio
async def test_flag_for_ticket_tool_sets_state() -> None:
    agent = build_ai_agent(
        get_settings(), InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter()
    )
    tool = _find_tool(agent, "flag_for_ticket_tool")
    ctx = SimpleNamespace(state={})

    result = await tool(ctx, reason="complaint")  # type: ignore[operator]

    assert ctx.state["ticket_flagged"] is True
    assert ctx.state["ticket_reason"] == "complaint"
    assert "complaint" in result
