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


def _classify_tool(taxonomy_json: str) -> object:
    settings = get_settings()
    settings = settings.model_copy(update={"case_taxonomy_json": taxonomy_json})
    agent = build_ai_agent(settings, InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter())
    return _find_tool(agent, "classify_ticket_tool")


VALID = '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}'


@pytest.mark.asyncio
async def test_valid_category_and_subcategory_written() -> None:
    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    await tool(
        ctx, category="sales", subcategory="Test Drive Booking", priority="HIGH", sla_minutes=60
    )  # type: ignore[operator]

    assert ctx.state["category"] == "sales"
    assert ctx.state["subcategory"] == "Test Drive Booking"
    assert ctx.state["priority"] == "HIGH"
    assert ctx.state["sla_minutes"] == 60


@pytest.mark.asyncio
async def test_invalid_category_not_written_but_priority_still_is() -> None:
    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    await tool(ctx, category="not_a_real_category", subcategory="x", priority="LOW", sla_minutes=30)  # type: ignore[operator]

    assert "category" not in ctx.state
    assert "subcategory" not in ctx.state
    assert ctx.state["priority"] == "LOW"
    assert ctx.state["sla_minutes"] == 30


@pytest.mark.asyncio
async def test_valid_category_invalid_subcategory_neither_written() -> None:
    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    await tool(ctx, category="sales", subcategory="Not A Real Sub", priority="LOW", sla_minutes=30)  # type: ignore[operator]

    assert "category" not in ctx.state
    assert "subcategory" not in ctx.state


@pytest.mark.asyncio
async def test_empty_taxonomy_falls_back_to_accepting_free_text() -> None:
    tool = _classify_tool("")
    ctx = SimpleNamespace(state={})

    await tool(ctx, category="Anything", subcategory="Whatever", priority="LOW", sla_minutes=30)  # type: ignore[operator]

    assert ctx.state["category"] == "Anything"
    assert ctx.state["subcategory"] == "Whatever"
