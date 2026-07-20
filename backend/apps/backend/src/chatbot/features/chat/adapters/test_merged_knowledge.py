"""Tests for MergedKnowledgeAdapter — TDD: write test first, then implement."""
from __future__ import annotations

import pytest

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.ports import LiveFaqEntry


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    async def embed(self, text: str) -> list[float]:  # noqa: ARG002
        return [0.1, 0.2]


class _RaisingEmbedder:
    async def embed(self, text: str) -> list[float]:
        raise RuntimeError("embed exploded")


class _FakeLiveStore:
    def __init__(self, results: list[tuple[LiveFaqEntry, float]]) -> None:
        self._results = results

    async def search(
        self, query_embedding: list[float], limit: int  # noqa: ARG002
    ) -> list[tuple[LiveFaqEntry, float]]:
        return self._results


class _RaisingLiveStore:
    async def search(
        self, query_embedding: list[float], limit: int
    ) -> list[tuple[LiveFaqEntry, float]]:
        raise RuntimeError("store exploded")


class _FakeBase:
    def __init__(self, results: list[KbArticle]) -> None:
        self._results = results

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:  # noqa: ARG002
        return list(self._results)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_faq_ranks_first_and_dedups() -> None:
    """Live-FAQ results appear first; a live entry whose question matches a base
    title (case-insensitive) is deduplicated so only one appears."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    live_entry = LiveFaqEntry(
        id="e1",
        question="Battery warranty?",
        answer="24 months",
    )
    # A live entry whose question exactly matches a base title (case-insensitive)
    dup_entry = LiveFaqEntry(
        id="e2",
        question="Vertex Doc",
        answer="duplicate content",
    )

    fake_live = _FakeLiveStore(
        [(live_entry, 0.9), (dup_entry, 0.8)]
    )
    base_article = KbArticle(title="Vertex Doc", content="c", url="http://v/1")
    fake_base = _FakeBase([base_article])
    embedder = _FakeEmbedder()

    adapter = MergedKnowledgeAdapter(fake_base, fake_live, embedder)
    results = await adapter.search_kb("warranty", 3)

    # Live-FAQ result is first
    assert results[0].title == "Battery warranty?"
    assert results[0].source_type == "live_faq"

    # "Vertex Doc" appears exactly once (deduplicated)
    titles = [r.title for r in results]
    assert titles.count("Vertex Doc") == 1

    # Both "Battery warranty?" and "Vertex Doc" are present
    assert "Battery warranty?" in titles
    assert "Vertex Doc" in titles


@pytest.mark.asyncio
async def test_falls_back_to_base_when_no_embedder() -> None:
    """When embedder is None, live-FAQ is skipped and base results are returned."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    base_article = KbArticle(title="Base Article", content="content")
    fake_base = _FakeBase([base_article])

    adapter = MergedKnowledgeAdapter(fake_base, None, None)
    results = await adapter.search_kb("x", 3)

    assert len(results) == 1
    assert results[0].title == "Base Article"


@pytest.mark.asyncio
async def test_never_raises_on_live_failure() -> None:
    """When the live store raises, base articles are returned without error."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    base_article = KbArticle(title="Safe Article", content="safe content")
    fake_base = _FakeBase([base_article])

    adapter = MergedKnowledgeAdapter(fake_base, _RaisingLiveStore(), _FakeEmbedder())
    results = await adapter.search_kb("query", 3)

    assert len(results) == 1
    assert results[0].title == "Safe Article"


@pytest.mark.asyncio
async def test_never_raises_on_embedder_failure() -> None:
    """When the embedder raises, base articles are returned without error."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    base_article = KbArticle(title="Base Only", content="content")
    fake_base = _FakeBase([base_article])

    adapter = MergedKnowledgeAdapter(fake_base, _FakeLiveStore([]), _RaisingEmbedder())
    results = await adapter.search_kb("query", 3)

    assert len(results) == 1
    assert results[0].title == "Base Only"
