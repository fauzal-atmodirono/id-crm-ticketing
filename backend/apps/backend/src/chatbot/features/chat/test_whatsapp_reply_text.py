from __future__ import annotations

from chatbot.features.chat.models import ProductCard, TurnResult
from chatbot.features.chat.router import _whatsapp_reply_text


def _result(reply: str, products: list[ProductCard]) -> TurnResult:
    return TurnResult(reply=reply, language="en", sentiment=None, handoff=None, products=products)


def test_plain_reply_passes_through() -> None:
    assert _whatsapp_reply_text(_result("Hello there", [])) == "Hello there"


def test_products_are_flattened_with_whatsapp_bold_titles() -> None:
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
    # Title is bolded with WhatsApp's single-asterisk syntax.
    assert "*1. Proton X50* — RM 86,300" in text
    assert "B-segment SUV with turbo engine." in text
    assert "https://proton.example/x50" in text
    assert "*2. Proton S70* — RM 73,800" in text


def test_stray_markdown_in_description_is_sanitized() -> None:
    # Scraped copy with footnote asterisks and underscores would otherwise toggle
    # WhatsApp bold/italic and garble the text.
    card = ProductCard(
        title="Proton All-New Saga",
        description="150,000km* 3 TIMES Free Labour Service* RM 38,990 Starting Price* *Terms apply",
        price="RM 38,990",
    )
    text = _whatsapp_reply_text(_result("Here are some models you might like:", [card]))

    # No stray formatting characters survive in the description body.
    body = text.split("\n", 2)[-1]  # drop the lead-in + bold header lines
    assert "*Terms" not in body
    assert "Service*" not in body
    assert "Free Labour Service RM 38,990" in text  # whitespace collapsed, asterisks gone


def test_markdown_link_becomes_bare_clickable_url() -> None:
    reply = "Maklumat lanjut di [laman web rasmi Proton](https://www.proton.com/models/saga)."
    text = _whatsapp_reply_text(_result(reply, []))
    assert "[laman web rasmi Proton]" not in text
    assert "laman web rasmi Proton (https://www.proton.com/models/saga)" in text


def test_markdown_headings_become_bold_and_bullets_preserved() -> None:
    # Reply markdown is CONVERTED to WhatsApp formatting (not flattened): headings
    # become *bold*, list items become • bullets, and line breaks are kept so the
    # message stays scannable.
    text = _whatsapp_reply_text(_result("## Models\n- Proton Saga\n- Proton X50", []))
    assert text == "*Models*\n• Proton Saga\n• Proton X50"


def test_markdown_bold_becomes_whatsapp_bold() -> None:
    # **double-asterisk** (and __underscore__) markdown bold -> WhatsApp *single*.
    assert _whatsapp_reply_text(_result("The **Saga** is great", [])) == "The *Saga* is great"
    assert _whatsapp_reply_text(_result("Price __RM 38,990__", [])) == "Price *RM 38,990*"


def test_reply_line_breaks_are_preserved() -> None:
    reply = "Here is a summary:\n\n- *Engine*: 1.5T\n- *Price*: RM 99,800"
    text = _whatsapp_reply_text(_result(reply, []))
    assert text == "Here is a summary:\n\n• *Engine*: 1.5T\n• *Price*: RM 99,800"


def test_card_without_optional_fields_is_safe() -> None:
    card = ProductCard(title="Proton X90", description="")
    assert _whatsapp_reply_text(_result("", [card])) == "*1. Proton X90*"
