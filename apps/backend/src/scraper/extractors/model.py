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


def _model_tokens(url: str) -> tuple[str, str]:
    """Return ``(slug, short_token)`` derived from the page URL.

    Examples::

        "https://www.proton.com/models/all-new-x50"
            → ("all-new-x50", "x50")
        "https://www.proton.com/models/x70"
            → ("x70", "x70")
    """
    slug = url.rstrip("/").split("/")[-1].lower()
    short_token = slug.split("-")[-1]
    return slug, short_token


def _image_urls(node: Tag, base_url: str) -> list[str]:
    """Return absolute image URLs from *node*, excluding SVGs, logos, and icons.

    Model-token-matching images are returned first (in document order), making
    ``image_urls[0]`` the model's own hero image rather than a nav leftover.
    """
    slug, short_token = _model_tokens(base_url)

    all_urls: list[str] = []
    for img in node.find_all("img"):
        src = str(img.get("src") or "")
        if not src or src.endswith(".svg"):
            continue
        absolute = urljoin(base_url, src)
        lo = absolute.lower()
        if "logo" in lo or "/icon" in lo:
            continue
        if absolute not in all_urls:
            all_urls.append(absolute)

    # Prioritize images whose URL contains the model token.
    matching = [u for u in all_urls if short_token in u.lower() or slug in u.lower()]
    other = [u for u in all_urls if u not in matching]
    return matching + other


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

    # Strip chrome BEFORE extracting price so the header nav's model-switcher
    # (which lists ALL models' prices) cannot pollute the match.  Images must be
    # collected first because the model thumbnail lives inside the c-header.
    strip_chrome(main)
    body = clean_text(main.get_text(separator=" "))
    price = _find_price(body)

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
