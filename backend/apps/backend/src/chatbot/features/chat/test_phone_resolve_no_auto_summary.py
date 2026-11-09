"""A resolved phone call gets no "[Auto-summary — resolved case]" note
(2026-11-09): the call's transcript and handoff note are already on the
conversation, and an abandoned call (no conversation at all) got an EMPTY
auto-summary. Chat conversations are unchanged."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeRunner:
    async def run_async(self, **_: Any) -> None:
        return None


class _Indexer:
    def __init__(self) -> None:
        self.resolved: list[str] = []

    async def handle_resolved(self, *, conversation_id: str) -> None:
        self.resolved.append(conversation_id)


def _client(indexer: _Indexer) -> TestClient:
    settings = get_settings()
    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )
    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator, resolved_case_index=indexer))  # type: ignore[arg-type]
    return TestClient(app)


def _resolve(client: TestClient, **extra: Any) -> None:
    body = {"event": "conversation_status_changed", "id": 42, "status": "resolved", **extra}
    assert client.post("/webhooks/chatwoot", json=body).status_code == 200


def test_phone_call_resolve_skips_the_auto_summary() -> None:
    idx = _Indexer()
    client = _client(idx)
    _resolve(client, additional_attributes={"session_id": "phone-CA1"})
    _resolve(client, custom_attributes={"external_id": "phone-CA1"})
    assert idx.resolved == []


def test_chat_resolve_still_gets_the_auto_summary() -> None:
    idx = _Indexer()
    _resolve(_client(idx), additional_attributes={"session_id": "whatsapp-1"})
    assert idx.resolved == ["42"]
