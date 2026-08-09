from __future__ import annotations

from typing import Any

_NEGATIVE_SENTIMENTS = {"negative", "very_negative", "angry", "frustrated", "urgent"}


def should_open_ticket(state: dict[str, Any]) -> bool:
    """Decide whether a conversation warrants an OPEN, actionable Zendesk ticket.

    Hybrid gate: the AI's `flag_for_ticket_tool`, a handoff, or strongly negative
    sentiment all open a ticket. Everything else is a routine conversation that is
    still logged to Zendesk, but as a solved record.

    "urgent" (P7 task 1's fourth sentiment level, safety-critical / needs
    immediate attention) joins this set alongside "negative" -- the gate gets
    stricter, never different: every case that tripped it before still does.
    """
    if state.get("ticket_flagged") is True:
        return True
    if state.get("handoff_triggered") is True:
        return True
    sentiment = str(state.get("sentiment") or "").lower()
    return sentiment in _NEGATIVE_SENTIMENTS
