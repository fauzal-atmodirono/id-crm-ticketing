from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.kb_suggest_router import build_kb_suggest_router
from chatbot.features.chat.models import KbArticle


class _FakeKnowledge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        self.calls.append((query, limit))
        return [KbArticle(title="Reset battery", content="Long body " * 50, url="http://x/1")]


def _client(kb: _FakeKnowledge) -> TestClient:
    app = FastAPI()
    app.include_router(build_kb_suggest_router(kb))
    return TestClient(app)


def test_suggest_returns_snippets() -> None:
    kb = _FakeKnowledge()
    r = _client(kb).get("/kb/suggest?q=battery&limit=3")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "battery"
    assert body["suggestions"][0]["title"] == "Reset battery"
    assert len(body["suggestions"][0]["snippet"]) <= 280
    assert kb.calls == [("battery", 3)]


def test_suggest_blank_query_skips_kb() -> None:
    kb = _FakeKnowledge()
    r = _client(kb).get("/kb/suggest?q=%20")
    assert r.status_code == 200
    assert r.json() == {"query": "", "suggestions": []}
    assert kb.calls == []
