from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from chatbot.features.chat.phone import gemini_live


class _FakeAsyncSession:
    """Fake google-genai live session. Each receive() call yields one turn's
    worth of messages then ends (mirroring the real SDK, where receive() returns
    after a turn completes). When all turns are consumed it raises, simulating
    the live connection closing."""

    def __init__(self, turns: list[list[str]]) -> None:
        self._turns = turns
        self.receive_calls = 0

    def receive(self) -> AsyncIterator[str]:
        if self.receive_calls >= len(self._turns):
            raise ConnectionError("live session closed")
        turn = self._turns[self.receive_calls]
        self.receive_calls += 1

        async def _gen() -> AsyncIterator[str]:
            for m in turn:
                yield m

        return _gen()


@pytest.mark.asyncio
async def test_events_spans_multiple_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """events() must keep re-entering receive() so a multi-turn call works — not
    stop after the first turn (which would drop the call after one AI reply)."""
    monkeypatch.setattr(gemini_live, "normalize_server_message", lambda m: [m])
    session = _FakeAsyncSession([["t1a", "t1b"], ["t2a"]])
    live = gemini_live._GeminiLiveSession(session)  # type: ignore[arg-type]

    got: list[Any] = []
    with pytest.raises(ConnectionError):
        async for ev in live.events():
            got.append(ev)

    assert got == ["t1a", "t1b", "t2a"]  # events from BOTH turns, not just turn 1
    assert session.receive_calls == 2  # re-entered receive() for turn 2 (the loop)
