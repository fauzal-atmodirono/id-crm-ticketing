from __future__ import annotations

from chatbot.features.chat.models import ProductCard, TurnResult


def test_turn_result_defaults_products_empty() -> None:
    tr = TurnResult(reply="hi")
    assert tr.products == []


def test_product_card_fields() -> None:
    card = ProductCard(
        title="X50",
        image_url="https://img/x50.jpg",
        description="Compact SUV",
        price="RM 89,800",
        url="https://www.proton.com/models/all-new-x50",
    )
    assert card.title == "X50"
    assert card.price == "RM 89,800"
