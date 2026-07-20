from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

_CHROME_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "iframe", "svg"]
_STORAGE_STRIP_TAGS = ["script", "style", "noscript", "iframe", "svg"]

# Matches class tokens used for site chrome on Proton pages, which use div-based
# chrome (e.g. <div class="c-header"> / <div class="c-footer">) rather than
# semantic <nav>/<header>/<footer> tags.
_CHROME_CLASS_RE = re.compile(
    r"^(c-)?(header|footer|nav|navbar|menu|cookie|breadcrumb)$",
    re.IGNORECASE,
)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def page_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return "PROTON Page"


def page_language(soup: BeautifulSoup) -> str:
    lang = (soup.html.get("lang") if soup.html else None) or "en"
    lang = str(lang).split("-")[0].lower()
    return lang if lang in {"en", "ms", "zh"} else "en"


def main_container(soup: BeautifulSoup) -> Tag:
    """Pick the richest content container, falling back to <body>."""
    for selector in ("main", "article", "div.content", "div#content"):
        node = soup.select_one(selector)
        if node is not None:
            return node
    return soup.body or soup


def strip_chrome(node: Tag) -> None:
    """Remove chrome (nav, header, footer, hidden widgets) from *node* in-place.

    Three passes are applied:

    1. **Tag-based**: decompose ``<script>``, ``<style>``, ``<noscript>``,
       ``<nav>``, ``<header>``, ``<footer>``, ``<iframe>``, ``<svg>``.
    2. **Class-based**: decompose any element whose class list contains a
       token matching ``_CHROME_CLASS_RE`` (covers Proton's div-based chrome,
       e.g. ``c-header``, ``c-footer``).  Only the listed token prefixes are
       checked; generic content classes are left untouched.
    3. **Hidden widgets**: decompose elements with ``display:none`` in their
       inline ``style`` attribute (hidden nav fly-outs and brochure drawers).
    """
    # Pass 1: semantic tag removal (original behaviour).
    for el in node(_CHROME_TAGS):
        el.decompose()

    # Pass 2: Proton div-based chrome (c-header, c-footer, etc.).
    # Collect first to avoid mutating the tree while iterating.
    chrome_els = [
        el
        for el in node.find_all(True)
        if isinstance(el, Tag) and any(_CHROME_CLASS_RE.match(c) for c in (el.get("class") or []))
    ]
    for el in chrome_els:
        el.decompose()

    # Pass 3: hidden nav fly-outs / brochure widgets (display:none).
    hidden_els = [
        el
        for el in node.find_all(True)
        if isinstance(el, Tag)
        and "display:none" in str(el.get("style") or "").replace(" ", "").lower()
    ]
    for el in hidden_els:
        el.decompose()


def headings(node: Tag, limit: int = 20) -> list[str]:
    out: list[str] = []
    for h in node.find_all(["h1", "h2", "h3"]):
        t = clean_text(h.get_text())
        if t and t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def clean_html_for_storage(html: str, source_url: str) -> str:
    """Return a slim standalone HTML document suitable for GCS storage.

    Strips ``_STORAGE_STRIP_TAGS`` (script, style, noscript, iframe, svg) and
    wraps the remaining body in a minimal ``<!DOCTYPE html>`` shell that
    includes a ``<link rel="canonical">`` so Vertex content retrieval can
    resolve the origin URL.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    for el in soup(_STORAGE_STRIP_TAGS):
        el.decompose()
    body: Tag = soup.body if soup.body is not None else soup
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '  <meta charset="utf-8">\n'
        f"  <title>{title}</title>\n"
        f'  <link rel="canonical" href="{source_url}">\n'
        "</head>\n<body>\n"
        f"  {body.decode_contents()}\n"
        "</body>\n</html>"
    )
