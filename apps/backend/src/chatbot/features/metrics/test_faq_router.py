from __future__ import annotations

from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from chatbot.features.metrics.faq_feedback import FaqFeedback
from chatbot.features.metrics.faq_router import build_faq_router
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


def _client(faq_port: Any, api_key: str) -> TestClient:
    settings = Settings(qa_api_key=api_key)
    app = create_app(settings)
    app.include_router(build_faq_router(faq_port, settings))
    return TestClient(app)


def _body() -> dict[str, Any]:
    return {
        "article_id": "kb-42",
        "session_id": "sess-123",
        "helpful": True,
        "score": 5,
    }


def test_faq_feedback_records_with_valid_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/kb/feedback", json=_body(), headers={"X-API-Key": "secret"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    port.record_feedback.assert_awaited_once()
    feedback = port.record_feedback.await_args.args[0]
    assert isinstance(feedback, FaqFeedback)
    assert feedback.article_id == "kb-42"
    assert feedback.session_id == "sess-123"
    assert feedback.helpful is True
    assert feedback.score == 5
    assert feedback.at.tzinfo is UTC


def test_faq_feedback_rejects_wrong_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/kb/feedback", json=_body(), headers={"X-API-Key": "nope"})
    assert res.status_code == 401
    port.record_feedback.assert_not_awaited()


def test_faq_feedback_rejects_missing_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/kb/feedback", json=_body())
    assert res.status_code == 401
    port.record_feedback.assert_not_awaited()


def test_faq_feedback_locked_when_api_key_unset() -> None:
    port = AsyncMock()
    client = _client(port, "")
    res = client.post("/kb/feedback", json=_body(), headers={"X-API-Key": ""})
    assert res.status_code == 401
    port.record_feedback.assert_not_awaited()


def test_faq_feedback_rejects_out_of_range_score() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    high = {**_body(), "score": 9}
    low = {**_body(), "score": 0}
    assert (
        client.post("/kb/feedback", json=high, headers={"X-API-Key": "secret"}).status_code == 422
    )
    assert client.post("/kb/feedback", json=low, headers={"X-API-Key": "secret"}).status_code == 422


def test_faq_feedback_rejects_non_ascii_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/kb/feedback", json=_body(), headers={"X-API-Key": b"\xe9"})
    assert res.status_code == 401
    port.record_feedback.assert_not_awaited()
