"""In-process caches that take work off the phone call's start (2026-11-09).

Live evidence (9 Nov): ~13-14 s from POST /voice/phone/incoming to the
Gemini Live session being connected. Two things on that path did not need to
be there:

- ``CallerPrefetch``: the caller lookup and dealer list were awaited inside
  /voice/phone/stream, BEFORE Gemini Live opened, although the verified
  caller and the CallSid are already known at /voice/phone/incoming -- which
  is seconds earlier (Twilio still has to play the TwiML and open the
  stream). They now start there, as tasks keyed by CallSid; the stream picks
  the result up and only falls back to its own lookup when there is none.
- ``AnnouncementAudio``: the PDPA recording notice as a TwiML ``<Say>`` runs
  before Twilio even opens the stream. With phone_fast_start_enabled it is
  synthesised ONCE per process (mu-law 8 kHz), cached, and played over the
  stream while Gemini Live connects. Cold cache = today's ``<Say>``.

Both are per-process, bounded and fail-open: a miss only costs the old
latency, never a call. One backend container per tenant serves both
webhooks, so the stream normally lands on the process that saw /incoming.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.features.chat.phone.caller_context import CallerContext

_log = structlog.get_logger(__name__)

Synth = Callable[[str], Awaitable[bytes]]
Lookup = Callable[[str, str], Awaitable["CallerContext"]]
Dealers = Callable[[], Awaitable[list[str]]]

# Twilio opens the stream a few seconds after /incoming; a prefetch nobody
# picked up in this long belongs to a call that never streamed.
_PREFETCH_TTL_SECONDS = 120.0
_PREFETCH_MAX_ENTRIES = 256


class AnnouncementAudio:
    """The recording notice as telephony audio, synthesised once per text."""

    def __init__(self, synth: Synth | None) -> None:
        self._synth = synth
        self._text: str | None = None
        self._audio: bytes | None = None
        self._task: asyncio.Task[None] | None = None

    def get(self, text: str) -> bytes | None:
        """The cached audio for exactly this notice text, else None."""
        return self._audio if self._audio is not None and self._text == text else None

    def warm(self, text: str) -> None:
        """Start synthesising ``text`` in the background unless it is cached
        or already in flight. Never raises."""
        if self._synth is None or not text or self.get(text) is not None:
            return
        if self._task is not None and not self._task.done():
            return
        coro = self._run(text)
        try:
            self._task = asyncio.create_task(coro)
        except RuntimeError:  # no running loop (sync caller): stay cold
            coro.close()

    async def _run(self, text: str) -> None:
        if self._synth is None:
            return
        try:
            audio = await self._synth(text)
        except Exception as e:
            _log.warning("phone_announcement_tts_failed", error_type=type(e).__name__)
            return
        if not audio:
            return
        self._text, self._audio = text, audio
        _log.info("phone_announcement_audio_cached", seconds=round(len(audio) / 8000, 1))


@dataclass
class PrefetchEntry:
    incoming_at: float
    phone: str | None
    source: str | None
    lookup_task: asyncio.Task[CallerContext] | None
    dealers_task: asyncio.Task[list[str]] | None

    async def caller(self, phone: str, source: str) -> CallerContext | None:
        """The prefetched lookup, only for the SAME verified number/source;
        None = not prefetched (the stream looks up itself)."""
        if self.lookup_task is None or phone != self.phone or source != self.source:
            return None
        try:
            return await self.lookup_task
        except Exception as e:
            _log.warning("phone_caller_prefetch_failed", error_type=type(e).__name__)
            return None

    async def dealer_slugs(self) -> list[str] | None:
        if self.dealers_task is None:
            return None
        try:
            return await self.dealers_task
        except Exception:
            return None

    def cancel(self) -> None:
        for t in (self.lookup_task, self.dealers_task):
            if t is not None and not t.done():
                t.cancel()


class CallerPrefetch:
    """CallSid -> the caller lookup started at /voice/phone/incoming."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = _PREFETCH_TTL_SECONDS,
        max_entries: int = _PREFETCH_MAX_ENTRIES,
    ) -> None:
        self._clock = clock
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: OrderedDict[str, PrefetchEntry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def _prune(self) -> None:
        now = self._clock()
        for sid in [s for s, e in self._entries.items() if now - e.incoming_at > self._ttl]:
            self._entries.pop(sid).cancel()
        while len(self._entries) >= self._max:
            _sid, oldest = self._entries.popitem(last=False)
            oldest.cancel()

    def start(
        self,
        call_sid: str,
        phone: str | None,
        source: str | None,
        lookup: Lookup | None,
        dealers: Dealers | None,
    ) -> None:
        """Record the call's arrival and start its lookups. ``phone`` None =
        no verified caller (only the arrival time and the dealer list)."""
        if not call_sid:
            return
        self._prune()
        old = self._entries.pop(call_sid, None)
        if old is not None:
            old.cancel()
        lookup_task = (
            asyncio.create_task(lookup(phone, source))
            if lookup is not None and phone and source
            else None
        )
        dealers_task = asyncio.create_task(dealers()) if dealers is not None else None
        self._entries[call_sid] = PrefetchEntry(
            self._clock(), phone, source, lookup_task, dealers_task
        )

    def take(self, call_sid: str | None) -> PrefetchEntry | None:
        """The call's entry, removed (one use); None when missing/expired."""
        if not call_sid:
            return None
        entry = self._entries.pop(call_sid, None)
        if entry is None:
            return None
        if self._clock() - entry.incoming_at > self._ttl:
            entry.cancel()
            return None
        return entry

    def discard_all(self) -> None:
        for entry in self._entries.values():
            entry.cancel()
        self._entries.clear()
