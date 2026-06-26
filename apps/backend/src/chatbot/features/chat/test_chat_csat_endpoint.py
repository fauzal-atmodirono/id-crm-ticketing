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


def test_csat_endpoint_records_web_rating() -> None:
    orch = MagicMock()
    orch.record_csat = AsyncMock(return_value=True)
    client = _client(orch)

    res = client.post("/chat/csat", json={"session_id": "sim-abc", "score": 5})

    assert res.status_code == 200
    orch.record_csat.assert_awaited_once_with("sim-abc", 5, channel="web")


def test_csat_endpoint_rejects_out_of_range() -> None:
    orch = MagicMock()
    client = _client(orch)
    res = client.post("/chat/csat", json={"session_id": "sim-abc", "score": 9})
    assert res.status_code == 422
