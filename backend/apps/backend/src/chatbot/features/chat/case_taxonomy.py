"""Case category/subcategory taxonomy — loaded once from CASE_TAXONOMY_JSON.

Mirrors pic_registry.py's fail-open JSON-parsing pattern: malformed/absent
config never crashes the app, it just yields an empty taxonomy (classification
falls back to accepting free text, matching pre-taxonomy behavior).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CategoryEntry:
    label: str
    subcategories: list[str] = field(default_factory=list)


class CaseTaxonomy:
    """slug -> CategoryEntry table, loaded once from JSON config."""

    def __init__(self, table: dict[str, CategoryEntry]) -> None:
        self._table = table

    def main_categories(self) -> list[str]:
        """Slugs, in the order they appeared in the source JSON."""
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

    def flattened_subcategory_options(self) -> list[str]:
        """'<Label>: <Subcategory>' for every category — the case_subcategory
        custom attribute definition's option list."""
        options: list[str] = []
        for entry in self._table.values():
            options.extend(f"{entry.label}: {sub}" for sub in entry.subcategories)
        return options

    def is_empty(self) -> bool:
        return not self._table


def build_case_taxonomy(settings: Settings) -> CaseTaxonomy:
    """Parse CASE_TAXONOMY_JSON and return a CaseTaxonomy.

    Returns an empty taxonomy (all validation calls return False/[]) when the
    JSON is absent, empty, or malformed — so a misconfigured taxonomy never
    crashes the app.
    """
    raw = (settings.case_taxonomy_json or "").strip()
    if not raw:
        return CaseTaxonomy({})
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("case_taxonomy_json_parse_failed", error=str(exc))
        return CaseTaxonomy({})
    if not isinstance(data, dict):
        _log.warning("case_taxonomy_json_not_a_dict", got=type(data).__name__)
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
            _log.warning("case_taxonomy_entry_invalid", slug=slug, error=str(exc))
    return CaseTaxonomy(table)
