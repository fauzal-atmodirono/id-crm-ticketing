from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

_CHROME_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "iframe", "svg"]


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
    for el in node(_CHROME_TAGS):
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
