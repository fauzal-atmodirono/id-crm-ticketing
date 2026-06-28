from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_token_endpoint_returns_jwt_and_identity() -> None:
    orch = MagicMock()
    s = get_settings()
    s.twilio_account_sid = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    s.twilio_api_key_sid = "SKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    s.twilio_api_key_secret = "secretsecretsecretsecretsecretse"
    s.twilio_twiml_app_sid = "APxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    orch._settings = s
    res = _client(orch).post("/voice/phone/token")
    assert res.status_code == 200
    body = res.json()
    assert body["identity"]
    assert body["token"].count(".") == 2  # a JWT has three dot-separated parts
