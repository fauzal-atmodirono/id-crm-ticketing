from __future__ import annotations

from chatbot.features.assist.copilot_tools import COPILOT_TOOLS, ToolExecutor
from chatbot.features.chat.models import KbArticle


class _FakeCtx:
    async def get_transcript(self, conversation_id: str):
        return [{"sender": "customer", "private": False, "content": "hi"}]

    async def get_contact(self, conversation_id: str):
        return {"name": "Aiman", "custom_attributes": {"plan": "premium"}}

    async def list_contact_conversations(self, conversation_id: str):
        return [{"id": 30, "status": "resolved", "labels": ["billing"]}]


class _FakeKb:
    def __init__(self) -> None:
        self.last_limit: int | None = None

    async def search_kb(self, query: str, limit: int = 3):
        self.last_limit = limit
        return [KbArticle(title="Warranty", content="12 months", url="http://faq/1")]


def test_tool_schemas_have_four_named_functions() -> None:
    names = {t["name"] for t in COPILOT_TOOLS}
    assert names == {
        "get_conversation_transcript",
        "get_contact_details",
        "list_past_conversations",
        "search_knowledge_base",
    }
    for t in COPILOT_TOOLS:
        assert "description" in t and "parameters" in t


async def test_executor_dispatches_transcript() -> None:
    ex = ToolExecutor(_FakeCtx(), _FakeKb(), conversation_id="42")
    out = await ex.run("get_conversation_transcript", {})
    assert out["turns"][0]["content"] == "hi"


async def test_executor_dispatches_kb_search() -> None:
    ex = ToolExecutor(_FakeCtx(), _FakeKb(), conversation_id="42")
    out = await ex.run("search_knowledge_base", {"query": "warranty"})
    assert out["articles"][0]["title"] == "Warranty"


async def test_executor_clamps_kb_limit() -> None:
    kb = _FakeKb()
    ex = ToolExecutor(_FakeCtx(), kb, conversation_id="42")

    await ex.run("search_knowledge_base", {"query": "warranty", "limit": 50})
    assert kb.last_limit == 10

    await ex.run("search_knowledge_base", {"query": "warranty", "limit": 0})
    assert kb.last_limit == 1


async def test_executor_unknown_tool() -> None:
    ex = ToolExecutor(_FakeCtx(), _FakeKb(), conversation_id="42")
    out = await ex.run("nope", {})
    assert "error" in out
