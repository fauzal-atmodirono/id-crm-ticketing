from __future__ import annotations

from chatbot.features.chat.product_cleanup import clean_description, clean_title, dedupe_cards


def test_clean_title_dedoubles_brand() -> None:
    assert clean_title("PROTON - PROTON All-New Saga") == "PROTON All-New Saga"


def test_clean_title_leaves_normal_titles() -> None:
    assert clean_title("Proton X50") == "Proton X50"
    assert clean_title("PROTON - X50 SUV") == "PROTON - X50 SUV"  # not a doubled brand


def test_clean_title_collapses_whitespace() -> None:
    assert clean_title("  Proton   All-New   Saga  ") == "Proton All-New Saga"


def test_clean_description_collapses_and_trims() -> None:
    raw = "5-YEAR Warranty   or   150,000km" + " word" * 80
    out = clean_description(raw, max_len=50)
    assert "  " not in out
    assert len(out) <= 51  # 50 + ellipsis, trimmed at a word boundary
    assert out.endswith("…")


def test_clean_description_short_text_unchanged() -> None:
    assert clean_description("A clean short summary.") == "A clean short summary."


def test_dedupe_cards_by_cleaned_title() -> None:
    cards = [
        {"title": "PROTON - PROTON All-New Saga", "url": "https://x/all-new-saga"},
        {"title": "PROTON All-New Saga", "url": "https://x/models/all-new-saga"},
        {"title": "Proton X50", "url": "https://x/x50"},
    ]
    out = dedupe_cards(cards)
    assert [c["title"] for c in out] == ["PROTON - PROTON All-New Saga", "Proton X50"]


def test_dedupe_drops_empty_titles() -> None:
    assert dedupe_cards([{"title": ""}, {"title": "  "}]) == []
