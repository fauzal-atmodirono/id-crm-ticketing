from __future__ import annotations

from scraper.models import ScrapedDoc


def test_to_document_packs_struct_data_and_content_uri() -> None:
    doc = ScrapedDoc(
        doc_id="models_all-new-x50",
        title="PROTON All-New X50",
        link="https://www.proton.com/models/all-new-x50",
        source_type="model",
        language="en",
        body="Full cleaned description text.",
        price="RM 89,800",
        image_urls=["https://img/x50.jpg"],
        brochure_url="https://img/x50.pdf",
        sections=["Stylish Design"],
        gcs_uri="gs://bucket/scraped_data/models_all-new-x50.html",
    )
    out = doc.to_document()
    assert out["id"] == "models_all-new-x50"
    sd = out["struct_data"]
    assert sd["title"] == "PROTON All-New X50"
    assert sd["source_type"] == "model"
    assert sd["price"] == "RM 89,800"
    assert sd["body_excerpt"] == "Full cleaned description text."
    assert sd["image_urls"] == ["https://img/x50.jpg"]
    assert sd["brochure_url"] == "https://img/x50.pdf"
    assert out["content"]["uri"] == "gs://bucket/scraped_data/models_all-new-x50.html"


def test_to_document_truncates_body_utf8_safe() -> None:
    doc = ScrapedDoc(
        doc_id="d",
        title="t",
        link="u",
        source_type="generic",
        language="en",
        body="é" * 100_000,  # multi-byte, exceeds cap
        gcs_uri="gs://b/x.html",
    )
    out = doc.to_document()
    body = out["struct_data"]["body_excerpt"]
    assert isinstance(body, str)
    # Encodes cleanly (no partial multi-byte char) and is bounded.
    assert len(body.encode("utf-8")) <= ScrapedDoc.STRUCT_BODY_CAP_BYTES
