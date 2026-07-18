"""Tests for /assist/* endpoints.

Uses a stub Gemini client and a stub KnowledgePort so no real GCP calls are made.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.router import build_assist_router
from chatbot.platform.config import Settings


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 3) -> list:
        from chatbot.features.chat.models import KbArticle
        return [KbArticle(title="FAQ Title", content="FAQ content body", url="http://faq/1")]


def _settings(key: str = "testkey") -> Settings:
    return Settings(proton_backend_key=key, assist_gemini_model="gemini-2.5-flash")


def _client(key: str = "testkey") -> tuple[TestClient, MagicMock]:
    """Return (TestClient, mock_genai_client) with the Gemini client patched."""
    mock_genai = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "This is the AI output."
    mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_response)

    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=_settings(key),
            knowledge_port=_FakeKnowledge(),
            genai_client=mock_genai,
        )
    )
    return TestClient(app), mock_genai


# ---------------------------------------------------------------------------
# Auth guard (all three endpoints)
# ---------------------------------------------------------------------------

def test_suggest_requires_api_key() -> None:
    client, _ = _client()
    r = client.post("/assist/suggest", json={"conversation_id": "42", "messages": ["Hi"]})
    assert r.status_code == 401


def test_suggest_wrong_key_rejected() -> None:
    client, _ = _client()
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["Hi"]},
        headers={"x-api-key": "wrongkey"},
    )
    assert r.status_code == 401


def test_summarize_requires_api_key() -> None:
    client, _ = _client()
    r = client.post("/assist/summarize", json={"conversation_id": "42", "messages": ["Hi"]})
    assert r.status_code == 401


def test_ask_requires_api_key() -> None:
    client, _ = _client()
    r = client.post(
        "/assist/ask",
        json={"conversation_id": "42", "messages": ["Hi"], "question": "What is the warranty?"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_suggest_returns_draft_and_sources() -> None:
    client, mock_genai = _client()
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["My battery died"], "limit": 2},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "draft" in body
    assert body["draft"] == "This is the AI output."
    assert isinstance(body["sources"], list)
    assert body["sources"][0]["title"] == "FAQ Title"


def test_summarize_returns_summary() -> None:
    client, mock_genai = _client()
    r = client.post(
        "/assist/summarize",
        json={"conversation_id": "42", "messages": ["Customer said X", "Agent replied Y"]},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "This is the AI output."


def test_ask_returns_answer() -> None:
    client, mock_genai = _client()
    r = client.post(
        "/assist/ask",
        json={
            "conversation_id": "42",
            "messages": ["Customer asked about warranty"],
            "question": "What is the warranty period?",
        },
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "This is the AI output."


# ---------------------------------------------------------------------------
# 503 when proton_backend_key is empty (misconfigured deployment)
# ---------------------------------------------------------------------------

def test_suggest_503_when_key_unconfigured() -> None:
    mock_genai = MagicMock()
    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=Settings(proton_backend_key=""),
            knowledge_port=_FakeKnowledge(),
            genai_client=mock_genai,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["Hi"]},
        headers={"x-api-key": "anything"},
    )
    assert r.status_code == 503
