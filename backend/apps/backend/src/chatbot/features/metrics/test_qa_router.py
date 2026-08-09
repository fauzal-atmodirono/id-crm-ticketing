from __future__ import annotations

from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from chatbot.features.metrics.qa import QaLabel
from chatbot.features.metrics.qa_router import build_qa_router
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


def _client(qa_port: Any, api_key: str) -> TestClient:
    settings = Settings(qa_api_key=api_key)
    app = create_app(settings)
    app.include_router(build_qa_router(qa_port, settings))
    return TestClient(app)


def _body() -> dict[str, Any]:
    return {
        "conversation_id": "T-9",
        "accuracy": 88,
        "quality": 92,
        "reviewer": "alice",
        "notes": "ok",
    }


def test_qa_label_records_with_valid_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/qa/label", json=_body(), headers={"X-API-Key": "secret"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    port.record_label.assert_awaited_once()
    label = port.record_label.await_args.args[0]
    assert isinstance(label, QaLabel)
    assert label.conversation_id == "T-9"
    assert label.accuracy == 88
    assert label.quality == 92
    assert label.reviewer == "alice"
    assert label.notes == "ok"
    assert label.labeled_at.tzinfo is UTC


def test_qa_label_rejects_wrong_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/qa/label", json=_body(), headers={"X-API-Key": "nope"})
    assert res.status_code == 401
    port.record_label.assert_not_awaited()


def test_qa_label_rejects_missing_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/qa/label", json=_body())
    assert res.status_code == 401
    port.record_label.assert_not_awaited()


def test_qa_label_locked_when_api_key_unset() -> None:
    port = AsyncMock()
    client = _client(port, "")
    res = client.post("/qa/label", json=_body(), headers={"X-API-Key": ""})
    assert res.status_code == 401
    port.record_label.assert_not_awaited()


def test_qa_label_rejects_out_of_range_score() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    high = {**_body(), "accuracy": 101}
    low = {**_body(), "quality": -1}
    assert client.post("/qa/label", json=high, headers={"X-API-Key": "secret"}).status_code == 422
    assert client.post("/qa/label", json=low, headers={"X-API-Key": "secret"}).status_code == 422


def test_qa_label_rejects_non_ascii_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    # httpx refuses to encode non-ASCII str headers; pass raw latin-1 bytes instead.
    # Starlette decodes these bytes as latin-1, producing a non-ASCII str ("\xe9")
    # that would make hmac.compare_digest(str, str) raise TypeError → 500 pre-fix.
    res = client.post("/qa/label", json=_body(), headers={"X-API-Key": b"\xe9"})
    assert res.status_code == 401
    port.record_label.assert_not_awaited()


# --- P8 task 7: channel + call rubric, gated on call_qa_enabled ------------


def _client_with_settings(qa_port: Any, settings: Settings) -> TestClient:
    app = create_app(settings)
    app.include_router(build_qa_router(qa_port, settings))
    return TestClient(app)


def test_call_qa_disabled_drops_channel_and_rubric_fields() -> None:
    """Off is byte-identical to today: a caller sending the new fields with
    call_qa_enabled off gets the exact pre-P8 channel-agnostic label."""
    port = AsyncMock()
    settings = Settings(qa_api_key="secret", call_qa_enabled=False)
    client = _client_with_settings(port, settings)
    body = {**_body(), "channel": "Phone", "rubric_greeting": True}

    res = client.post("/qa/label", json=body, headers={"X-API-Key": "secret"})

    assert res.status_code == 200
    label = port.record_label.await_args.args[0]
    assert label.channel is None
    assert label.call_rubric is None


def test_call_qa_enabled_persists_channel_and_rubric() -> None:
    port = AsyncMock()
    settings = Settings(qa_api_key="secret", call_qa_enabled=True)
    client = _client_with_settings(port, settings)
    body = {
        **_body(),
        "channel": "Phone",
        "rubric_greeting": True,
        "rubric_identification": True,
        "rubric_resolution": True,
        "rubric_closing": False,
        "rubric_compliance": True,
    }

    res = client.post("/qa/label", json=body, headers={"X-API-Key": "secret"})

    assert res.status_code == 200
    label = port.record_label.await_args.args[0]
    assert label.channel == "Phone"
    assert label.call_rubric is not None
    assert label.call_rubric.percentage() == 80.0


def test_call_qa_enabled_channel_only_label_carries_no_rubric() -> None:
    """A channel-only label (tagging without scoring) must not manufacture a
    five-None rubric -- that would read as `incomplete` rather than as
    "no rubric was ever attempted", a different thing."""
    port = AsyncMock()
    settings = Settings(qa_api_key="secret", call_qa_enabled=True)
    client = _client_with_settings(port, settings)
    body = {**_body(), "channel": "WhatsApp"}

    res = client.post("/qa/label", json=body, headers={"X-API-Key": "secret"})

    assert res.status_code == 200
    label = port.record_label.await_args.args[0]
    assert label.channel == "WhatsApp"
    assert label.call_rubric is None
