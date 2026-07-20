"""Pure per-turn metrics event + builder for the Phase-2 instrumentation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chatbot.features.metrics.mapping import channel_from_external_id


@dataclass(frozen=True)
class TurnEvent:
    occurred_at: datetime
    channel: str
    session_id: str
    latency_ms: int
    is_first_turn: bool
    is_fallback: bool
    handed_off: bool


def build_turn_event(
    session_id: str,
    occurred_at: datetime,
    latency_ms: int,
    turn_count: int,
    is_fallback: bool,
    handed_off: bool,
) -> TurnEvent:
    """Build an immutable turn event. Pure: no clock, no uuid, no I/O."""
    return TurnEvent(
        occurred_at=occurred_at,
        channel=channel_from_external_id(session_id),
        session_id=session_id,
        latency_ms=latency_ms,
        is_first_turn=turn_count == 1,
        is_fallback=is_fallback,
        handed_off=handed_off,
    )
