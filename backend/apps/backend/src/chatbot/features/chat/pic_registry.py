"""dept→PIC lookup registry (Phase 2, item 12).

Loaded once from the PIC_MAP_JSON environment variable (a JSON object keyed by
department slug). Lookup normalises the key to lowercase so callers do not need
to worry about casing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PicEntry:
    pic_name: str
    pic_email: str
    pic_whatsapp: str
    zammad_group: str
    chatwoot_team_id: int | None = None


class PicRegistry:
    """In-memory dept→PIC table loaded from JSON config.

    Lookup is O(1) dict access after a one-time parse at construction.
    Keys are lower-cased at both load and lookup time so label values like
    ``dept_Apps`` and ``dept_apps`` resolve identically.
    """

    def __init__(self, table: dict[str, PicEntry]) -> None:
        self._table = table

    def lookup(self, department: str) -> PicEntry | None:
        """Return the PicEntry for *department* (case-insensitive) or None."""
        return self._table.get(department.lower())


def build_pic_registry(settings: Settings) -> PicRegistry:
    """Parse PIC_MAP_JSON and return a PicRegistry.

    Returns an empty registry (all lookups return None) when the JSON is
    absent, empty, or malformed — so a misconfigured map never crashes the app.
    """
    raw = (settings.pic_map_json or "").strip()
    if not raw:
        return PicRegistry({})
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("pic_map_json_parse_failed", error=str(exc))
        return PicRegistry({})
    if not isinstance(data, dict):
        _log.warning("pic_map_json_not_a_dict", got=type(data).__name__)
        return PicRegistry({})
    table: dict[str, PicEntry] = {}
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        try:
            table[key.lower()] = PicEntry(
                pic_name=str(val["pic_name"]),
                pic_email=str(val["pic_email"]),
                pic_whatsapp=str(val["pic_whatsapp"]),
                zammad_group=str(val["zammad_group"]),
                chatwoot_team_id=int(val["chatwoot_team_id"])
                if val.get("chatwoot_team_id") is not None
                else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("pic_map_entry_invalid", dept=key, error=str(exc))
    return PicRegistry(table)
