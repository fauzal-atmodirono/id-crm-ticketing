"""Turn-taking on the phone bridge (2026-11-09): one greeting, no double
answers, barge-in that really stops the old answer, the PDPA notice played
over the stream, and the per-call timing log.

Reproduces the live evidence with a fake Live session:
- 27 Oct (CAd8f6c6ee...): the assistant answered twice per caller turn --
  once to the caller, once to the bridge's own language reminder, which went
  out as a realtime TEXT input (= a caller turn to the Live API);
- after a barge-in the interrupted answer's tail ("...yang ingin anda") still
  reached the transcript / the caller next to the restarted answer.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any

from structlog.testing import capture_logs

from chatbot.features.chat.phone.bridge import PhoneBridge, is_unclear_transcript
from chatbot.features.chat.phone.handoff_target import HandoffTarget
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    LiveEvent,
    OutputTranscript,
    TurnComplete,
)
from chatbot.features.chat.phone.test_bridge import _FakeKnowledge, _FakeLog
from chatbot.platform.config import Settings


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class _Live:
    """Fake Live session. Each scripted item is an event, or a callable run
    at that point in the stream (to advance the clock mid-stream)."""

    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = scripted
        self.audio_sent: list[bytes] = []
        self.text_hints: list[str] = []
        self.greetings: list[str] = []
        self.tool_responses: list[Any] = []

    async def send_audio(self, pcm16k: bytes) -> None:
        self.audio_sent.append(pcm16k)

    async def send_tool_response(self, call_id: str, name: str, response: Any) -> None:
        self.tool_responses.append((call_id, name, response))

    async def send_text_hint(self, text: str) -> None:
        self.text_hints.append(text)

    async def send_greeting(self, text: str) -> None:
        self.greetings.append(text)

    async def events(self) -> AsyncIterator[LiveEvent]:
        for item in self._scripted:
            if callable(item):
                item()
                continue
            yield item


def _bridge(
    live: _Live,
    sent: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    clock: _Clock | None = None,
    log: _FakeLog | None = None,
    **kw: Any,
) -> PhoneBridge:
    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    b = PhoneBridge(
        live,  # type: ignore[arg-type]
        _FakeKnowledge(),
        log or _FakeLog(),  # type: ignore[arg-type]
        send,
        settings or Settings(_env_file=None, phone_language_nudge_enabled=True),
        clock=clock or _Clock(),
        **kw,
    )
    b.stream_sid = "S1"
    return b


def _media(sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in sent if m.get("event") == "media"]


AUDIO = AudioOut(b"\x01\x00" * 480)


# --- 27 Oct reproduction --------------------------------------------------------


async def test_27_oct_sequence_one_answer_per_caller_turn_and_no_stale_tail() -> None:
    clock = _Clock()

    def later(seconds: float):  # type: ignore[no-untyped-def]
        def advance() -> None:
            clock.t += seconds

        return advance

    live = _Live(
        [
            InputTranscript("Hallo."),
            AUDIO,
            OutputTranscript("Hello! How can I help you today?"),
            TurnComplete(),
            InputTranscript("Hai. Halo. Nak tanya Proton X50, harganya berapa?"),
            AUDIO,
            OutputTranscript("Harga Proton X50 bermula dari RM89,800. Ada yang lebih"),
            # background noise, transcribed as Hindi, barges in
            InputTranscript("जय माता दी।"),
            Interrupted(),
            # the cancelled answer's tail, still in flight: must not be heard
            AUDIO,
            OutputTranscript(" lanjut yang ingin anda"),
            later(2.0),
            AUDIO,
            OutputTranscript("Ada yang lain?"),
            TurnComplete(),
        ]
    )
    sent: list[dict[str, Any]] = []
    b = _bridge(live, sent, clock=clock)
    await b.pump()

    # no bridge-made caller turns: nothing the model could answer a second time
    assert live.text_hints == []
    # barge-in cleared Twilio's buffer and the stale tail was dropped
    assert {"event": "clear", "streamSid": "S1"} in sent
    assert len(_media(sent)) == 3
    assistant = [t for r, t in b.transcript if r == "ASSISTANT"]
    assert all("lanjut yang ingin anda" not in t for t in assistant)
    # the answer after the interruption is its own row, not glued onto the old one
    assert assistant[-1] == "Ada yang lain?"
    # the noise turn is kept on the record but marked
    assert ("USER", "(unclear) जय माता दी।") in b.transcript


async def test_new_answer_after_barge_in_is_forwarded_once_caller_speaks() -> None:
    live = _Live([AUDIO, Interrupted(), InputTranscript("Wait, the X70"), AUDIO])
    sent: list[dict[str, Any]] = []
    b = _bridge(live, sent)
    await b.pump()
    assert len(_media(sent)) == 2
    assert sent.index({"event": "clear", "streamSid": "S1"}) == 1


async def test_turn_complete_ends_the_drop_window() -> None:
    live = _Live([Interrupted(), TurnComplete(), AUDIO])
    sent: list[dict[str, Any]] = []
    await _bridge(live, sent).pump()
    assert len(_media(sent)) == 1


async def test_unclear_transcript_rules() -> None:
    assert is_unclear_transcript("जय माता दी।")
    assert is_unclear_transcript("...")
    assert is_unclear_transcript("  ")
    assert not is_unclear_transcript("Hello.")
    assert not is_unclear_transcript("Saya nak tanya harga X50")
    assert not is_unclear_transcript("请问X50多少钱")
    assert not is_unclear_transcript("e.MAS 7")


# --- greeting -------------------------------------------------------------------


async def test_greets_when_the_caller_has_not_spoken() -> None:
    live = _Live([])
    b = _bridge(live, [])
    assert await b.greet_if_caller_silent("Greet now.") is True
    assert live.greetings == ["Greet now."]
    # once only
    assert await b.greet_if_caller_silent("Greet now.") is False
    assert live.greetings == ["Greet now."]


async def test_no_greeting_once_the_caller_has_spoken() -> None:
    live = _Live([InputTranscript("Hello?")])
    b = _bridge(live, [])
    await b.pump()
    assert await b.greet_if_caller_silent("Greet now.") is False
    assert live.greetings == []


async def test_noise_does_not_count_as_the_caller_speaking() -> None:
    live = _Live([InputTranscript("...")])
    b = _bridge(live, [])
    await b.pump()
    assert await b.greet_if_caller_silent("Greet now.") is True


# --- the PDPA notice over the stream ---------------------------------------------


def _rec_settings(**kw: Any) -> Settings:
    return Settings(
        _env_file=None,
        phone_transcript_live_enabled=True,
        phone_recording_enabled=True,
        phone_recording_announcement="This call is recorded.",
        twilio_webhook_base_url="https://example.test",
        **kw,
    )


class _CallControl:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_recording(self, call_sid: str, callback: str) -> str:
        self.started.append(call_sid)
        return "RE1"

    async def redirect(self, call_sid: str, twiml: str) -> bool:
        return False


async def test_model_audio_waits_for_the_notice_and_recording_starts_after_it() -> None:
    cc = _CallControl()
    live = _Live([AUDIO])
    sent: list[dict[str, Any]] = []
    b = _bridge(
        live,
        sent,
        settings=_rec_settings(),
        call_control=cc,  # type: ignore[arg-type]
        announcement_mark="pdpa_notice",
        announcement_seconds=3.0,
    )
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1", "callSid": "CA1"}})
    await b.pump()
    assert _media(sent) == []  # held behind the notice
    assert cc.started == []  # PDPA: never before the notice has played
    await b.handle_twilio({"event": "mark", "mark": {"name": "pdpa_notice"}})
    await b.finalize()
    assert len(_media(sent)) == 1
    assert cc.started == ["CA1"]


async def test_barge_in_during_the_notice_never_clears_it() -> None:
    live = _Live([AUDIO, Interrupted(), InputTranscript("hello"), AUDIO])
    sent: list[dict[str, Any]] = []
    b = _bridge(live, sent, announcement_mark="pdpa_notice", announcement_seconds=3.0)
    await b.pump()
    assert not [m for m in sent if m.get("event") == "clear"]
    await b.handle_twilio({"event": "mark", "mark": {"name": "pdpa_notice"}})
    # only the answer after the barge-in is played
    assert len(_media(sent)) == 1


async def test_without_a_stream_notice_recording_starts_at_stream_start() -> None:
    cc = _CallControl()
    b = _bridge(_Live([]), [], settings=_rec_settings(), call_control=cc)  # type: ignore[arg-type]
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1", "callSid": "CA1"}})
    await b.finalize()
    assert cc.started == ["CA1"]


async def test_notice_never_confirmed_means_no_recording() -> None:
    cc = _CallControl()
    sent: list[dict[str, Any]] = []
    b = _bridge(
        _Live([AUDIO]),
        sent,
        settings=_rec_settings(),
        call_control=cc,  # type: ignore[arg-type]
        announcement_mark="pdpa_notice",
        announcement_seconds=0.0,
    )
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1", "callSid": "CA1"}})
    await b.pump()
    await b.announcement_watchdog_expired()
    await b.finalize()
    assert cc.started == []
    assert len(_media(sent)) == 1  # the caller is not left in silence


# --- timing log -----------------------------------------------------------------


async def test_one_timing_log_per_call_without_numbers() -> None:
    clock = _Clock()
    live = _Live([lambda: setattr(clock, "t", clock.t + 0.8), AUDIO, AUDIO])
    b = _bridge(
        live,
        [],
        clock=clock,
        timing={"incoming_to_stream_ms": 900, "lookup_ms": 3, "live_connect_ms": 400},
        stream_started_at=clock.t,
    )
    with capture_logs() as logs:
        await b.pump()
        await b.finalize()
    timing = [e for e in logs if e["event"] == "phone_call_timing"]
    assert len(timing) == 1
    t = timing[0]
    assert t["first_model_audio_ms"] == 800
    assert t["incoming_to_stream_ms"] == 900 and t["lookup_ms"] == 3
    assert t["live_connect_ms"] == 400
    assert "phone" not in t and "caller" not in t


async def test_timing_logged_at_hangup_when_the_model_never_spoke() -> None:
    b = _bridge(_Live([]), [], timing={"lookup_ms": 1}, stream_started_at=100.0)
    b.call_sid = "CA1"
    with capture_logs() as logs:
        await b.finalize()
    timing = [e for e in logs if e["event"] == "phone_call_timing"]
    assert len(timing) == 1 and timing[0]["first_model_audio_ms"] is None


# --- fix round 1 (R40/R41) ------------------------------------------------------


async def test_caller_audio_during_the_notice_is_dropped() -> None:
    """R40: nothing the caller says over the PDPA notice reaches Gemini (so
    it is never transcribed or answered)."""
    live = _Live([])
    b = _bridge(live, [], announcement_mark="pdpa_notice", announcement_seconds=3.0)
    payload = base64.b64encode(b"\xff" * 160).decode()
    await b.handle_twilio({"event": "media", "media": {"payload": payload}})
    assert live.audio_sent == []
    await b.handle_twilio({"event": "mark", "mark": {"name": "pdpa_notice"}})
    await b.handle_twilio({"event": "media", "media": {"payload": payload}})
    assert len(live.audio_sent) == 1


async def test_greeting_waits_for_the_mark_and_is_sent_once() -> None:
    live = _Live([])
    b = _bridge(
        live,
        [],
        announcement_mark="pdpa_notice",
        announcement_seconds=3.0,
        greeting_prompt="Greet now.",
    )
    await b.greet_when_ready()
    assert live.greetings == []  # not over the notice
    await b.handle_twilio({"event": "mark", "mark": {"name": "pdpa_notice"}})
    assert live.greetings == ["Greet now."]
    await b.announcement_watchdog_expired()
    await b.greet_when_ready()
    assert live.greetings == ["Greet now."]


async def test_greeting_at_watchdog_expiry_when_the_mark_never_comes() -> None:
    live = _Live([])
    b = _bridge(
        live,
        [],
        announcement_mark="pdpa_notice",
        announcement_seconds=0.0,
        greeting_prompt="Greet now.",
    )
    await b.greet_when_ready()
    await b.announcement_watchdog_expired()
    assert live.greetings == ["Greet now."]


async def test_greeting_without_a_notice_is_immediate() -> None:
    live = _Live([])
    b = _bridge(live, [], greeting_prompt="Greet now.")
    await b.greet_when_ready()
    assert live.greetings == ["Greet now."]


def test_tamil_digits_and_numbers_are_speech() -> None:
    """R41: "(unclear)" must never hide real speech."""
    assert not is_unclear_transcript("வணக்கம், எனக்கு உதவி வேண்டும்")
    assert not is_unclear_transcript("0 1 2 3 4 5 6 7 8 9")
    assert not is_unclear_transcript("12 ribu ringgit")
    assert not is_unclear_transcript("012-345 6789")


async def test_unclear_is_decided_per_row_not_per_first_fragment() -> None:
    """A row that starts with a stray fragment but continues with real
    speech is speech; a row that is noise throughout is marked."""
    live = _Live(
        [
            InputTranscript("..."),
            InputTranscript(" saya nak tanya harga X50"),
            OutputTranscript("Baik."),
            InputTranscript("जय माता दी।"),
            OutputTranscript("Maaf?"),
        ]
    )
    b = _bridge(live, [])
    await b.pump()
    users = [t for r, t in b.transcript if r == "USER"]
    assert users == ["... saya nak tanya harga X50", "(unclear) जय माता दी।"]


async def test_unclear_row_still_open_at_hangup_is_marked() -> None:
    live = _Live([InputTranscript("जय माता दी।")])
    b = _bridge(live, [])
    b.call_sid = "CA1"
    await b.pump()
    await b.finalize()
    assert b.transcript[-1] == ("USER", "(unclear) जय माता दी।")


async def test_live_transcript_block_carries_the_row_decision() -> None:
    log = _FakeLog()
    live = _Live([InputTranscript("जय माता दी।"), OutputTranscript("Maaf?")])
    s = Settings(_env_file=None, phone_transcript_live_enabled=True)
    b = _bridge(live, [], settings=s, log=log)
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1", "callSid": "CA1"}})
    await b.pump()
    await b.finalize()
    posted = "\n".join(text for _t, text, _s in log.comments)
    assert "USER: (unclear) जय माता दी।" in posted


async def test_drop_window_follows_the_vad_silence_and_any_transcript_ends_it() -> None:
    clock = _Clock()
    s = Settings(_env_file=None, gemini_live_vad_silence_duration_ms=500)
    live = _Live(
        [
            Interrupted(),
            lambda: setattr(clock, "t", clock.t + 0.45),  # past 0.8 x 500 ms
            AUDIO,
            Interrupted(),
            InputTranscript("..."),  # even an unclear transcript ends it
            AUDIO,
        ]
    )
    sent: list[dict[str, Any]] = []
    await _bridge(live, sent, settings=s, clock=clock).pump()
    assert len(_media(sent)) == 2


async def test_handoff_during_the_notice_waits_for_it_to_finish() -> None:
    """R5: the caller hears the whole notice and the AI's handoff line
    before the call is redirected."""
    order: list[str] = []

    class _CC:
        async def start_recording(self, call_sid: str, callback: str) -> str:
            return "RE1"

        async def redirect(self, call_sid: str, twiml: str) -> bool:
            order.append("redirect")
            return True

    class _Resolver:
        async def prefetch(self) -> None: ...

        async def resolve(self) -> Any:
            return HandoffTarget(kind="pstn", value="+60000000000")

    s = Settings(
        _env_file=None,
        twilio_webhook_base_url="https://example.test",
        phone_transcript_live_enabled=True,
        phone_handoff_enabled=True,
    )
    b = _bridge(
        _Live([]),
        [],
        settings=s,
        call_control=_CC(),  # type: ignore[arg-type]
        handoff_resolver=_Resolver(),
        announcement_mark="pdpa_notice",
        announcement_seconds=3.0,
    )
    b.call_sid = "CA1"
    task = asyncio.create_task(b._attempt_transfer())
    await asyncio.sleep(0.05)
    assert order == []
    order.append("mark")
    await b.handle_twilio({"event": "mark", "mark": {"name": "pdpa_notice"}})
    assert await task == "transferring"
    assert order == ["mark", "redirect"]
