from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from scraper.html_utils import (
    clean_text,
    headings,
    main_container,
    page_language,
    page_title,
    strip_chrome,
)
from scraper.models import ScrapedDoc

_PRICE_RE = re.compile(r"RM\s?[\d,]+(?:\.\d{2})?")


def _find_price(text: str) -> str | None:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    # Normalize "RM89,800" / "RM 89,800" → "RM 89,800".
    raw = m.group(0).replace("RM", "RM ").replace("  ", " ")
    return clean_text(raw)


def _image_urls(node: Tag, base_url: str) -> list[str]:
    out: list[str] = []
    for img in node.find_all("img"):
        src = str(img.get("src") or "")
        if not src or src.endswith(".svg"):
            continue
        absolute = urljoin(base_url, src)
        if absolute not in out:
            out.append(absolute)
    return out


def _brochure_url(node: Tag, base_url: str) -> str | None:
    for a in node.find_all("a", href=True):
        href = str(a["href"])
        if href.lower().endswith(".pdf"):
            return urljoin(base_url, href)
    return None


def extract_model(html: str, url: str) -> ScrapedDoc:
    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    lang = page_language(soup)
    main = main_container(soup)

    sections = headings(main)
    images = _image_urls(main, url)
    brochure = _brochure_url(main, url)

    # Derive price from raw text before chrome removal can drop it.
    price = _find_price(main.get_text(separator=" "))

    strip_chrome(main)
    body = clean_text(main.get_text(separator=" "))

    return ScrapedDoc(
        doc_id="",
        title=title,
        link=url,
        source_type="model",
        language=lang,
        body=body,
        gcs_uri="",
        price=price,
        image_urls=images,
        brochure_url=brochure,
        sections=sections,
    )
