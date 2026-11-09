"""Brief 6: the routes hand the customer's number to the orchestrator.

/chat/turn and /voice/turn forward a console persona number (the service
allowlists it); the Twilio-signed WhatsApp webhook forwards the sender's
number as trusted. Absent / flag off -> the call is exactly as before.
"""

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
from chatbot.features.chat.models import TurnResult
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app

PERSONA = "+60000000101"


class _FakeRunner:
    async def run_async(self, **_: Any) -> AsyncGenerator[Any, None]:
        for _i in range(0):
            yield None


def _client(**flags: Any) -> tuple[TestClient, OrchestratorService]:
    settings = get_settings().model_copy(
        update={
            "twilio_auth_token": "test_token",
            "twilio_account_sid": "AC1",
            "twilio_whatsapp_number": "whatsapp:+60111",
            "twilio_webhook_base_url": "",
            **flags,
        }
    )
    orch = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _agent: _FakeRunner(),
    )
    orch.handle_turn = AsyncMock(return_value=TurnResult(reply="ok"))  # type: ignore[method-assign]
    orch.handle_voice_turn = AsyncMock(return_value=(b"mp3", TurnResult(reply="ok")))  # type: ignore[method-assign]
    app = create_app(settings)
    app.include_router(build_chat_router(orch, twilio_adapter=AsyncMock()))
    return TestClient(app), orch


def test_chat_turn_forwards_a_persona_number() -> None:
    client, orch = _client()
    res = client.post(
        "/chat/turn", json={"session_id": "sim-1", "text": "hi", "persona_phone": PERSONA}
    )
    assert res.status_code == 200
    assert orch.handle_turn.await_args.kwargs["persona_phone"] == PERSONA  # type: ignore[attr-defined]


def test_chat_turn_without_persona_is_the_old_call() -> None:
    client, orch = _client()
    client.post("/chat/turn", json={"session_id": "sim-1", "text": "hi"})
    assert "persona_phone" not in orch.handle_turn.await_args.kwargs  # type: ignore[attr-defined]


def test_voice_turn_forwards_a_persona_number() -> None:
    client, orch = _client()
    res = client.post(
        "/voice/turn",
        data={"session_id": "voice-1", "persona_phone": PERSONA},
        files={"audio": ("clip.ogg", b"\x00" * 64, "audio/ogg")},
    )
    assert res.status_code == 200
    assert orch.handle_voice_turn.await_args.kwargs["persona_phone"] == PERSONA  # type: ignore[attr-defined]


def test_voice_turn_without_persona_is_the_old_call() -> None:
    client, orch = _client()
    client.post(
        "/voice/turn",
        data={"session_id": "voice-1"},
        files={"audio": ("clip.ogg", b"\x00" * 64, "audio/ogg")},
    )
    assert "persona_phone" not in orch.handle_voice_turn.await_args.kwargs  # type: ignore[attr-defined]


def _whatsapp(client: TestClient) -> None:
    params = {"From": "whatsapp:+60000000101", "Body": "hi", "MessageSid": "SM1"}
    url = "http://testserver/webhooks/twilio-whatsapp"
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    sig = base64.b64encode(hmac.new(b"test_token", data.encode(), hashlib.sha1).digest()).decode()
    res = client.post("/webhooks/twilio-whatsapp", data=params, headers={"X-Twilio-Signature": sig})
    assert res.status_code == 200


def test_whatsapp_webhook_passes_the_signed_sender_number() -> None:
    client, orch = _client(chat_turn_caller_context_enabled=True)
    _whatsapp(client)
    kwargs = orch.handle_turn.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["session_id"] == "whatsapp-+60000000101"
    assert kwargs["customer_phone"] == "+60000000101"


def test_whatsapp_webhook_flag_off_is_the_old_call() -> None:
    client, orch = _client(chat_turn_caller_context_enabled=False)
    _whatsapp(client)
    assert orch.handle_turn.await_args.kwargs == {  # type: ignore[attr-defined]
        "session_id": "whatsapp-+60000000101",
        "text": "hi",
    }


# --- fix round 1: server-owned sessions (R49), WhatsApp without a token (M-5)

KEY = "backend-key"


def _keyed() -> tuple[TestClient, OrchestratorService]:
    return _client(proton_backend_key=KEY)


@pytest.mark.parametrize(
    "session_id",
    ["whatsapp-+60000000101", "crm-1", "email-9", "phone-CA1", "zendesk-conv-1", "chatwoot-conv-3"],
)
def test_browser_cannot_use_a_server_owned_session(session_id: str) -> None:
    client, orch = _keyed()
    res = client.post("/chat/turn", json={"session_id": session_id, "text": "who am I?"})
    assert res.status_code == 403
    res = client.post(
        "/chat/turn",
        json={"session_id": session_id, "text": "hi"},
        headers={"x-api-key": "wrong"},
    )
    assert res.status_code == 403
    orch.handle_turn.assert_not_awaited()  # type: ignore[attr-defined]


def test_browser_cannot_use_a_server_owned_voice_session() -> None:
    client, orch = _keyed()
    res = client.post(
        "/voice/turn",
        data={"session_id": "whatsapp-+60000000101"},
        files={"audio": ("clip.ogg", b"\x00" * 64, "audio/ogg")},
    )
    assert res.status_code == 403
    orch.handle_voice_turn.assert_not_awaited()  # type: ignore[attr-defined]


def test_the_agent_service_credential_may_use_crm_sessions() -> None:
    client, orch = _keyed()
    res = client.post(
        "/chat/turn", json={"session_id": "crm-42", "text": "hi"}, headers={"x-api-key": KEY}
    )
    assert res.status_code == 200
    assert orch.handle_turn.await_args.kwargs["session_id"] == "crm-42"  # type: ignore[attr-defined]


def test_no_backend_key_configured_fails_closed() -> None:
    client, _ = _client(proton_backend_key="")
    res = client.post("/chat/turn", json={"session_id": "crm-42", "text": "hi"})
    assert res.status_code == 403


def test_whatsapp_without_a_twilio_token_never_claims_a_number() -> None:
    client, orch = _client(chat_turn_caller_context_enabled=True, twilio_auth_token="")
    params = {"From": "whatsapp:+60000000101", "Body": "hi", "MessageSid": "SM1"}
    client.post("/webhooks/twilio-whatsapp", data=params)
    assert "customer_phone" not in orch.handle_turn.await_args.kwargs  # type: ignore[attr-defined]
