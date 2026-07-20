from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

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
    return base64.b64encode(hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()


def _setup() -> tuple[TestClient, OrchestratorService, AsyncMock]:
    settings = get_settings()
    settings.twilio_auth_token = "tok"
    settings.twilio_account_sid = "AC1"
    settings.twilio_whatsapp_number = "whatsapp:+60111"
    settings.twilio_webhook_base_url = ""
    orch = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        runner_factory=lambda _a: _FakeRunner(),
    )
    twilio = AsyncMock()
    app = create_app(settings)
    app.include_router(build_chat_router(orch, twilio_adapter=twilio))
    return TestClient(app), orch, twilio


def _post(client: TestClient, body: str, frm: str = "whatsapp:+60123") -> None:
    params = {"From": frm, "Body": body, "MessageSid": "SM1"}
    sig = _sign("tok", "http://testserver/webhooks/twilio-whatsapp", params)
    res = client.post("/webhooks/twilio-whatsapp", data=params, headers={"X-Twilio-Signature": sig})
    assert res.status_code == 200


def test_paused_session_forwards_and_skips_ai() -> None:
    client, orch, twilio = _setup()
    orch.handle_turn = AsyncMock()  # type: ignore[method-assign]
    orch.whatsapp_state = AsyncMock(return_value="paused")  # type: ignore[method-assign]
    orch.forward_whatsapp_to_agent = AsyncMock()  # type: ignore[method-assign]

    _post(client, "any update?")

    orch.handle_turn.assert_not_awaited()
    orch.forward_whatsapp_to_agent.assert_awaited_once()
    twilio.send_message.assert_not_awaited()


def test_survey_valid_rating_records_and_thanks() -> None:
    client, orch, twilio = _setup()
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")  # type: ignore[method-assign]
    orch.record_csat = AsyncMock(return_value=True)  # type: ignore[method-assign]

    _post(client, "5")

    orch.record_csat.assert_awaited_once()
    call = orch.record_csat.await_args
    assert call is not None
    assert call.args[1] == 5
    twilio.send_message.assert_awaited()  # thank-you sent


def test_survey_nudge_on_first_invalid_response() -> None:
    """Non-rating first reply while awaiting_survey → _CSAT_NUDGE sent once."""
    from chatbot.features.chat.router import _CSAT_NUDGE  # noqa: PLC0415

    client, orch, twilio = _setup()
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")  # type: ignore[method-assign]
    orch.consume_survey_nudge = AsyncMock(return_value=True)  # type: ignore[method-assign]

    _post(client, "hello")

    orch.consume_survey_nudge.assert_awaited_once()
    twilio.send_message.assert_awaited_once_with(
        conversation_id="whatsapp:+60123", text=_CSAT_NUDGE
    )


def test_survey_resume_on_second_invalid_response() -> None:
    """Second non-rating reply → resume_ai + handle_turn executed (back to AI)."""
    from chatbot.features.chat.models import TurnResult  # noqa: PLC0415

    client, orch, _twilio = _setup()
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")  # type: ignore[method-assign]
    orch.consume_survey_nudge = AsyncMock(return_value=False)  # type: ignore[method-assign]
    orch.resume_ai = AsyncMock()  # type: ignore[method-assign]
    orch.handle_turn = AsyncMock(return_value=TurnResult(reply="How can I help?"))  # type: ignore[method-assign]
    orch.capture_conversation = AsyncMock()  # type: ignore[method-assign]

    _post(client, "hello again")

    orch.resume_ai.assert_awaited_once()
    orch.handle_turn.assert_awaited_once()
    orch.capture_conversation.assert_awaited_once()
