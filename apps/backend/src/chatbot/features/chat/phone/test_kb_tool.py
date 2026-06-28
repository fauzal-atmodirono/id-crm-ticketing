from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.phone.kb_tool import KB_SEARCH_TOOL, dispatch_kb_search


class _FakeKnowledge:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        self.queries.append(query)
        return [KbArticle(title="Warranty", content="5 years", url="https://x/y")]


def test_tool_declares_kb_search() -> None:
    names = [fd.name for fd in (KB_SEARCH_TOOL.function_declarations or [])]
    assert "kb_search" in names


async def test_dispatch_runs_search_and_returns_results() -> None:
    kb = _FakeKnowledge()
    out = await dispatch_kb_search({"query": "battery warranty"}, kb)
    assert kb.queries == ["battery warranty"]
    assert out["results"]
    assert out["results"][0]["title"] == "Warranty"


async def test_dispatch_handles_missing_query() -> None:
    kb = _FakeKnowledge()
    out = await dispatch_kb_search({}, kb)
    assert out["results"] == []
    assert kb.queries == []
