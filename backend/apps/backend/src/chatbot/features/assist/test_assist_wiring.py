"""Smoke-test: verify /assist/* routes exist and are auth-guarded in the live app.

Uses the full bootstrap to ensure the router is wired; mocks GCP so no credentials needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from chatbot.platform.config import get_settings


def _patched_app() -> object:
    """Bootstrap the app with all GCP clients stubbed out."""
    import sys

    # Remove the app module if already imported to force reload
    if "chatbot.main" in sys.modules:
        del sys.modules["chatbot.main"]

    mock_genai = MagicMock()
    mock_genai.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="reply"))

    with (
        # Both construction sites now go through the metering wrapper, so the
        # stub goes there rather than at `google.genai.Client`. `main.py`'s seam
        # is patched separately below because it is the function tests have
        # always reached for.
        patch(
            "chatbot.features.chat.service.build_metered_genai_client",
            MagicMock(return_value=mock_genai),
        ),
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


def test_assist_preflight_allows_the_chatwoot_session_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CORS preflight must not reject the headers /assist/translate needs.

    /assist/translate is gated by require_permission, which reads the caller's
    devise_token_auth triplet (x-chatwoot-access-token/client/uid). The browser
    asks permission for those headers before sending them, so an allow_headers
    list that omits them fails the request in the browser — before the
    dependency runs, and with a CORS error rather than the 401 the missing
    headers would otherwise produce. Only registered when ASSIST_CORS_ORIGINS
    is set, hence the env var here.
    """
    monkeypatch.setenv("ASSIST_CORS_ORIGINS", '["http://crm.example.com"]')
    get_settings.cache_clear()
    try:
        client = TestClient(_patched_app())
        r = client.options(
            "/assist/translate",
            headers={
                "Origin": "http://crm.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "content-type,x-api-key,x-chatwoot-access-token,"
                    "x-chatwoot-client,x-chatwoot-uid"
                ),
            },
        )
    finally:
        get_settings.cache_clear()

    assert r.status_code == 200, r.text
    allowed = r.headers.get("access-control-allow-headers", "").lower()
    for header in ("x-chatwoot-access-token", "x-chatwoot-client", "x-chatwoot-uid"):
        assert header in allowed


def test_assist_ask_route_exists_and_is_auth_guarded() -> None:
    app = _patched_app()
    client = TestClient(app)
    r = client.post(
        "/assist/ask",
        json={"conversation_id": "1", "messages": ["hi"], "question": "what?"},
    )
    assert r.status_code in (401, 503)
