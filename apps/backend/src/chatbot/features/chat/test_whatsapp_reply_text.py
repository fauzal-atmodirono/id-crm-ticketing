from __future__ import annotations

from chatbot.features.chat.models import ProductCard, TurnResult
from chatbot.features.chat.router import _whatsapp_reply_text


def _result(reply: str, products: list[ProductCard]) -> TurnResult:
    return TurnResult(reply=reply, language="en", sentiment=None, handoff=None, products=products)


def test_plain_reply_passes_through() -> None:
    assert _whatsapp_reply_text(_result("Hello there", [])) == "Hello there"


def test_products_are_flattened_into_text() -> None:
    cards = [
        ProductCard(
            title="Proton X50",
            description="B-segment SUV with turbo engine.",
            price="RM 86,300",
            url="https://proton.example/x50",
        ),
        ProductCard(title="Proton S70", description="C-segment sedan.", price="RM 73,800"),
    ]
    text = _whatsapp_reply_text(_result("Here are some models you might like:", cards))

    assert "Here are some models you might like:" in text
    assert "1. Proton X50 — RM 86,300" in text
    assert "B-segment SUV with turbo engine." in text
    assert "https://proton.example/x50" in text
    assert "2. Proton S70 — RM 73,800" in text


def test_card_without_optional_fields_is_safe() -> None:
    cards = [ProductCard(title="Proton X90", description="")]
    text = _whatsapp_reply_text(_result("", cards))
    assert text == "1. Proton X90"
