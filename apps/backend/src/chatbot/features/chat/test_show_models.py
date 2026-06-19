from __future__ import annotations

from chatbot.features.chat.service import _cards_from_state


def test_cards_from_state_maps_dicts() -> None:
    raw = [
        {
            "title": "X50",
            "description": "Compact SUV",
            "image_url": "https://img/x50.jpg",
            "price": "RM 89,800",
            "url": "https://www.proton.com/models/all-new-x50",
        }
    ]
    cards = _cards_from_state(raw)
    assert len(cards) == 1
    assert cards[0].title == "X50"
    assert cards[0].image_url == "https://img/x50.jpg"
