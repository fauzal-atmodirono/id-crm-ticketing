from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.sim.router import build_sim_router
from chatbot.platform.config import get_settings


class _FakeRunner:
    async def run_async(self, **_: Any) -> AsyncGenerator[Any, None]:
        for _i in range(0):
            yield None


@pytest.fixture
def client() -> TestClient:
    settings = get_settings()
    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        stt_port=MockVoiceAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )
    app = FastAPI()
    app.include_router(build_sim_router(orchestrator))
    return TestClient(app)


def test_sim_index_returns_html(client: TestClient) -> None:
    response = client.get("/sim")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Proton Conversational AI" in response.text
    assert "/sim/chat" in response.text


def test_sim_chat_runs_a_turn_and_returns_reply_shape(client: TestClient) -> None:
    response = client.post(
        "/sim/chat",
        json={"session_id": "sim-test", "text": "Hello"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "reply" in body
    assert "language" in body
    assert "sentiment" in body
    assert "handoff" in body
