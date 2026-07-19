from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.copilot_router import build_copilot_router
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import Settings


class _FakeKb:
    async def search_kb(self, query: str, limit: int = 3):
        return [KbArticle(title="Warranty", content="12 months", url="http://faq/1")]


def _text_response(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.function_calls = []
    return r


def _tool_call_response(name: str, args: dict) -> MagicMock:
    r = MagicMock()
    r.text = None
    r.function_calls = [SimpleNamespace(name=name, args=args)]
    return r


def _client(responses: list, key: str = "k") -> TestClient:
    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(side_effect=responses)
    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=Settings(proton_backend_key=key, chatwoot_enabled=False),
            knowledge_port=_FakeKb(),
            genai_client=genai,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_requires_api_key() -> None:
    c = _client([_text_response("hi")])
    r = c.post("/assist/copilot", json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_single_turn_answer() -> None:
    c = _client([_text_response("The warranty is 12 months.")])
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "warranty?"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "The warranty is 12 months."
    assert body["tool_calls"] == []


def test_tool_call_then_answer() -> None:
    c = _client([
        _tool_call_response("search_knowledge_base", {"query": "warranty"}),
        _text_response("Per the KB, 12 months."),
    ])
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "warranty?"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Per the KB, 12 months."
    assert body["tool_calls"] == ["search_knowledge_base"]


def test_iteration_cap_returns_fallback() -> None:
    # Always returns a tool call; loop must stop at the cap and still 200.
    c = _client([_tool_call_response("search_knowledge_base", {"query": "x"})] * 20)
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["answer"], str) and r.json()["answer"]
    assert r.json()["tool_calls"] == ["search_knowledge_base"] * 5


def test_wrong_key_rejected() -> None:
    c = _client([_text_response("hi")])
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": "wrong"},
    )
    assert r.status_code == 401


def test_503_when_key_unconfigured() -> None:
    c = _client([_text_response("hi")], key="")
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": "anything"},
    )
    assert r.status_code == 503


def test_copilot_returns_sources_from_kb_tool() -> None:
    # First response calls the KB tool; second returns text.
    c = _client([
        _tool_call_response("search_knowledge_base", {"query": "warranty"}),
        _text_response("12 months per the KB."),
    ])
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "warranty?"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] and body["sources"][0]["title"] == "Warranty"
