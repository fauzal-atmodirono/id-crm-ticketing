"""Tests for /kb/suggest merging live-FAQ semantic hits with Vertex-Search hits.

Both sources are faked; the embedder is mocked. Verifies: both appear, live_faq
entries are labelled + ranked first, and a live-store failure falls back to
Vertex-only without error.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.kb_suggest_router import build_kb_suggest_router
from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.ports import LiveFaqEntry


class FakeKnowledge:
    def __init__(self, articles: list[KbArticle] | None = None) -> None:
        self.articles = articles or [
            KbArticle(title="Vertex battery article", content="body", url="http://x/1")
        ]

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        return self.articles


class FakeLiveStore:
    def __init__(self, entry: LiveFaqEntry | None = None) -> None:
        self.entry = entry

    async def search(
        self, query_embedding: list[float], limit: int
    ) -> list[tuple[LiveFaqEntry, float]]:
        return [(self.entry, 0.92)] if self.entry else []


class FailingLiveStore:
    async def search(self, query_embedding: list[float], limit: int) -> list[object]:
        raise RuntimeError("firestore down")


class FakeEmbedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector if vector is not None else [1.0, 0.0]

    async def embed(self, text: str) -> list[float]:
        return self.vector


class FailingEmbedder:
    async def embed(self, text: str) -> list[float]:
        raise RuntimeError("embed down")


def _client(router_args: dict) -> TestClient:  # type: ignore[type-arg]
    app = FastAPI()
    app.include_router(build_kb_suggest_router(**router_args))
    return TestClient(app)


def test_merge_returns_both_live_and_vertex() -> None:
    live = FakeLiveStore(
        LiveFaqEntry(id="faq-1", question="Reset battery FAQ", answer="Hold the button.")
    )
    client = _client(
        {"knowledge_port": FakeKnowledge(), "live_faq_store": live, "embedder": FakeEmbedder()}
    )
    body = client.get("/kb/suggest?q=battery&limit=5").json()
    titles = [s["title"] for s in body["suggestions"]]
    assert "Reset battery FAQ" in titles
    assert "Vertex battery article" in titles


def test_live_faq_ranked_first_and_labelled() -> None:
    live = FakeLiveStore(
        LiveFaqEntry(id="faq-1", question="Reset battery FAQ", answer="Hold the button.")
    )
    client = _client(
        {"knowledge_port": FakeKnowledge(), "live_faq_store": live, "embedder": FakeEmbedder()}
    )
    suggestions = client.get("/kb/suggest?q=battery&limit=5").json()["suggestions"]
    assert suggestions[0]["source_type"] == "live_faq"
    assert suggestions[0]["title"] == "Reset battery FAQ"
    assert suggestions[0]["url"] is None
    assert suggestions[0]["snippet"] == "Hold the button."


def test_response_shape_unchanged() -> None:
    live = FakeLiveStore(LiveFaqEntry(id="faq-1", question="Q", answer="A"))
    client = _client(
        {"knowledge_port": FakeKnowledge(), "live_faq_store": live, "embedder": FakeEmbedder()}
    )
    body = client.get("/kb/suggest?q=battery").json()
    assert set(body.keys()) == {"query", "suggestions"}
    for s in body["suggestions"]:
        assert {"title", "snippet", "url", "source_type"} <= set(s.keys())


def test_live_store_failure_falls_back_to_vertex_only() -> None:
    client = _client(
        {
            "knowledge_port": FakeKnowledge(),
            "live_faq_store": FailingLiveStore(),
            "embedder": FakeEmbedder(),
        }
    )
    r = client.get("/kb/suggest?q=battery&limit=5")
    assert r.status_code == 200
    suggestions = r.json()["suggestions"]
    assert [s["title"] for s in suggestions] == ["Vertex battery article"]


def test_embedder_failure_falls_back_to_vertex_only() -> None:
    live = FakeLiveStore(LiveFaqEntry(id="faq-1", question="Q", answer="A"))
    client = _client(
        {
            "knowledge_port": FakeKnowledge(),
            "live_faq_store": live,
            "embedder": FailingEmbedder(),
        }
    )
    r = client.get("/kb/suggest?q=battery&limit=5")
    assert r.status_code == 200
    assert [s["title"] for s in r.json()["suggestions"]] == ["Vertex battery article"]


def test_no_live_store_configured_is_vertex_only() -> None:
    client = _client({"knowledge_port": FakeKnowledge()})
    r = client.get("/kb/suggest?q=battery")
    assert r.status_code == 200
    assert [s["title"] for s in r.json()["suggestions"]] == ["Vertex battery article"]


def test_limit_is_respected_after_merge() -> None:
    live = FakeLiveStore(LiveFaqEntry(id="faq-1", question="Live Q", answer="A"))
    client = _client(
        {"knowledge_port": FakeKnowledge(), "live_faq_store": live, "embedder": FakeEmbedder()}
    )
    suggestions = client.get("/kb/suggest?q=battery&limit=1").json()["suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["source_type"] == "live_faq"
