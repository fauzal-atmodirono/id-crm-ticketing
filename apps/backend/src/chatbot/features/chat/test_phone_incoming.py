from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_incoming_returns_connect_stream_twiml() -> None:
    orch = MagicMock()
    s = get_settings()
    s.public_wss_base_url = "wss://tunnel.test"
    orch._settings = s
    res = _client(orch).post("/voice/phone/incoming")
    assert res.status_code == 200
    assert "application/xml" in res.headers["content-type"]
    assert 'url="wss://tunnel.test/voice/phone/stream"' in res.text
    assert "<Connect>" in res.text
