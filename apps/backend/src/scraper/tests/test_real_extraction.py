"""Tests against the real saved Proton X50 page (real_x50.html).

The fixture is a cleaned copy of the live page saved via ``clean_html_for_storage``.
It exercises the div-based chrome patterns (``c-header``, ``c-footer``,
``display:none`` fly-outs) that the synthetic fixtures do not cover.
"""

from __future__ import annotations

from pathlib import Path

from scraper.extractors.model import extract_model

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.proton.com/models/all-new-x50"


def test_real_x50_price() -> None:
    html = (FIXTURES / "real_x50.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)
    assert doc.price == "RM 89,800"


def test_real_x50_source_type() -> None:
    html = (FIXTURES / "real_x50.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)
    assert doc.source_type == "model"


def test_real_x50_chrome_stripped() -> None:
    """Footer/nav giveaway strings must not appear in the extracted body."""
    html = (FIXTURES / "real_x50.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)
    # "Proton Global Network" appears in the nav fly-out; gone after strip.
    assert "Proton Global Network" not in doc.body
    # The body must not open with nav chrome text.
    assert not doc.body.startswith("Models Back MODELS")


def test_real_x50_images() -> None:
    html = (FIXTURES / "real_x50.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)
    assert doc.image_urls, "image_urls must be non-empty"
    first = doc.image_urls[0].lower()
    assert "logo" not in first, f"image_urls[0] contains 'logo': {doc.image_urls[0]}"
    assert "x50" in first, f"image_urls[0] does not contain 'x50': {doc.image_urls[0]}"
