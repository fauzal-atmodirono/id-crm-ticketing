"""The two in-process caches behind the phone fast start (2026-11-09)."""

from __future__ import annotations

import asyncio

from chatbot.features.chat.phone.call_start_cache import AnnouncementAudio, CallerPrefetch
from chatbot.features.chat.phone.caller_context import CallerContext


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# --- AnnouncementAudio --------------------------------------------------------


async def test_announcement_is_synthesised_once_and_cached() -> None:
    calls: list[str] = []

    async def synth(text: str) -> bytes:
        calls.append(text)
        return b"\xff" * 800

    audio = AnnouncementAudio(synth)
    assert audio.get("This call is recorded.") is None  # cold: caller hears <Say>
    audio.warm("This call is recorded.")
    audio.warm("This call is recorded.")  # in flight: no second synthesis
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert audio.get("This call is recorded.") == b"\xff" * 800
    assert calls == ["This call is recorded."]
    # an edited notice is never served the old audio
    assert audio.get("This call may be recorded.") is None


async def test_announcement_synthesis_failure_falls_back_and_retries_later() -> None:
    attempts = 0

    async def synth(_text: str) -> bytes:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("tts down")

    audio = AnnouncementAudio(synth)
    audio.warm("n")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert audio.get("n") is None
    audio.warm("n")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert attempts == 2


async def test_no_synthesiser_means_never_any_stream_audio() -> None:
    audio = AnnouncementAudio(None)
    audio.warm("n")
    assert audio.get("n") is None


# --- CallerPrefetch -----------------------------------------------------------


async def test_prefetch_started_at_incoming_is_used_by_the_stream() -> None:
    clock = _Clock()
    lookups: list[str] = []

    async def lookup(phone: str, source: str) -> CallerContext:
        lookups.append(phone)
        return CallerContext(phone=phone, source=source)

    async def dealers() -> list[str]:
        return ["petaling_jaya"]

    cache = CallerPrefetch(clock=clock)
    cache.start("CA1", "+60000000101", "caller_id", lookup, dealers)
    clock.t += 2.0
    entry = cache.take("CA1")
    assert entry is not None
    assert entry.incoming_at == 1000.0
    ctx = await entry.caller("+60000000101", "caller_id")
    assert ctx is not None and ctx.phone == "+60000000101"
    assert await entry.dealer_slugs() == ["petaling_jaya"]
    assert lookups == ["+60000000101"]
    assert cache.take("CA1") is None  # one use


async def test_prefetch_for_a_different_number_is_not_used() -> None:
    async def lookup(phone: str, source: str) -> CallerContext:
        return CallerContext(phone=phone, source=source)

    cache = CallerPrefetch(clock=_Clock())
    cache.start("CA1", "+60000000101", "caller_id", lookup, None)
    entry = cache.take("CA1")
    assert entry is not None
    assert await entry.caller("+60000000999", "caller_id") is None
    assert await entry.dealer_slugs() is None


async def test_prefetch_expires_and_is_bounded() -> None:
    clock = _Clock()

    async def lookup(phone: str, source: str) -> CallerContext:
        return CallerContext(phone=phone, source=source)

    cache = CallerPrefetch(clock=clock, ttl_seconds=60.0, max_entries=3)
    cache.start("CA1", "+60000000101", "caller_id", lookup, None)
    clock.t += 61
    assert cache.take("CA1") is None
    for i in range(5):
        cache.start(f"CB{i}", None, None, None, None)
    assert len(cache) == 3
    assert cache.take("CB0") is None and cache.take("CB4") is not None


async def test_incoming_time_is_recorded_without_a_caller() -> None:
    cache = CallerPrefetch(clock=_Clock())
    cache.start("CA2", None, None, None, None)
    entry = cache.take("CA2")
    assert entry is not None and entry.incoming_at == 1000.0
    assert await entry.caller("+60000000101", "caller_id") is None


def test_warm_without_a_running_loop_stays_cold() -> None:
    async def synth(_text: str) -> bytes:
        return b"x"

    audio = AnnouncementAudio(synth)
    audio.warm("n")  # no event loop here: must not raise
    assert audio.get("n") is None
