import base64
import json
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.phone.token import mint_voice_token
from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import Settings, get_settings
from chatbot.platform.server import create_app

ALLOWED_ORIGIN = "http://localhost:5173"


def _twilio_settings() -> Settings:
    s = get_settings()
    s.twilio_account_sid = "AC" + "x" * 32
    s.twilio_api_key_sid = "SK" + "x" * 32
    s.twilio_api_key_secret = "secretsecretsecretsecretsecretse"
    s.twilio_twiml_app_sid = "AP" + "x" * 32
    s.frontend_origins = [ALLOWED_ORIGIN]
    s.phone_token_ttl_seconds = 300
    s.phone_token_rate_limit = 10
    s.phone_token_rate_window_seconds = 60
    return s


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def _orch() -> MagicMock:
    orch = MagicMock()
    orch._settings = _twilio_settings()
    return orch


def _jwt_payload(jwt: str) -> dict[str, Any]:
    seg = jwt.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(seg))
    return payload


def test_token_endpoint_returns_jwt_and_identity() -> None:
    res = _client(_orch()).post("/voice/phone/token", headers={"Origin": ALLOWED_ORIGIN})
    assert res.status_code == 200
    body = res.json()
    assert body["identity"]
    assert body["token"].count(".") == 2  # a JWT has three dot-separated parts


def test_minted_token_uses_configured_short_ttl() -> None:
    s = _twilio_settings()
    s.phone_token_ttl_seconds = 120
    payload = _jwt_payload(mint_voice_token(s, "ident"))
    assert payload["exp"] - payload["nbf"] == 120  # not the Twilio 3600s default


def test_token_rejected_for_disallowed_origin() -> None:
    res = _client(_orch()).post("/voice/phone/token", headers={"Origin": "https://evil.example"})
    assert res.status_code == 403


def test_token_rejected_when_origin_missing() -> None:
    res = _client(_orch()).post("/voice/phone/token")
    assert res.status_code == 403


def test_token_rate_limited_per_client() -> None:
    orch = _orch()
    orch._settings.phone_token_rate_limit = 3
    client = _client(orch)
    codes = [
        client.post("/voice/phone/token", headers={"Origin": ALLOWED_ORIGIN}).status_code
        for _ in range(4)
    ]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429  # 4th within the window is throttled
