import base64
from collections.abc import AsyncIterator
from typing import Any

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.phone.bridge import PhoneBridge
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    LiveEvent,
    OutputTranscript,
    ToolCall,
)


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent]) -> None:
        self._scripted = scripted
        self.audio_sent: list[bytes] = []
        self.tool_responses: list[tuple[str, str, dict[str, Any]]] = []

    async def send_audio(self, pcm16k: bytes) -> None:
        self.audio_sent.append(pcm16k)

    async def send_tool_response(self, call_id: str, name: str, response: dict[str, Any]) -> None:
        self.tool_responses.append((call_id, name, response))

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        return [KbArticle(title="W", content="5y", url="https://x")]


class _FakeLog:
    def __init__(self) -> None:
        self.ticket_calls: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str | None]] = []
        self.external_ids: list[tuple[str, str]] = []
        self.tags: list[tuple[str, str]] = []

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        self.ticket_calls.append((session_id, subject))
        return "T-1"

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        self.comments.append((ticket_id, text, status))

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        self.tags.append((ticket_id, tag))

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        self.external_ids.append((ticket_id, external_id))

    async def post_public_reply(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:  # unused here
        ...

    async def get_latest_public_comment(
        self, ticket_id: str
    ) -> tuple[str, str | None, str | None]:  # unused here
        return ("", None, None)


def _bridge(
    live: _FakeLive, sent: list[dict[str, object]], log: _FakeLog | None = None
) -> PhoneBridge:
    async def send_twilio(msg: dict[str, object]) -> None:
        sent.append(msg)

    return PhoneBridge(live, _FakeKnowledge(), log or _FakeLog(), send_twilio)


async def test_handle_start_records_sids() -> None:
    live = _FakeLive([])
    b = _bridge(live, [])
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1", "callSid": "C1"}})
    assert b.stream_sid == "S1"
    assert b.call_sid == "C1"


async def test_handle_media_forwards_decoded_audio_to_live() -> None:
    live = _FakeLive([])
    b = _bridge(live, [])
    payload = base64.b64encode(b"\xff" * 160).decode()
    await b.handle_twilio({"event": "media", "media": {"payload": payload}})
    assert len(live.audio_sent) == 1
    assert len(live.audio_sent[0]) == 640  # 160 μ-law → 320 samples @16k * 2 bytes


async def test_pump_audio_out_sends_twilio_media_frame() -> None:
    live = _FakeLive([AudioOut(b"\x00\x00" * 240)])  # 240 samples @24k
    sent: list[dict[str, object]] = []
    b = _bridge(live, sent)
    b.stream_sid = "S1"
    await b.pump()
    media = [m for m in sent if m.get("event") == "media"]
    assert media and media[0]["streamSid"] == "S1"
    inner = media[0]["media"]
    assert isinstance(inner, dict) and inner["payload"]  # base64 μ-law


async def test_pump_tool_call_dispatches_kb_and_responds() -> None:
    live = _FakeLive([ToolCall(id="c1", name="kb_search", args={"query": "warranty"})])
    b = _bridge(live, [])
    await b.pump()
    assert live.tool_responses
    _call_id, name, response = live.tool_responses[0]
    assert name == "kb_search"
    assert response["results"][0]["title"] == "W"


async def test_pump_interrupted_sends_twilio_clear() -> None:
    live = _FakeLive([Interrupted()])
    sent: list[dict[str, object]] = []
    b = _bridge(live, sent)
    b.stream_sid = "S1"
    await b.pump()
    assert {"event": "clear", "streamSid": "S1"} in sent


async def test_pump_accumulates_transcript() -> None:
    live = _FakeLive([InputTranscript("hi"), OutputTranscript("hello there")])
    b = _bridge(live, [])
    await b.pump()
    assert ("USER", "hi") in b.transcript
    assert ("ASSISTANT", "hello there") in b.transcript


async def test_finalize_writes_transcript_to_zendesk() -> None:
    log = _FakeLog()
    live = _FakeLive([])
    b = _bridge(live, [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "hi"), ("ASSISTANT", "hello")]
    await b.finalize()
    assert log.ticket_calls and log.ticket_calls[0][0] == "phone-C1"
    assert log.external_ids == [("T-1", "phone-C1")]
    assert log.comments and log.comments[0][2] == "solved"
    assert "USER: hi" in log.comments[0][1]


async def test_finalize_noop_without_transcript() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    await b.finalize()
    assert log.ticket_calls == []


async def test_pump_unknown_tool_responds_with_error() -> None:
    live = _FakeLive([ToolCall(id="c9", name="mystery_tool", args={})])
    b = _bridge(live, [])
    await b.pump()
    assert live.tool_responses
    call_id, name, response = live.tool_responses[0]
    assert call_id == "c9"
    assert name == "mystery_tool"
    assert "error" in response


async def test_finalize_noop_without_call_sid() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.transcript = [("USER", "hi")]
    # call_sid stays None
    await b.finalize()
    assert log.ticket_calls == []


async def test_pump_coalesces_same_role_transcript_fragments() -> None:
    live = _FakeLive(
        [
            InputTranscript("what is"),
            InputTranscript(" the warranty"),
            OutputTranscript("five"),
            OutputTranscript(" years"),
        ]
    )
    b = _bridge(live, [])
    await b.pump()
    assert b.transcript == [("USER", "what is the warranty"), ("ASSISTANT", "five years")]


async def test_pump_request_handoff_sets_state_and_responds() -> None:
    live = _FakeLive(
        [
            ToolCall(
                id="h1",
                name="request_human_handoff",
                args={"reason": "billing", "summary": "double charge"},
            )
        ]
    )
    b = _bridge(live, [])
    await b.pump()
    assert b.handoff == {"reason": "billing", "summary": "double charge"}
    assert live.tool_responses and live.tool_responses[0][0] == "h1"


async def test_pump_submit_csat_sets_score() -> None:
    live = _FakeLive([ToolCall(id="c1", name="submit_csat", args={"score": 5})])
    b = _bridge(live, [])
    await b.pump()
    assert b.csat_score == 5
    assert live.tool_responses and live.tool_responses[0][1] == "submit_csat"


async def test_pump_submit_csat_ignores_out_of_range() -> None:
    live = _FakeLive([ToolCall(id="c2", name="submit_csat", args={"score": 9})])
    b = _bridge(live, [])
    await b.pump()
    assert b.csat_score is None
    assert live.tool_responses  # still answered so the Live turn isn't stalled


async def test_finalize_handoff_opens_ticket_with_note_and_no_csat() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "I want a human")]
    b.handoff = {"reason": "complaint", "summary": "angry about delay"}
    b.csat_score = 5  # must be ignored on a handoff
    await b.finalize()
    # the handoff note + transcript are posted; the handoff note carries status "open"
    assert any("[Handoff to human agent]" in c[1] for c in log.comments)
    assert any(c[2] == "open" for c in log.comments)
    assert log.external_ids == [("T-1", "phone-C1")]
    assert log.tags == []  # NO csat tag on a handoff


async def test_finalize_resolved_with_score_solves_and_records_csat() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "thanks"), ("ASSISTANT", "you're welcome")]
    b.csat_score = 4
    await b.finalize()
    assert any(c[2] == "solved" for c in log.comments)
    assert ("T-1", "csat_4") in log.tags
    assert any("Customer satisfaction: 4/5 (via phone)" in c[1] for c in log.comments)


async def test_finalize_resolved_without_score_solves_no_csat() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "ok")]
    await b.finalize()
    assert any(c[2] == "solved" for c in log.comments)
    assert log.tags == []
