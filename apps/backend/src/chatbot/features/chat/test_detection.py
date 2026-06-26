from __future__ import annotations

from chatbot.features.chat.detection import should_open_ticket


def test_routine_conversation_is_not_opened() -> None:
    assert should_open_ticket({"sentiment": "positive"}) is False
    assert should_open_ticket({}) is False


def test_ticket_flag_opens() -> None:
    assert should_open_ticket({"ticket_flagged": True}) is True


def test_handoff_opens() -> None:
    assert should_open_ticket({"handoff_triggered": True}) is True


def test_negative_sentiment_opens() -> None:
    assert should_open_ticket({"sentiment": "negative"}) is True
    assert should_open_ticket({"sentiment": "VERY_NEGATIVE"}) is True
