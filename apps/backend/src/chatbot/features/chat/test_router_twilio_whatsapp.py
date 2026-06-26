from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeRunner:
    async def run_async(self, **_: Any) -> AsyncGenerator[Any, None]:
        for _i in range(0):
            yield None


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = hmac.new(token.encode(), s.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


@pytest.fixture
def setup() -> tuple[TestClient, AsyncMock, str]:
    settings = get_settings()
    settings.twilio_auth_token = "test_token"
    settings.twilio_account_sid = "AC1"
    settings.twilio_whatsapp_number = "whatsapp:+60111"

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )
    twilio = AsyncMock()
    app = create_app(settings)
    app.include_router(build_chat_router(orchestrator, twilio_adapter=twilio))
    return TestClient(app), twilio, "test_token"


def test_rejects_bad_signature(setup: tuple[TestClient, AsyncMock, str]) -> None:
    client, _twilio, _token = setup
    res = client.post(
        "/webhooks/twilio-whatsapp",
        data={"From": "whatsapp:+60123", "Body": "hi", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "wrong"},
    )
    assert res.status_code == 401


def test_valid_request_runs_turn_and_replies(setup: tuple[TestClient, AsyncMock, str]) -> None:
    client, twilio, token = setup
    params = {"From": "whatsapp:+60123", "Body": "hi", "MessageSid": "SM1"}
    url = "http://testserver/webhooks/twilio-whatsapp"
    sig = _sign(token, url, params)

    res = client.post("/webhooks/twilio-whatsapp", data=params, headers={"X-Twilio-Signature": sig})

    assert res.status_code == 200
    # Empty FakeRunner → fallback reply is produced and sent via Twilio.
    assert twilio.send_message.await_count == 1
    assert twilio.send_message.await_args.kwargs["conversation_id"] == "whatsapp:+60123"


def test_signature_verified_against_public_base_url(
    setup: tuple[TestClient, AsyncMock, str],
) -> None:
    # Behind a tunnel the request arrives as http://testserver/... but Twilio
    # signed the public https URL. With TWILIO_WEBHOOK_BASE_URL set, the handler
    # must verify against that base, not request.url.
    client, twilio, token = setup
    settings = get_settings()
    base = "https://sky-analyzed-income-witnesses.trycloudflare.com"
    settings.twilio_webhook_base_url = base
    try:
        params = {"From": "whatsapp:+60123", "Body": "hi", "MessageSid": "SM1"}
        sig = _sign(token, f"{base}/webhooks/twilio-whatsapp", params)

        res = client.post(
            "/webhooks/twilio-whatsapp", data=params, headers={"X-Twilio-Signature": sig}
        )

        assert res.status_code == 200
        assert twilio.send_message.await_count == 1
    finally:
        settings.twilio_webhook_base_url = ""
