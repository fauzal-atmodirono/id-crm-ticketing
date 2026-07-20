from __future__ import annotations

import re
from typing import Any

# "PROTON - PROTON All-New Saga" -> the scraper prefixes the brand, then the page
# title repeats it. Drop the leading "X - " when the remainder also starts with X.
_DOUBLED_BRAND = re.compile(r"^(\S+)\s*-\s*(?=\1\b)", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Tidy a scraped card title: collapse whitespace and de-double the brand."""
    collapsed = re.sub(r"\s+", " ", title or "").strip()
    return _DOUBLED_BRAND.sub("", collapsed).strip()


def clean_description(text: str, max_len: int = 200) -> str:
    """Collapse scraped run-on whitespace and trim to a word boundary."""
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if len(collapsed) <= max_len:
        return collapsed
    truncated = collapsed[:max_len].rsplit(" ", 1)[0]
    return truncated.rstrip(" ,.;:") + "…"


def dedupe_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate model cards, keyed by cleaned, case-insensitive title.

    Vertex Search returns the same model page more than once (e.g. `/all-new-saga`
    and `/models/all-new-saga`), which surfaces as repeated carousel cards.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for card in cards:
        key = clean_title(str(card.get("title", ""))).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(card)
    return out
