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
        self,
        query_embedding: list[float],  # noqa: ARG002
        limit: int,  # noqa: ARG002
        *,
        query_text: str | None = None,
    ) -> list[tuple[LiveFaqEntry, float]]:
        self.last_query_text = query_text
        return self._results


class _RaisingLiveStore:
    async def search(
        self,
        query_embedding: list[float],
        limit: int,
        *,
        query_text: str | None = None,
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

    fake_live = _FakeLiveStore([(live_entry, 0.9), (dup_entry, 0.8)])
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


@pytest.mark.asyncio
async def test_base_result_survives_when_priority_sources_would_fill_limit() -> None:
    """The exact demo symptom: several loosely-matching live-FAQ hits must not
    crowd out a genuinely relevant base (Vertex) result."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    fake_live = _FakeLiveStore(
        [
            (LiveFaqEntry(id="e1", question="Q1", answer="A1"), 0.6),
            (LiveFaqEntry(id="e2", question="Q2", answer="A2"), 0.6),
        ]
    )
    fake_base = _FakeBase([KbArticle(title="iMAS 5 specs", content="...", source_type="vertex")])
    embedder = _FakeEmbedder()

    adapter = MergedKnowledgeAdapter(fake_base, fake_live, embedder, pg_port=None)
    results = await adapter.search_kb("imas 5 specs", limit=2)

    titles = [r.title for r in results]
    assert "iMAS 5 specs" in titles  # base result must survive truncation


@pytest.mark.asyncio
async def test_priority_sources_still_win_their_reserved_slots() -> None:
    """pg/live ranking first (within their budget) must still hold — this fix
    reserves room for base, it does not invert the intentional priority."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    fake_pg = _FakeBase([KbArticle(title="PG hit", content="...", source_type="pgvector")])
    fake_live = _FakeLiveStore([])
    fake_base = _FakeBase(
        [
            KbArticle(title="Base 1", content="...", source_type="vertex"),
            KbArticle(title="Base 2", content="...", source_type="vertex"),
        ]
    )
    embedder = _FakeEmbedder()

    adapter = MergedKnowledgeAdapter(fake_base, fake_live, embedder, pg_port=fake_pg)
    results = await adapter.search_kb("query", limit=2)

    assert results[0].title == "PG hit"  # priority source still first


@pytest.mark.asyncio
async def test_dedup_by_title_across_two_pass_merge() -> None:
    """A title appearing in both a priority source and base must not appear
    twice or consume two slots."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    fake_pg = _FakeBase(
        [KbArticle(title="Shared Title", content="pg version", source_type="pgvector")]
    )
    fake_live = _FakeLiveStore([])
    fake_base = _FakeBase(
        [
            KbArticle(title="Shared Title", content="base version", source_type="vertex"),
            KbArticle(title="Unique Base", content="...", source_type="vertex"),
        ]
    )
    embedder = _FakeEmbedder()

    adapter = MergedKnowledgeAdapter(fake_base, fake_live, embedder, pg_port=fake_pg)
    results = await adapter.search_kb("query", limit=3)

    titles = [r.title for r in results]
    assert titles.count("Shared Title") == 1
    assert "Unique Base" in titles


@pytest.mark.asyncio
async def test_leftover_priority_hits_top_up_when_base_is_short() -> None:
    """If base has fewer results than its reserved budget, remaining slots
    come from leftover pg/live hits rather than being left empty."""
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    fake_pg = _FakeBase(
        [
            KbArticle(title="PG 1", content="...", source_type="pgvector"),
            KbArticle(title="PG 2", content="...", source_type="pgvector"),
            KbArticle(title="PG 3", content="...", source_type="pgvector"),
        ]
    )
    fake_live = _FakeLiveStore([])
    fake_base = _FakeBase([])  # base has nothing
    embedder = _FakeEmbedder()

    adapter = MergedKnowledgeAdapter(fake_base, fake_live, embedder, pg_port=fake_pg)
    results = await adapter.search_kb("query", limit=3)

    assert len(results) == 3  # all 3 slots filled from pg, not left short
