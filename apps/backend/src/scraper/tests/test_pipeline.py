from __future__ import annotations

from pathlib import Path

from scraper.config import ScraperSettings
from scraper.pipeline import build_doc

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = ScraperSettings()


def test_build_doc_model_dispatch_assigns_id() -> None:
    html = (FIXTURES / "model_x50.html").read_text(encoding="utf-8")
    url = "https://www.proton.com/models/all-new-x50"
    # pdf_fetcher returns no bytes → no brochure text appended, no crash.
    doc = build_doc(url, html, SETTINGS, pdf_fetcher=lambda _u: None)
    # doc_id_for() converts dots to underscores; slug_for() keeps them.
    assert doc.doc_id == "www_proton_com_models_all-new-x50"
    assert doc.source_type == "model"
    assert doc.price == "RM 89,800"
    assert doc.gcs_uri.endswith("/www.proton.com_models_all-new-x50.html")


def test_build_doc_generic_dispatch() -> None:
    html = (FIXTURES / "generic_about.html").read_text(encoding="utf-8")
    url = "https://www.proton.com/about-us"
    doc = build_doc(url, html, SETTINGS, pdf_fetcher=lambda _u: None)
    assert doc.source_type == "generic"
