from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_nps_endpoint_records_web_score() -> None:
    orch = MagicMock()
    orch.record_nps = AsyncMock(return_value=True)
    client = _client(orch)

    res = client.post("/chat/nps", json={"session_id": "sim-abc", "score": 9})

    assert res.status_code == 200
    orch.record_nps.assert_awaited_once_with("sim-abc", 9, channel="web")


def test_nps_endpoint_rejects_too_high() -> None:
    orch = MagicMock()
    client = _client(orch)
    res = client.post("/chat/nps", json={"session_id": "sim-abc", "score": 11})
    assert res.status_code == 422


def test_nps_endpoint_rejects_negative() -> None:
    orch = MagicMock()
    client = _client(orch)
    res = client.post("/chat/nps", json={"session_id": "sim-abc", "score": -1})
    assert res.status_code == 422
