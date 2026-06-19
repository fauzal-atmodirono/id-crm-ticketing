from __future__ import annotations

from bs4 import BeautifulSoup

from scraper.classifier import classify
from scraper.html_utils import (
    clean_text,
    headings,
    main_container,
    page_language,
    page_title,
    strip_chrome,
)
from scraper.models import ScrapedDoc


def extract_generic(html: str, url: str) -> ScrapedDoc:
    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    lang = page_language(soup)
    main = main_container(soup)
    # Capture headings BEFORE stripping (headings live inside main, not chrome).
    sections = headings(main)
    strip_chrome(main)
    body = clean_text(main.get_text(separator=" "))
    return ScrapedDoc(
        doc_id="",  # assigned by the pipeline (slug)
        title=title,
        link=url,
        source_type=classify(url),
        language=lang,
        body=body,
        gcs_uri="",
        sections=sections,
    )
