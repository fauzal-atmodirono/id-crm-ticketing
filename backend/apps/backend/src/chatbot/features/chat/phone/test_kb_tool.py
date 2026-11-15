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


# --- 2026-11-16 call gaps: a lookup must never leave the caller in silence -----

import asyncio  # noqa: E402


class _Knowledge:
    def __init__(self, *, delay: float = 0.0, raises: bool = False) -> None:
        self._delay, self._raises = delay, raises

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("vertex down")
        return [KbArticle(title="Service", content="every 10,000 km", url="https://x/s")]


async def test_kb_search_over_the_cap_returns_empty_with_timeout_status() -> None:
    slow = _Knowledge(delay=0.2)
    out = await dispatch_kb_search({"query": "x"}, slow, timeout_seconds=0.05)
    assert out == {"results": [], "status": "timeout"}


async def test_zero_cap_waits_as_before() -> None:
    out = await dispatch_kb_search({"query": "x"}, _Knowledge(delay=0.05), timeout_seconds=0.0)
    assert out["results"]
    assert "status" not in out


async def test_kb_search_error_returns_empty_results() -> None:
    out = await dispatch_kb_search({"query": "x"}, _Knowledge(raises=True))
    assert out == {"results": [], "status": "error"}
