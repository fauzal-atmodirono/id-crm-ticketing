from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.assistant_runtime import DEFAULT_COPILOT_PROMPT
from chatbot.features.assist.copilot_router import build_copilot_router
from chatbot.features.assist.copilot_tools import COPILOT_TOOLS
from chatbot.features.chat.adapters.assistants_store import (
    Assistant,
    AssistantConfig,
    InMemoryAssistantsStore,
)
from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
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
            assistants_store=InMemoryAssistantsStore(),
            tenant_settings_store=InMemoryTenantSettingsStore(),
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def _client_with_genai(responses: list, key: str = "k") -> tuple[TestClient, MagicMock]:
    """Like _client but also returns the genai mock for call-args inspection."""
    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(side_effect=responses)
    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=Settings(proton_backend_key=key, chatwoot_enabled=False),
            knowledge_port=_FakeKb(),
            genai_client=genai,
            assistants_store=InMemoryAssistantsStore(),
            tenant_settings_store=InMemoryTenantSettingsStore(),
        )
    )
    return TestClient(app, raise_server_exceptions=False), genai


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
    # Full field-mapping contract: content -> snippet, url passthrough.
    assert body["sources"][0] == {
        "title": "Warranty",
        "snippet": "12 months",
        "url": "http://faq/1",
    }


def test_copilot_cap_fallback_includes_sources() -> None:
    # Always returns a KB tool call; loop hits the cap. sources must still be present.
    c = _client([_tool_call_response("search_knowledge_base", {"query": "x"})] * 20)
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    # Deduped to a single source despite the tool running on every iteration.
    assert body["sources"] == [
        {"title": "Warranty", "snippet": "12 months", "url": "http://faq/1"}
    ]


# ---------------------------------------------------------------------------
# Behaviour-preservation: default assistant + no overrides → exact old contract
# ---------------------------------------------------------------------------


async def test_default_assistant_no_overrides_behaviour_preserving() -> None:
    """Default assistant + empty tenant store must produce exactly the same
    generate_content call as the old hardcoded path — model from settings,
    system_instruction == DEFAULT_COPILOT_PROMPT, temperature == 1.0, full COPILOT_TOOLS.
    """
    settings = Settings(proton_backend_key="k", chatwoot_enabled=False)
    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(return_value=_text_response("ok"))

    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=settings,
            knowledge_port=_FakeKb(),
            genai_client=genai,
            assistants_store=InMemoryAssistantsStore(),
            tenant_settings_store=InMemoryTenantSettingsStore(),
        )
    )

    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=True) as c:
        r = c.post(
            "/assist/copilot",
            json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
            headers={"x-api-key": "k"},
        )
    assert r.status_code == 200

    call_kwargs = genai.aio.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == settings.copilot_gemini_model
    called_config = call_kwargs.kwargs["config"]
    assert called_config["system_instruction"] == DEFAULT_COPILOT_PROMPT
    assert called_config["temperature"] == 1.0
    called_tools = called_config["tools"][0]["function_declarations"]
    assert called_tools == list(COPILOT_TOOLS)


async def test_tenant_override_changes_model() -> None:
    """A tenant-level override for copilot_gemini_model must be forwarded to
    generate_content instead of the env default.
    """
    settings = Settings(proton_backend_key="k", chatwoot_enabled=False)
    override_model = "gemini-2.0-pro"
    store = InMemoryTenantSettingsStore()
    await store.set_overrides({"copilot_gemini_model": override_model})

    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(return_value=_text_response("ok"))

    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=settings,
            knowledge_port=_FakeKb(),
            genai_client=genai,
            assistants_store=InMemoryAssistantsStore(),
            tenant_settings_store=store,
        )
    )

    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=True) as c:
        r = c.post(
            "/assist/copilot",
            json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
            headers={"x-api-key": "k"},
        )
    assert r.status_code == 200

    call_kwargs = genai.aio.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == override_model


# ---------------------------------------------------------------------------
# feature_citations gate
# ---------------------------------------------------------------------------


def _make_assistant(feature_citations: bool) -> Assistant:
    return Assistant(
        id="asst_citations_test",
        name="Citations Test",
        description="",
        product_name="",
        config=AssistantConfig(feature_citations=feature_citations),
        enabled=True,
        is_default=True,
        created_at=datetime.now(UTC).isoformat(),
    )


def _client_with_assistant(responses: list, assistant: Assistant, key: str = "k") -> TestClient:
    """Build a TestClient with a pre-seeded assistant in the store."""
    import asyncio  # noqa: PLC0415

    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(side_effect=responses)
    store = InMemoryAssistantsStore()

    asyncio.run(store.create(assistant))

    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=Settings(proton_backend_key=key, chatwoot_enabled=False),
            knowledge_port=_FakeKb(),
            genai_client=genai,
            assistants_store=store,
            tenant_settings_store=InMemoryTenantSettingsStore(),
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_feature_citations_false_suppresses_sources() -> None:
    """When feature_citations=False, sources must be [] even when search_knowledge_base returned articles."""
    assistant = _make_assistant(feature_citations=False)
    c = _client_with_assistant(
        [
            _tool_call_response("search_knowledge_base", {"query": "warranty"}),
            _text_response("12 months per KB."),
        ],
        assistant=assistant,
    )
    r = c.post(
        "/assist/copilot",
        json={
            "conversation_id": "1",
            "thread": [{"role": "user", "content": "warranty?"}],
            "assistant_id": "asst_citations_test",
        },
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] == []
    # tool_calls must still be present (citations gate does not affect tool_calls).
    assert body["tool_calls"] == ["search_knowledge_base"]


def test_feature_citations_true_keeps_sources() -> None:
    """When feature_citations=True (default), sources are surfaced as normal."""
    assistant = _make_assistant(feature_citations=True)
    c = _client_with_assistant(
        [
            _tool_call_response("search_knowledge_base", {"query": "warranty"}),
            _text_response("12 months per KB."),
        ],
        assistant=assistant,
    )
    r = c.post(
        "/assist/copilot",
        json={
            "conversation_id": "1",
            "thread": [{"role": "user", "content": "warranty?"}],
            "assistant_id": "asst_citations_test",
        },
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] and body["sources"][0]["title"] == "Warranty"


def test_feature_citations_false_at_cap_sources_empty() -> None:
    """feature_citations=False must suppress sources even when the iteration cap is hit."""
    assistant = _make_assistant(feature_citations=False)
    c = _client_with_assistant(
        [_tool_call_response("search_knowledge_base", {"query": "x"})] * 20,
        assistant=assistant,
    )
    r = c.post(
        "/assist/copilot",
        json={
            "conversation_id": "1",
            "thread": [{"role": "user", "content": "hi"}],
            "assistant_id": "asst_citations_test",
        },
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    assert r.json()["sources"] == []


# ---------------------------------------------------------------------------
# Behaviour-preservation: default assistant → full COPILOT_TOOLS + sources present
# ---------------------------------------------------------------------------


def test_default_assistant_has_citations_enabled() -> None:
    """Default assistant (feature_citations=True by default) must return sources."""
    c = _client(
        [
            _tool_call_response("search_knowledge_base", {"query": "warranty"}),
            _text_response("12 months."),
        ]
    )
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "warranty?"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] and body["sources"][0]["title"] == "Warranty"
