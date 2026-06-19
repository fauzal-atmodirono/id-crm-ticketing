from __future__ import annotations

from pathlib import Path

from scraper.extractors.generic import extract_generic

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_generic_strips_chrome_and_keeps_main() -> None:
    html = (FIXTURES / "generic_about.html").read_text(encoding="utf-8")
    doc = extract_generic(html, "https://www.proton.com/about-us")

    assert doc.title == "PROTON - About Us"
    assert doc.source_type == "generic"
    assert doc.link == "https://www.proton.com/about-us"
    # Main content survives.
    assert "Malaysian automotive company" in doc.body
    assert "To build cars Malaysians are proud of." in doc.body
    # Nav/footer/script chrome is gone.
    assert "TEST DRIVE" not in doc.body
    assert "Footer links" not in doc.body
    assert "var x" not in doc.body
    assert doc.sections == ["About PROTON", "Our Mission"]
