"""A video part reaches Gemini alongside the text, and a corrupt payload is
skipped rather than failing the turn."""

from __future__ import annotations

import base64

import pytest
from google.genai import types

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    # Mirrors test_service.py: get_settings() is lru_cached, so pin
    # session_store back to the "memory" default in case an earlier test in
    # the run mutated the shared Settings instance.
    get_settings().session_store = "memory"


@pytest.fixture
def captured_contents() -> list[types.Content]:
    """Collects every `types.Content` handed to the (stubbed) support agent,
    in call order — mirrors how test_service.py's per-test `captured["parts"]`
    dict inspects `new_message`, just accumulated across calls/sessions."""
    return []


@pytest.fixture
def orchestrator_service(captured_contents: list[types.Content]) -> OrchestratorService:
    """A real OrchestratorService (same construction as test_service.py) with
    `_run_support_agent` stubbed to record the Content it would have sent to
    Gemini instead of actually calling the ADK runner — the same injection
    point test_service.py's multimodal tests use."""
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
    )

    async def fake_run_support_agent(
        _session_id: str, new_message: types.Content
    ) -> tuple[str, list[str], bool]:
        captured_contents.append(new_message)
        return "ok", [], False

    svc._run_support_agent = fake_run_support_agent  # type: ignore[method-assign]
    return svc


async def test_video_base64_becomes_a_gemini_part(orchestrator_service, captured_contents):
    await orchestrator_service.handle_turn(
        session_id="s1",
        text="what is wrong with my car",
        video_base64=base64.b64encode(b"MP4BYTES").decode(),
        video_mime_type="video/mp4",
    )
    parts = captured_contents[-1].parts
    assert any(
        getattr(p, "inline_data", None) and p.inline_data.mime_type == "video/mp4" for p in parts
    )


async def test_undecodable_video_is_skipped_and_turn_still_runs(
    orchestrator_service, captured_contents
):
    await orchestrator_service.handle_turn(
        session_id="s2",
        text="what is wrong with my car",
        video_base64="!!!not-base64!!!",
        video_mime_type="video/mp4",
    )
    parts = captured_contents[-1].parts
    assert any(getattr(p, "text", None) == "what is wrong with my car" for p in parts)
    assert not any(getattr(p, "inline_data", None) for p in parts)
