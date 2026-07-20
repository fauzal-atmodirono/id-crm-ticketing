"""Tests for the live-FAQ store: embedding on write, CRUD, cosine ranking.

The embedder is mocked (no Vertex call). We exercise the InMemoryLiveFaqStore,
which shares the create/update/search logic with the Firestore store.
"""

from __future__ import annotations

import pytest

from chatbot.features.chat.adapters.live_faq import (
    InMemoryLiveFaqStore,
    cosine_similarity,
)
from chatbot.features.chat.ports import LiveFaqEntry


class FakeEmbedder:
    """Maps known text substrings to fixed vectors so cosine is deterministic."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        lowered = text.lower()
        if "battery" in lowered:
            return [1.0, 0.0, 0.0]
        if "tyre" in lowered or "tire" in lowered:
            return [0.0, 1.0, 0.0]
        if "warranty" in lowered:
            return [0.0, 0.0, 1.0]
        return [0.3, 0.3, 0.3]


@pytest.mark.asyncio
async def test_create_computes_and_stores_embedding() -> None:
    embedder = FakeEmbedder()
    store = InMemoryLiveFaqStore(embedder)

    entry_id = await store.create(
        LiveFaqEntry(id="", question="How to reset the battery?", answer="Hold the button.")
    )

    assert entry_id
    stored = (await store.list_all())[0]
    assert stored.embedding == [1.0, 0.0, 0.0]
    assert stored.updated_at != ""
    # Embedded on "question + answer".
    assert "battery" in embedder.calls[0].lower()


@pytest.mark.asyncio
async def test_update_recomputes_embedding_when_text_changes() -> None:
    embedder = FakeEmbedder()
    store = InMemoryLiveFaqStore(embedder)
    entry_id = await store.create(
        LiveFaqEntry(id="", question="battery question", answer="battery answer")
    )

    await store.update(entry_id, {"question": "tyre pressure", "answer": "tyre answer"})

    stored = (await store.list_all())[0]
    assert stored.embedding == [0.0, 1.0, 0.0]
    assert stored.question == "tyre pressure"


@pytest.mark.asyncio
async def test_update_metadata_only_does_not_recompute_embedding() -> None:
    embedder = FakeEmbedder()
    store = InMemoryLiveFaqStore(embedder)
    entry_id = await store.create(
        LiveFaqEntry(id="", question="battery question", answer="battery answer")
    )
    calls_before = len(embedder.calls)

    await store.update(entry_id, {"tags": ["priority"], "active": False})

    assert len(embedder.calls) == calls_before  # no re-embed
    stored = (await store.list_all())[0]
    assert stored.tags == ["priority"]
    assert stored.active is False
    assert stored.embedding == [1.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_delete_removes_entry() -> None:
    store = InMemoryLiveFaqStore(FakeEmbedder())
    entry_id = await store.create(LiveFaqEntry(id="", question="battery", answer="x"))

    await store.delete(entry_id)

    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_list_active_filters_inactive() -> None:
    store = InMemoryLiveFaqStore(FakeEmbedder())
    active_id = await store.create(LiveFaqEntry(id="", question="battery", answer="x", active=True))
    await store.create(LiveFaqEntry(id="", question="tyre", answer="y", active=False))

    active = await store.list_active()

    assert [e.id for e in active] == [active_id]
    assert len(await store.list_all()) == 2


@pytest.mark.asyncio
async def test_search_ranks_closest_entry_first() -> None:
    embedder = FakeEmbedder()
    store = InMemoryLiveFaqStore(embedder)
    await store.create(LiveFaqEntry(id="", question="battery reset", answer="battery"))
    await store.create(LiveFaqEntry(id="", question="tyre change", answer="tyre"))
    await store.create(LiveFaqEntry(id="", question="warranty terms", answer="warranty"))

    # Query embedding aligned with the "battery" axis.
    results = await store.search([1.0, 0.0, 0.0], limit=3)

    assert results, "expected at least one hit above the score floor"
    top_entry, top_score = results[0]
    assert "battery" in top_entry.question.lower()
    assert top_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_search_empty_query_embedding_returns_empty() -> None:
    store = InMemoryLiveFaqStore(FakeEmbedder())
    await store.create(LiveFaqEntry(id="", question="battery", answer="x"))
    assert await store.search([], limit=3) == []


def test_cosine_similarity_edge_cases() -> None:
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0  # length mismatch
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero norm
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
