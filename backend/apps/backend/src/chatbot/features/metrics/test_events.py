from datetime import UTC, datetime

import pytest

from chatbot.features.metrics.events import TurnEvent, build_turn_event

_T = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "session_id,expected_channel",
    [
        ("whatsapp-+60123", "WhatsApp"),
        ("email-55", "Email"),
        ("phone-CA1", "Phone"),
        ("sim-abc", "Web"),
        ("zendesk-conv-9", "Web"),
        ("chatwoot-conv-9", "Web"),
        ("weird-1", "Other"),
    ],
)
def test_channel_derived_from_session_id(session_id: str, expected_channel: str) -> None:
    ev = build_turn_event(session_id, _T, 1200, 1, False, False)
    assert ev.channel == expected_channel


@pytest.mark.parametrize(
    "turn_count,expected_first",
    [(1, True), (2, False), (7, False)],
)
def test_is_first_turn_from_turn_count(turn_count: int, expected_first: bool) -> None:
    ev = build_turn_event("sim-1", _T, 100, turn_count, False, False)
    assert ev.is_first_turn is expected_first


def test_fields_pass_through() -> None:
    ev = build_turn_event("sim-1", _T, 1234, 1, True, True)
    assert ev == TurnEvent(
        occurred_at=_T,
        channel="Web",
        session_id="sim-1",
        latency_ms=1234,
        is_first_turn=True,
        is_fallback=True,
        handed_off=True,
    )
