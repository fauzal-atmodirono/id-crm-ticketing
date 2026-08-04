"""Batches streamed transcript deltas into postable blocks.

Gemini Live emits transcription as many small fragments. Posting one Chatwoot
message per fragment would be unreadable and would hit rate limits, so
fragments are concatenated per speaker and released either when the speaker
changes (the turn is complete) or when the flush interval elapses (so a long
monologue still appears during the call).

Deliberately pure — no I/O, no network, no Chatwoot client, no clock of its
own (``now`` is injected). That isolation is what lets this batching rule be
unit tested without a live call, and it keeps the class safe to call from
inside the audio loop's task: it cannot raise for "nothing to do" (an empty
buffer, an unmet threshold), so a caller can poll it on every loop tick
without special-casing failure.

``take_if_due`` consumes what it returns: anything released is dropped from
the buffer immediately, so calling it repeatedly on a timer (Task 3's plan)
never re-posts the same block twice. The one exception the timer/turn checks
don't cover is the very last in-progress turn — that only leaves the buffer
via a caller-forced flush (``force=True``) at call end, since releasing it
early would risk splitting one sentence across two Chatwoot messages.
"""

from __future__ import annotations

from collections.abc import Callable


class TranscriptSink:
    def __init__(self, flush_seconds: float, now: Callable[[], float]) -> None:
        self._flush_seconds = flush_seconds
        self._now = now
        self._pending: list[tuple[str, str]] = []
        self._last_flush = now()

    def add(self, role: str, text: str) -> None:
        """Append a transcript fragment, merging into the current turn when
        the speaker hasn't changed."""
        if self._pending and self._pending[-1][0] == role:
            prev_role, prev_text = self._pending[-1]
            self._pending[-1] = (prev_role, prev_text + text)
        else:
            self._pending.append((role, text))

    def take_if_due(self, *, force: bool = False) -> str | None:
        """Return the formatted block to post, or ``None`` when nothing is
        due. Releasing empties exactly what it returns from the buffer, so
        this is safe to call on every loop tick without double-posting."""
        if not self._pending:
            return None
        turn_completed = len(self._pending) > 1
        timer_elapsed = (self._now() - self._last_flush) >= self._flush_seconds
        if not (force or turn_completed or timer_elapsed):
            return None
        # Keep the in-progress (last) turn pending unless forced or the timer
        # fired: releasing it mid-turn would split one sentence across two
        # Chatwoot messages.
        release = self._pending if (force or timer_elapsed) else self._pending[:-1]
        self._pending = [] if (force or timer_elapsed) else self._pending[-1:]
        self._last_flush = self._now()
        if not release:
            return None
        return "\n".join(f"{role}: {text}" for role, text in release)
