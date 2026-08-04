import asyncio
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
from chatbot.features.chat.ports import ConversationLogResult
from chatbot.platform.config import Settings


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent]) -> None:
        self._scripted = scripted
        self.audio_sent: list[bytes] = []
        self.tool_responses: list[tuple[str, str, dict[str, Any]]] = []
        self.text_hints: list[str] = []

    async def send_audio(self, pcm16k: bytes) -> None:
        self.audio_sent.append(pcm16k)

    async def send_tool_response(self, call_id: str, name: str, response: dict[str, Any]) -> None:
        self.tool_responses.append((call_id, name, response))

    async def send_text_hint(self, text: str) -> None:
        self.text_hints.append(text)

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        return [KbArticle(title="W", content="5y", url="https://x")]


class _FakeLog:
    def __init__(self) -> None:
        self.ticket_calls: list[tuple[str, str]] = []
        self.ensured: list[str] = []
        self.ensure_raises: Exception | None = None
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
        self.ensured.append(session_id)
        if self.ensure_raises is not None:
            raise self.ensure_raises
        return "T-1"

    async def rotate_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        self.ticket_calls.append((session_id, subject))
        return "T-2"

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        self.comments.append((ticket_id, text, status))
        return ConversationLogResult.OK

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
    live: _FakeLive,
    sent: list[dict[str, object]],
    log: _FakeLog | None = None,
    settings: Settings | None = None,
    clock: Any | None = None,
) -> PhoneBridge:
    async def send_twilio(msg: dict[str, object]) -> None:
        sent.append(msg)

    return PhoneBridge(
        live,
        _FakeKnowledge(),
        log or _FakeLog(),
        send_twilio,
        settings or Settings(_env_file=None),
        clock=clock,
    )


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


async def test_pump_sends_language_hint_after_input_transcript_when_enabled() -> None:
    live = _FakeLive([InputTranscript("Saya nak tanya")])
    b = _bridge(live, [], settings=Settings(_env_file=None, phone_language_nudge_enabled=True))
    await b.pump()
    assert len(live.text_hints) == 1
    assert "language" in live.text_hints[0].lower()


async def test_pump_skips_language_hint_when_disabled() -> None:
    live = _FakeLive([InputTranscript("Saya nak tanya")])
    b = _bridge(live, [], settings=Settings(_env_file=None, phone_language_nudge_enabled=False))
    await b.pump()
    assert live.text_hints == []


async def test_pump_sends_language_hint_once_per_utterance_across_fragments() -> None:
    # A single caller utterance streamed as two InputTranscript deltas must
    # only trigger one language-nudge hint, not one per fragment.
    live = _FakeLive([InputTranscript("Saya"), InputTranscript(" nak tanya")])
    b = _bridge(live, [], settings=Settings(_env_file=None, phone_language_nudge_enabled=True))
    await b.pump()
    assert len(live.text_hints) == 1


async def test_pump_sends_second_language_hint_for_new_utterance_after_assistant_turn() -> None:
    # A genuinely new caller turn (after the assistant has replied) must
    # still trigger its own hint.
    live = _FakeLive(
        [
            InputTranscript("Saya nak tanya"),
            OutputTranscript("Baik, boleh saya bantu?"),
            InputTranscript("Terima kasih"),
        ]
    )
    b = _bridge(live, [], settings=Settings(_env_file=None, phone_language_nudge_enabled=True))
    await b.pump()
    assert len(live.text_hints) == 2


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
    assert live.tool_responses[0][2] == {"status": "ticket_created"}


async def test_pump_submit_csat_sets_score() -> None:
    live = _FakeLive([ToolCall(id="c1", name="submit_csat", args={"score": 5})])
    b = _bridge(live, [])
    await b.pump()
    assert b.csat_score == 5
    assert live.tool_responses and live.tool_responses[0][1] == "submit_csat"
    assert live.tool_responses[0][2] == {"status": "recorded"}


async def test_pump_submit_csat_ignores_out_of_range() -> None:
    live = _FakeLive([ToolCall(id="c2", name="submit_csat", args={"score": 9})])
    b = _bridge(live, [])
    await b.pump()
    assert b.csat_score is None
    assert live.tool_responses  # still answered so the Live turn isn't stalled
    assert live.tool_responses[0][2] == {"status": "ignored"}


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
    assert any("I want a human" in c[1] for c in log.comments)


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


# --- Task 3: create the ticket at call start, stream the transcript live ---


async def test_ticket_is_created_on_stream_start_when_enabled() -> None:
    log = _FakeLog()
    b = _bridge(
        _FakeLive([]),
        [],
        log,
        settings=Settings(_env_file=None, phone_transcript_live_enabled=True),
    )
    await b.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert log.ensured == ["phone-CA1"]
    assert b.ticket_id is not None


async def test_ticket_is_not_created_when_flag_off() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)  # default settings: flag off
    await b.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert log.ensured == []
    assert b.ticket_id is None


