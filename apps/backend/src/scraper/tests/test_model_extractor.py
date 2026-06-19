from __future__ import annotations

from pathlib import Path

from scraper.extractors.model import extract_model

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.proton.com/models/all-new-x50"


def test_extract_model_fields() -> None:
    html = (FIXTURES / "model_x50.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)

    assert doc.source_type == "model"
    assert doc.title == "PROTON - PROTON All-New X50"
    assert doc.price == "RM 89,800"
    assert "compact SUV" in doc.body
    # Relative image URLs resolved to absolute; .svg icons excluded.
    assert "https://www.proton.com/images/x50-front.jpg" in doc.image_urls
    assert "https://cdn.proton.com/x50-interior.jpg" in doc.image_urls
    assert all(not u.endswith(".svg") for u in doc.image_urls)
    # Brochure link resolved absolute.
    assert doc.brochure_url == "https://www.proton.com/downloads/x50-brochure.pdf"
