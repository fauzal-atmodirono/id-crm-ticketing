"""P8 task 5: NPS wiring -- the sampling gate, the WhatsApp/email/phone
survey-flow integration, and end-to-end agent attribution.

See ``nps.py``'s module docstring for the sampling design: the sampling
unit is the CONVERSATION (never the message), decided once per conversation
by a deterministic hash of its session id, so a re-nudge or a retried
webhook delivery always lands on the same question.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.nps import parse_nps, should_survey_nps
from chatbot.features.chat.phone.bridge import PhoneBridge
from chatbot.features.chat.phone.live_events import LiveEvent, ToolCall
from chatbot.features.chat.ports import ConversationLogResult
from chatbot.features.chat.router import (
    _EMAIL_CSAT_THANKS,
    _EMAIL_NPS_THANKS,
    _NPS_SURVEY_MESSAGE,
    _SURVEY_MESSAGE,
    build_chat_router,
)
from chatbot.features.metrics.mapping import map_chatwoot_conversation_to_row
from chatbot.platform.config import Settings, get_settings
from chatbot.platform.server import create_app

# --- should_survey_nps / parse_nps: pure sampling + parsing ----------------

_KEYS = ("whatsapp-+60123", "email-55", "phone-CA1", "sim-abc", "")


def test_a_sample_rate_of_zero_asks_no_nps_question() -> None:
    for key in _KEYS:
        assert should_survey_nps(key, 0.0) is False


def test_a_sample_rate_of_one_asks_nps_instead_of_csat() -> None:
    for key in _KEYS:
        assert should_survey_nps(key, 1.0) is True


def test_an_out_of_range_score_is_rejected_not_clamped() -> None:
    assert parse_nps("15") is None
    assert parse_nps("a rating of 11 please") is None
    assert parse_nps("no numbers here") is None
    assert parse_nps("9") == 9
    assert parse_nps("0") == 0


# --- WhatsApp survey flow: router-level dispatch ----------------------------


def _whatsapp_orch(nps_sample_rate: float) -> MagicMock:
    orch = MagicMock()
    orch._settings = Settings(_env_file=None, nps_sample_rate=nps_sample_rate)
    orch._conversation_log_port = MagicMock()
    orch._conversation_log_port.find_conversation_ticket = AsyncMock(return_value=None)
    orch._conversation_log_port.get_conversation_assignee = AsyncMock(return_value=None)
    orch._conversation_log_port.add_ticket_tag = AsyncMock()
    orch.record_csat = AsyncMock(return_value=True)
    orch.record_nps = AsyncMock(return_value=True)
    orch.consume_survey_nudge = AsyncMock(return_value=True)
    orch.resume_ai = AsyncMock()
    orch.begin_survey = AsyncMock()
    orch._ticketing_port = MagicMock()
    orch._ticketing_port.unpause_ai_for_session = AsyncMock()
    return orch


def _whatsapp_client(orch: MagicMock, twilio: AsyncMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch, twilio_adapter=twilio))
    return TestClient(app)


def _post_whatsapp(client: TestClient, body: str, frm: str = "whatsapp:+60123") -> None:
    res = client.post(
        "/webhooks/twilio-whatsapp", data={"From": frm, "Body": body, "MessageSid": "SM1"}
    )
    assert res.status_code == 200


def test_handback_sends_csat_survey_message_when_sample_rate_is_zero() -> None:
    """Integration companion to the pure test above: with the flag off, the
    end-of-conversation survey is byte-identical to pre-P8 -- CSAT, never NPS."""
    orch = _whatsapp_orch(0.0)
    orch.conversation_state = AsyncMock(return_value="paused")
    twilio = AsyncMock()
    client = _whatsapp_client(orch, twilio)

    res = client.post("/webhooks/zendesk-handback", json={"session_id": "whatsapp-+60123"})

    assert res.status_code == 200
    twilio.send_message.assert_awaited_once_with(
        conversation_id="whatsapp:+60123", text=_SURVEY_MESSAGE
    )


def test_handback_sends_nps_survey_message_when_sample_rate_is_one() -> None:
    orch = _whatsapp_orch(1.0)
    orch.conversation_state = AsyncMock(return_value="paused")
    twilio = AsyncMock()
    client = _whatsapp_client(orch, twilio)

    res = client.post("/webhooks/zendesk-handback", json={"session_id": "whatsapp-+60123"})

    assert res.status_code == 200
    twilio.send_message.assert_awaited_once_with(
        conversation_id="whatsapp:+60123", text=_NPS_SURVEY_MESSAGE
    )


def test_nps_replaces_csat_rather_than_being_appended_to_it() -> None:
    """Sampled in: the reply is parsed as NPS, record_csat is never touched,
    and exactly ONE thank-you is sent -- never both questions/acks for the
    one conversation."""
    orch = _whatsapp_orch(1.0)
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")
    twilio = AsyncMock()
    client = _whatsapp_client(orch, twilio)

    _post_whatsapp(client, "9")

    orch.record_nps.assert_awaited_once_with("whatsapp-+60123", 9, channel="WhatsApp")
    orch.record_csat.assert_not_awaited()
    twilio.send_message.assert_awaited_once()  # exactly one ack, not two


def test_the_score_is_attributed_to_the_agent_assigned_at_survey_time() -> None:
    orch = _whatsapp_orch(1.0)
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")
    orch._conversation_log_port.find_conversation_ticket = AsyncMock(return_value="T-1")
    orch._conversation_log_port.get_conversation_assignee = AsyncMock(return_value="agent-42")
    twilio = AsyncMock()
    client = _whatsapp_client(orch, twilio)

    _post_whatsapp(client, "9")

    orch._conversation_log_port.get_conversation_assignee.assert_awaited_once_with("T-1")
    orch._conversation_log_port.add_ticket_tag.assert_any_await("T-1", "nps_agent_agent-42")


def test_a_reassignment_after_the_survey_does_not_re_attribute_the_score() -> None:
    """The attribution tag, once written, is what `features.metrics.mapping`
    trusts -- NOT the conversation's live assignee. Simulates the exact
    scenario the tag exists for: the ticket is reassigned to a different
    agent after the survey was answered, and the reporting row must still
    credit the ORIGINAL agent."""
    row = map_chatwoot_conversation_to_row(
        {
            "id": 88,
            "status": "resolved",
            "labels": ["nps_9", "nps_agent_agent-42"],
            "created_at": 1750489200,
            "meta": {
                "sender": {"identifier": "whatsapp-+60123"},
                # the CURRENT (post-reassignment) assignee -- must be ignored.
                "assignee": {"id": 99},
            },
        }
    )
    assert row is not None
    assert row.nps_score == 9
    assert row.agent_id == "agent-42"  # not "99"


def test_an_invalid_nps_reply_nudges_instead_of_recording() -> None:
    orch = _whatsapp_orch(1.0)
    orch.whatsapp_state = AsyncMock(return_value="awaiting_survey")
    twilio = AsyncMock()
    client = _whatsapp_client(orch, twilio)

    _post_whatsapp(client, "15")  # out of the 0-10 NPS range

    orch.record_nps.assert_not_awaited()
    orch.consume_survey_nudge.assert_awaited_once_with("whatsapp-+60123")


# --- Email survey flow: same sampling gate, different transport ------------


def _email_orch(nps_sample_rate: float) -> MagicMock:
    orch = MagicMock()
    orch._settings = Settings(_env_file=None, nps_sample_rate=nps_sample_rate)
    orch._settings.zendesk_support_webhook_secret = ""
    orch._conversation_log_port = MagicMock()
    orch._conversation_log_port.post_public_reply = AsyncMock()
    orch._conversation_log_port.get_latest_public_comment = AsyncMock(return_value=("", None, None))
    orch._conversation_log_port.get_conversation_assignee = AsyncMock(return_value=None)
    orch._conversation_log_port.add_ticket_tag = AsyncMock()
    orch.record_csat = AsyncMock(return_value=True)
    orch.record_nps = AsyncMock(return_value=True)
    orch.consume_survey_nudge = AsyncMock(return_value=True)
    orch.resume_ai = AsyncMock()
    orch.bind_email_ticket = AsyncMock()
    orch.get_email_dedup = AsyncMock(return_value=(None, None))
    orch.remember_email_exchange = AsyncMock()
    orch._ticketing_port = MagicMock()
    orch._ticketing_port.unpause_ai_for_session = AsyncMock()
    return orch


def _email_client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_email_survey_asks_nps_and_attributes_the_answering_agent() -> None:
    orch = _email_orch(1.0)
    orch.conversation_state = AsyncMock(return_value="awaiting_survey")
    orch._conversation_log_port.get_conversation_assignee = AsyncMock(return_value="agent-7")
    client = _email_client(orch)

    res = client.post(
        "/webhooks/zendesk-email",
        json={"ticket_id": "55", "text": "9", "requester_email": "alice@acme.com"},
    )

    assert res.status_code == 200
    orch.record_nps.assert_awaited_once_with("email-55", 9, channel="email")
    orch.record_csat.assert_not_awaited()
    orch._conversation_log_port.add_ticket_tag.assert_any_await("55", "nps_agent_agent-7")
    orch._conversation_log_port.post_public_reply.assert_awaited_once_with(
        "55", _EMAIL_NPS_THANKS, status="solved"
    )


def test_email_survey_stays_csat_when_not_sampled() -> None:
    orch = _email_orch(0.0)
    orch.conversation_state = AsyncMock(return_value="awaiting_survey")
    client = _email_client(orch)

    res = client.post(
        "/webhooks/zendesk-email",
        json={"ticket_id": "55", "text": "4", "requester_email": "alice@acme.com"},
    )

    assert res.status_code == 200
    orch.record_csat.assert_awaited_once_with("email-55", 4, channel="email")
    orch.record_nps.assert_not_awaited()
    orch._conversation_log_port.post_public_reply.assert_awaited_once_with(
        "55", _EMAIL_CSAT_THANKS, status="solved"
    )


# --- Phone survey flow: PhoneBridge records via the submit_nps tool --------


class _PhoneFakeLog:
    """Minimal ConversationLogPort double for the phone finalize() path,
    extended with `get_conversation_assignee` for the P8 task 5 attribution
    write (mirrors phone/test_bridge.py's own `_FakeLog`, kept local here so
    that file is untouched)."""

    def __init__(self, assignee: str | None) -> None:
        self.comments: list[tuple[str, str, str | None]] = []
        self.tags: list[tuple[str, str]] = []
        self.external_ids: list[tuple[str, str]] = []
        self._assignee = assignee

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        return "T-9"

    async def rotate_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        return session_id

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        return None

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        self.comments.append((ticket_id, text, status))
        return ConversationLogResult.OK

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        self.tags.append((ticket_id, tag))

    async def post_public_reply(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:  # unused here
        return None

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        self.external_ids.append((ticket_id, external_id))

    async def set_ticket_classification(self, ticket_id: str, **_kw: Any) -> None:  # unused here
        return None

    async def get_latest_public_comment(
        self, ticket_id: str
    ) -> tuple[str, str | None, str | None]:  # unused here
        return ("", None, None)

    async def set_call_recording(self, ticket_id: str, **_kw: Any) -> None:  # unused here
        return None

    async def get_inbox_working_hours(self, inbox_id: int) -> dict[str, Any] | None:  # unused
        return None

    async def has_ticket_tag(self, ticket_id: str, tag: str) -> bool:
        return (ticket_id, tag) in self.tags

    async def get_conversation_assignee(self, ticket_id: str) -> str | None:
        return self._assignee


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent]) -> None:
        self._scripted = scripted
        self.tool_responses: list[tuple[str, str, dict[str, Any]]] = []

    async def send_audio(self, pcm16k: bytes) -> None:  # unused here
        return None

    async def send_tool_response(self, call_id: str, name: str, response: dict[str, Any]) -> None:
        self.tool_responses.append((call_id, name, response))

    async def send_text_hint(self, text: str) -> None:  # unused here
        return None

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


def test_the_phone_path_records_nps() -> None:
    log = _PhoneFakeLog(assignee="agent-3")
    live = _FakeLive([ToolCall(id="c1", name="submit_nps", args={"score": 9})])

    async def send_twilio(_msg: dict[str, object]) -> None:
        return None

    bridge = PhoneBridge(live, MagicMock(), log, send_twilio, Settings(_env_file=None))

    async def _run() -> None:
        await bridge.pump()
        bridge.call_sid = "CA1"
        bridge.transcript = [("USER", "thanks"), ("ASSISTANT", "you're welcome")]
        await bridge.finalize()

    asyncio.run(_run())

    assert bridge.nps_score == 9
    assert bridge.csat_score is None
    assert ("T-9", "nps_9") in log.tags
    assert ("T-9", "nps_agent_agent-3") in log.tags
    assert any("Net Promoter Score: 9/10 (via phone)" in c[1] for c in log.comments)
    # csat_<n> must never also appear -- NPS replaces it, not appends to it.
    assert not any(tag.startswith("csat_") for _tid, tag in log.tags)


# --- v_nps_by_agent's exact dimensions, populated end to end ---------------


def test_v_nps_by_agent_is_populated_end_to_end() -> None:
    """`v_nps_by_agent` (bigquery_schema.py) groups by day/agent_id/channel,
    counts respondents via `nps_score IS NOT NULL`, and is filtered to
    channel IN ('Phone', 'WhatsApp'). This drives the exact pipeline that
    feeds it -- survey answer -> tags -> row mapping -- for a WhatsApp
    conversation, and checks every dimension/measure the view reads."""
    row = map_chatwoot_conversation_to_row(
        {
            "id": 42,
            "status": "resolved",
            "labels": ["nps_9", "nps_agent_agent-42"],
            "created_at": 1750489200,  # 2025-06-21T09:00:00Z
            "meta": {"sender": {"identifier": "whatsapp-+60123"}, "assignee": {"id": "agent-42"}},
        }
    )
    assert row is not None
    assert row.channel == "WhatsApp"  # in v_nps_by_agent's channel IN (...) filter
    assert row.nps_score == 9  # respondents = COUNTIF(nps_score IS NOT NULL)
    assert row.agent_id == "agent-42"  # GROUP BY agent_id
    assert row.created_at is not None  # v_nps_by_agent's `day` column
