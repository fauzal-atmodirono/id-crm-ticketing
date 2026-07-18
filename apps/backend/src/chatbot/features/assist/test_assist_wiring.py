"""Smoke-test: verify /assist/* routes exist and are auth-guarded in the live app.

Uses the full bootstrap to ensure the router is wired; mocks GCP so no credentials needed.
"""
from unittest.mock import MagicMock, patch, AsyncMock

from fastapi.testclient import TestClient


def _patched_app() -> object:
    """Bootstrap the app with all GCP clients stubbed out."""
    import sys

    # Remove the app module if already imported to force reload
    if "chatbot.main" in sys.modules:
        del sys.modules["chatbot.main"]

    mock_genai = MagicMock()
    mock_genai.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="reply"))

    with (
        patch("chatbot.features.chat.service.Client", MagicMock(return_value=mock_genai)),
        patch("chatbot.main._build_genai_client", return_value=mock_genai),
        patch("chatbot.main.VertexAISearchAdapter", MagicMock()),
        patch("chatbot.main.build_handoff_store", MagicMock()),
        patch("chatbot.main.build_audit_log", MagicMock(return_value=None)),
        patch("chatbot.main.build_metrics_port", MagicMock()),
        patch("chatbot.main.start_sla_scheduler", return_value=None),
        patch("chatbot.main.start_metrics_scheduler", return_value=None),
        patch("chatbot.main.start_report_scheduler", return_value=None),
    ):
        from chatbot.main import bootstrap_application
        return bootstrap_application()


def test_assist_suggest_route_exists_and_is_auth_guarded() -> None:
    app = _patched_app()
    client = TestClient(app)
    r = client.post("/assist/suggest", json={"conversation_id": "1", "messages": ["hi"]})
    # 401 or 503 — either means the route exists and is guarded (not 404)
    assert r.status_code in (401, 503)


def test_assist_summarize_route_exists_and_is_auth_guarded() -> None:
    app = _patched_app()
    client = TestClient(app)
    r = client.post("/assist/summarize", json={"conversation_id": "1", "messages": ["hi"]})
    assert r.status_code in (401, 503)


def test_assist_ask_route_exists_and_is_auth_guarded() -> None:
    app = _patched_app()
    client = TestClient(app)
    r = client.post(
        "/assist/ask",
        json={"conversation_id": "1", "messages": ["hi"], "question": "what?"},
    )
    assert r.status_code in (401, 503)
