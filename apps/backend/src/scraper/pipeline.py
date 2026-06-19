from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import structlog

from scraper.classifier import classify
from scraper.config import ScraperSettings
from scraper.extractors.generic import extract_generic
from scraper.extractors.model import extract_model
from scraper.indexer import doc_id_for, slug_for
from scraper.models import ScrapedDoc
from scraper.pdf import extract_pdf_text

_log = structlog.get_logger(__name__)

PdfFetcher = Callable[[str], bytes | None]


def _gcs_uri(settings: ScraperSettings, slug: str) -> str:
    return f"gs://{settings.bucket_name}/{settings.gcs_prefix}/{slug}.html"


def build_doc(
    url: str,
    html: str,
    settings: ScraperSettings,
    pdf_fetcher: PdfFetcher,
) -> ScrapedDoc:
    page_type = classify(url)
    base = extract_model(html, url) if page_type == "model" else extract_generic(html, url)

    slug = slug_for(url)
    body = base.body

    # On Standard tier Vertex can't read PDF content; fold brochure text into body.
    if base.brochure_url:
        data = pdf_fetcher(base.brochure_url)
        if data:
            text = extract_pdf_text(data)
            if text:
                body = f"{body}\n\n[Brochure] {text}"
                _log.debug("brochure_folded", url=base.brochure_url, chars=len(text))

    return replace(base, doc_id=doc_id_for(slug), gcs_uri=_gcs_uri(settings, slug), body=body)
