from __future__ import annotations

from chatbot.features.chat.models import ProductCard, TurnResult
from chatbot.features.chat.router import _products_list


def test_products_list_serializes_cards() -> None:
    tr = TurnResult(
        reply="Here are some models:",
        products=[
            ProductCard(
                title="X50", description="Compact SUV",
                image_url="https://img/x50.jpg", price="RM 89,800",
                url="https://www.proton.com/models/all-new-x50",
            )
        ],
    )
    out = _products_list(tr)
    assert out == [
        {
            "title": "X50",
            "description": "Compact SUV",
            "image_url": "https://img/x50.jpg",
            "price": "RM 89,800",
            "url": "https://www.proton.com/models/all-new-x50",
        }
    ]


def test_products_list_empty_by_default() -> None:
    assert _products_list(TurnResult(reply="hi")) == []