async def test_ticket_creation_failure_does_not_break_the_call() -> None:
    log = _FakeLog()
    log.ensure_raises = RuntimeError("chatwoot down")
    b = _bridge(
        _FakeLive([]),
        [],
        log,
        settings=Settings(_env_file=None, phone_transcript_live_enabled=True),
    )
    await b.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert b.ticket_id is None
    # The call must keep going: a subsequent media frame must not raise.
    payload = base64.b64encode(b"\xff" * 160).decode()
    await b.handle_twilio({"event": "media", "media": {"payload": payload}})


async def test_finalize_is_idempotent_after_live_creation() -> None:
    log = _FakeLog()
    b = _bridge(
        _FakeLive([]),
        [],
        log,
        settings=Settings(_env_file=None, phone_transcript_live_enabled=True),
    )
    await b.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    b.transcript = [("USER", "hi")]
    await b.finalize()
    # ensure_conversation_ticket must NOT be called a second time in
    # finalize(): the ticket already created at call-start is reused.
    assert log.ensured == ["phone-CA1"]


async def test_ticket_not_recreated_on_a_reconnecting_start_event() -> None:
    """Twilio Media Streams can reconnect mid-call and resend "start". That
    must not spawn a second ensure_conversation_ticket lookup once we already
    hold a ticket_id for this call."""
    log = _FakeLog()
    b = _bridge(
        _FakeLive([]),
        [],
        log,
        settings=Settings(_env_file=None, phone_transcript_live_enabled=True),
    )
    await b.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    await b.handle_twilio({"event": "start", "start": {"streamSid": "MZ2", "callSid": "CA1"}})
    assert log.ensured == ["phone-CA1"]


async def test_pump_posts_a_completed_turn_to_the_ticket_when_live() -> None:
    live = _FakeLive([InputTranscript("hi there"), OutputTranscript("hello")])
    log = _FakeLog()
    b = _bridge(
        live,
        [],
        log,
        settings=Settings(_env_file=None, phone_transcript_live_enabled=True),
    )
    b.ticket_id = "T-1"
    await b.pump()
    await asyncio.sleep(0)  # let the detached flush task run
    await asyncio.sleep(0)
    assert ("T-1", "USER: hi there", None) in log.comments


async def test_pump_does_not_touch_the_ticket_when_flag_off() -> None:
    live = _FakeLive([InputTranscript("hi there"), OutputTranscript("hello")])
    log = _FakeLog()
    b = _bridge(live, [], log)  # default settings: flag off
    b.ticket_id = "T-1"
    await b.pump()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert log.comments == []


async def test_pump_flushes_a_due_block_on_a_non_transcript_tick() -> None:
    """The sink has no clock of its own -- it only becomes "due" when
    something calls take_if_due(). If pump() only polled it from inside the
    InputTranscript/OutputTranscript branches, a block sitting in the sink
    would never flush during a stretch of audio-only events (e.g. the
    assistant's own speech). Here the flush-due condition (timer elapsed)
    only becomes true on the SECOND poll, which happens on an AudioOut
    event, not a transcript event -- so this fails if the per-tick poll is
    removed or narrowed to transcript branches only."""
    now_values = iter([0.0, 0.0, 25.0, 25.0])
    live = _FakeLive([InputTranscript("first turn"), AudioOut(b"\x00\x00" * 240)])
    log = _FakeLog()
    b = _bridge(
        live,
        [],
        log,
        settings=Settings(
            _env_file=None, phone_transcript_live_enabled=True, phone_transcript_flush_seconds=10.0
        ),
        clock=lambda: next(now_values),
    )
    b.ticket_id = "T-1"
    b.stream_sid = "S1"
    await b.pump()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert ("T-1", "USER: first turn", None) in log.comments


async def test_finalize_flushes_the_trailing_live_block_without_duplicating_it() -> None:
    """A call that ends mid-turn must not lose the trailing (still-open)
    turn, but the sink's own take_if_due() is what guarantees it isn't
    double-posted: it empties exactly what it returns, so calling it once
    more in finalize() can only surface it once. The separate whole-
    transcript summary comment (status="solved") that finalize() has always
    posted is expected too -- it's not a duplicate of the live block, it's
    today's end-of-call record, unchanged."""
    live = _FakeLive([InputTranscript("hi"), InputTranscript(" there")])
    log = _FakeLog()
    b = _bridge(
        live,
        [],
        log,
        settings=Settings(_env_file=None, phone_transcript_live_enabled=True),
    )
    b.ticket_id = "T-1"
    await b.pump()  # single in-progress USER turn: nothing due yet, held back
    b.call_sid = "C1"
    await b.finalize()
    live_posts = [c for c in log.comments if c[2] is None]
    assert live_posts == [("T-1", "USER: hi there", None)]
    assert any(c[2] == "solved" for c in log.comments)


async def test_finalize_posts_no_live_block_when_call_ends_before_anyone_speaks() -> None:
    log = _FakeLog()
    b = _bridge(
        _FakeLive([]),
        [],
        log,
        settings=Settings(_env_file=None, phone_transcript_live_enabled=True),
    )
    b.ticket_id = "T-1"
    b.call_sid = "C1"
    # No transcript at all -> finalize() is a no-op past the live-flush step.
    await b.finalize()
    assert log.comments == []
