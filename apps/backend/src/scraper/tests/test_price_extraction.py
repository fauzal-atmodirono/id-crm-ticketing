"""Regression test: model price must come from page body, not the header nav.

Proton's ``<div class="c-header">`` contains a model-switcher that lists ALL
models and their prices.  Before the fix, ``extract_model`` searched the raw
pre-strip text, so the first price in the header (always the X50 at RM89,800)
won regardless of which model page was being processed.

This test uses a synthetic fixture that mimics the real pattern:
- ``c-header`` nav: "Starting at RM89,800  Starting at RM47,800"
- Page body:        "RM 47,800 Starting Price"

After the fix the Persona page must yield RM 47,800, not RM 89,800.
"""
from __future__ import annotations

from pathlib import Path

from scraper.extractors.model import extract_model

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.proton.com/models/persona"


def test_persona_price_not_polluted_by_header_nav() -> None:
    """Header nav's RM89,800 must NOT win over the page's own RM 47,800."""
    html = (FIXTURES / "model_persona_header_price.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)
    assert doc.price == "RM 47,800", (
        f"Expected 'RM 47,800' (page price) but got {doc.price!r}. "
        "The header nav's first price (RM89,800) must not bleed into extraction."
    )


def test_persona_source_type() -> None:
    html = (FIXTURES / "model_persona_header_price.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)
    assert doc.source_type == "model"


def test_persona_body_excludes_header_chrome() -> None:
    """After strip_chrome, nav prices and chrome text must not appear in body."""
    html = (FIXTURES / "model_persona_header_price.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)
    assert "RM89,800" not in doc.body, "Header's RM89,800 must be stripped from body"
    assert "Starting at RM" not in doc.body, "Model-switcher text must be stripped"
