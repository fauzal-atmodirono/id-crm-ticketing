"""Case category/subcategory taxonomy for the resolution-time fallback
classifier (services/categorize.py). Mirrors backend/'s case_taxonomy.py —
duplicated, not shared, per this repo's agent/backend decoupling."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CategoryEntry:
    label: str
    subcategories: list[str] = field(default_factory=list)


class CaseTaxonomy:
    def __init__(self, table: dict[str, CategoryEntry]) -> None:
        self._table = table

    def main_categories(self) -> list[str]:
        return list(self._table.keys())

    def label_for(self, slug: str) -> str | None:
        entry = self._table.get(slug.lower())
        return entry.label if entry else None

    def subcategories_for(self, slug: str) -> list[str]:
        entry = self._table.get(slug.lower())
        return list(entry.subcategories) if entry else []

    def is_valid_category(self, slug: str) -> bool:
        return slug.lower() in self._table

    def is_valid_subcategory(self, slug: str, subcategory: str) -> bool:
        return subcategory in self.subcategories_for(slug)

    def is_empty(self) -> bool:
        return not self._table


def build_case_taxonomy(settings: Settings) -> CaseTaxonomy:
    raw = (settings.case_taxonomy_json or "").strip()
    if not raw:
        return CaseTaxonomy({})
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("case_taxonomy_json_parse_failed: %s", exc)
        return CaseTaxonomy({})
    if not isinstance(data, dict):
        _log.warning("case_taxonomy_json_not_a_dict: got %s", type(data).__name__)
        return CaseTaxonomy({})
    table: dict[str, CategoryEntry] = {}
    for slug, val in data.items():
        if not isinstance(val, dict):
            continue
        try:
            subs_raw = val.get("subcategories", [])
            subcategories = [str(s) for s in subs_raw] if isinstance(subs_raw, list) else []
            table[slug.lower()] = CategoryEntry(label=str(val["label"]), subcategories=subcategories)
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("case_taxonomy_entry_invalid slug=%s: %s", slug, exc)
    return CaseTaxonomy(table)
