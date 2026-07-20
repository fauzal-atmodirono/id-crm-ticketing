"""Tests for /assist/* endpoints.

Uses a stub Gemini client and a stub KnowledgePort so no real GCP calls are made.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.router import (
    _ASK_SYSTEM,
    _SUGGEST_SYSTEM,
    _SUMMARIZE_SYSTEM,
    build_assist_router,
)
from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import Settings


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 3) -> list:
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


# ---------------------------------------------------------------------------
# Helpers for store-aware tests
# ---------------------------------------------------------------------------


class _FakeTenantSettingsStore:
    """Returns a single override value for assist_gemini_model."""

    def __init__(self, overrides: dict) -> None:
        self._overrides = overrides

    async def get_overrides(self) -> dict:
        return self._overrides


def _make_assistant(product_name: str = "", guardrails: list[str] | None = None):
    """Build an Assistant dataclass with minimal fields."""
    return Assistant(
        id="asst_test",
        name="Test Assistant",
        description="",
        product_name=product_name,
        config=AssistantConfig(guardrails=guardrails or []),
        enabled=True,
        is_default=False,
        created_at="2024-01-01T00:00:00+00:00",
    )


class _FakeAssistantsStore:
    """Returns a preconfigured assistant for any id; also serves as default."""

    def __init__(self, assistant) -> None:
        self._assistant = assistant

    async def get(self, assistant_id: str):
        return self._assistant

    async def get_default(self):
        return self._assistant


def _client_with_stores(
    tenant_settings_store=None,
    assistants_store=None,
    key: str = "testkey",
) -> tuple[TestClient, MagicMock]:
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
            assistants_store=assistants_store,
            tenant_settings_store=tenant_settings_store,
        )
    )
    return TestClient(app), mock_genai


# ---------------------------------------------------------------------------
# Tenant model override tests
# ---------------------------------------------------------------------------


def test_suggest_uses_tenant_model_override() -> None:
    """When tenant_settings_store overrides assist_gemini_model, that model is used."""
    store = _FakeTenantSettingsStore({"assist_gemini_model": "gemini-2.0-flash"})
    client, mock_genai = _client_with_stores(tenant_settings_store=store)

    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["Hi"]},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    call_kwargs = mock_genai.aio.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == "gemini-2.0-flash"


def test_summarize_uses_tenant_model_override() -> None:
    store = _FakeTenantSettingsStore({"assist_gemini_model": "gemini-2.0-pro"})
    client, mock_genai = _client_with_stores(tenant_settings_store=store)

    r = client.post(
        "/assist/summarize",
        json={"conversation_id": "42", "messages": ["msg"]},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    call_kwargs = mock_genai.aio.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == "gemini-2.0-pro"


def test_ask_uses_tenant_model_override() -> None:
    store = _FakeTenantSettingsStore({"assist_gemini_model": "gemini-2.0-flash"})
    client, mock_genai = _client_with_stores(tenant_settings_store=store)

    r = client.post(
        "/assist/ask",
        json={"conversation_id": "42", "messages": ["msg"], "question": "What?"},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    call_kwargs = mock_genai.aio.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == "gemini-2.0-flash"


# ---------------------------------------------------------------------------
# Assistant persona prefix tests
# ---------------------------------------------------------------------------


def test_suggest_includes_assistant_persona_in_system_prompt() -> None:
    """When an assistant has product_name + guardrails, they appear in the system prompt."""
    assistant = _make_assistant(
        product_name="Proton Mail",
        guardrails=["Keep it brief", "No pricing details"],
    )
    astore = _FakeAssistantsStore(assistant)
    client, mock_genai = _client_with_stores(assistants_store=astore)

    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["Hi"], "assistant_id": "asst_test"},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    call_kwargs = mock_genai.aio.models.generate_content.call_args
    system = call_kwargs.kwargs["config"]["system_instruction"]
    assert "Product: Proton Mail" in system
    assert "Keep it brief" in system
    assert "No pricing details" in system
    # Task prompt must still be present and after the persona
    assert "customer-support agent" in system
    persona_pos = system.index("Product: Proton Mail")
    task_pos = system.index("customer-support agent")
    assert persona_pos < task_pos


def test_summarize_includes_assistant_persona_in_system_prompt() -> None:
    assistant = _make_assistant(
        product_name="Proton Drive",
        guardrails=["Escalate billing issues"],
    )
    astore = _FakeAssistantsStore(assistant)
    client, mock_genai = _client_with_stores(assistants_store=astore)

    r = client.post(
        "/assist/summarize",
        json={"conversation_id": "42", "messages": ["msg"], "assistant_id": "asst_test"},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    system = mock_genai.aio.models.generate_content.call_args.kwargs["config"]["system_instruction"]
    assert "Product: Proton Drive" in system
    assert "Escalate billing issues" in system
    assert "customer-support supervisor" in system


def test_ask_includes_assistant_persona_in_system_prompt() -> None:
    assistant = _make_assistant(
        product_name="Proton VPN",
        guardrails=["Do not reveal internal tools"],
    )
    astore = _FakeAssistantsStore(assistant)
    client, mock_genai = _client_with_stores(assistants_store=astore)

    r = client.post(
        "/assist/ask",
        json={
            "conversation_id": "42",
            "messages": ["msg"],
            "question": "What?",
            "assistant_id": "asst_test",
        },
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    system = mock_genai.aio.models.generate_content.call_args.kwargs["config"]["system_instruction"]
    assert "Product: Proton VPN" in system
    assert "Do not reveal internal tools" in system
    assert "knowledge assistant" in system


# ---------------------------------------------------------------------------
# Behaviour-preserving: no stores, no assistant_id → identical to original
# ---------------------------------------------------------------------------


def test_suggest_default_path_is_behaviour_preserving() -> None:
    """No stores, no assistant_id: model == settings value AND system == original task prompt."""
    class _SingleArticle:
        async def search_kb(self, query: str, limit: int = 3) -> list:
            return [KbArticle(title="FAQ Title", content="FAQ content body", url="http://faq/1")]

    mock_genai = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "reply"
    mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_response)

    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=_settings(),
            knowledge_port=_SingleArticle(),
            genai_client=mock_genai,
        )
    )
    client = TestClient(app)
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "1", "messages": ["Hi"]},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    call_kwargs = mock_genai.aio.models.generate_content.call_args.kwargs
    # Model must be the settings default
    assert call_kwargs["model"] == "gemini-2.5-flash"
    # System prompt must be exactly the original task prompt (no persona prefix)
    expected_faq = "Q: FAQ Title\nA: FAQ content body"
    expected_system = _SUGGEST_SYSTEM.format(faq_context=expected_faq)
    assert call_kwargs["config"]["system_instruction"] == expected_system


def test_summarize_default_path_is_behaviour_preserving() -> None:
    mock_genai = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "summary"
    mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_response)

    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=_settings(),
            knowledge_port=_FakeKnowledge(),
            genai_client=mock_genai,
        )
    )
    client = TestClient(app)
    r = client.post(
        "/assist/summarize",
        json={"conversation_id": "1", "messages": ["msg"]},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    call_kwargs = mock_genai.aio.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-flash"
    assert call_kwargs["config"]["system_instruction"] == _SUMMARIZE_SYSTEM


def test_ask_default_path_is_behaviour_preserving() -> None:
    class _SingleArticle:
        async def search_kb(self, query: str, limit: int = 3) -> list:
            return [KbArticle(title="FAQ Title", content="FAQ content body", url="http://faq/1")]

    mock_genai = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "answer"
    mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_response)

    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=_settings(),
            knowledge_port=_SingleArticle(),
            genai_client=mock_genai,
        )
    )
    client = TestClient(app)
    r = client.post(
        "/assist/ask",
        json={"conversation_id": "1", "messages": ["msg"], "question": "What?"},
        headers={"x-api-key": "testkey"},
    )
    assert r.status_code == 200
    call_kwargs = mock_genai.aio.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-flash"
    expected_faq = "Q: FAQ Title\nA: FAQ content body"
    expected_system = _ASK_SYSTEM.format(faq_context=expected_faq)
    assert call_kwargs["config"]["system_instruction"] == expected_system
