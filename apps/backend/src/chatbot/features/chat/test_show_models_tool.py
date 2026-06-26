from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatbot.features.chat.adapters.mock import InMemoryTicketingAdapter
from chatbot.features.chat.agents import build_ai_agent
from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.ports import KnowledgePort
from chatbot.platform.config import get_settings


class _DupModelsKnowledge(KnowledgePort):
    """Returns the same model page twice + raw scraped fields, like Vertex does."""

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        saga = KbArticle(
            title="PROTON - PROTON All-New Saga",
            content="5-YEAR Warranty   or 150,000km* 3 TIMES Free Labour Service*",
            url="https://proton.com/all-new-saga",
            source_type="model",
            price="RM 38,990",
        )
        saga2 = KbArticle(
            title="PROTON All-New Saga",
            content="duplicate page body",
            url="https://proton.com/models/all-new-saga",
            source_type="model",
            price="RM 38,990",
        )
        return [saga, saga2]


def _find_tool(agent: object, name: str) -> object:
    for tool in agent.tools:  # type: ignore[attr-defined]
        func = getattr(tool, "func", tool)
        if getattr(func, "__name__", "") == name:
            return func
    raise AssertionError(f"tool {name} not registered")


@pytest.mark.asyncio
async def test_show_models_tool_dedupes_and_cleans() -> None:
    agent = build_ai_agent(get_settings(), InMemoryTicketingAdapter(), _DupModelsKnowledge())
    tool = _find_tool(agent, "show_models_tool")
    ctx = SimpleNamespace(state={})

    await tool(ctx, query="models")  # type: ignore[operator]

    cards = ctx.state["product_carousel"]
    assert len(cards) == 1  # the duplicate Saga page is collapsed
    assert cards[0]["title"] == "PROTON All-New Saga"  # brand de-doubled
    assert "  " not in cards[0]["description"]  # whitespace collapsed
