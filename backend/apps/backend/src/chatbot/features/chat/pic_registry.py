"""dept→PIC lookup registry (Phase 2, item 12).

Loaded once from the PIC_MAP_JSON environment variable (a JSON object keyed by
department slug). Lookup normalises the key to lowercase so callers do not need
to worry about casing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.features.chat.pic_store import PicStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PicEntry:
    pic_name: str
    pic_email: str
    pic_whatsapp: str
    chatwoot_team_id: int | None = None
    # Extra "relevant personnel" CC'd on the escalation email (e.g. a manager or a
    # team distribution list). Empty = To-the-PIC only. Gated by escalation_cc_pic.
    cc_emails: list[str] = field(default_factory=list)


class PicRegistry:
    """dept→PIC lookup: Firestore-backed store first, JSON-config table fallback.

    Lookup is O(1) dict access (after an async store round-trip) once the
    table's one-time parse has happened at construction. Keys are lower-cased
    at both load and lookup time so label values like ``dept_Apps`` and
    ``dept_apps`` resolve identically.
    """

    def __init__(self, table: dict[str, PicEntry], store: PicStore | None = None) -> None:
        self._table = table
        self._store = store

    async def lookup(self, department: str) -> PicEntry | None:
        """Return the PicEntry for *department* (case-insensitive) or None.

        Store-first: checks the Firestore-backed PicStore (the operator-
        editable source of truth) before falling back to the legacy
        PIC_MAP_JSON-parsed table, so a tenant that never touches the new
        admin UI keeps working exactly as before.
        """
        key = department.lower()
        if self._store is not None:
            record = await self._store.get(key)
            if record is not None:
                return PicEntry(
                    pic_name=record.pic_name,
                    pic_email=record.pic_email,
                    pic_whatsapp=record.pic_whatsapp,
                    cc_emails=record.cc_emails,
                )
        return self._table.get(key)


def build_pic_registry(settings: Settings, store: PicStore | None = None) -> PicRegistry:
    """Parse PIC_MAP_JSON and return a PicRegistry.

    Returns an empty-table registry (a lookup still checks *store* first, then
    falls through to None) when the JSON is absent, empty, or malformed — so a
    misconfigured map never crashes the app.
    """
    raw = (settings.pic_map_json or "").strip()
    if not raw:
        return PicRegistry({}, store=store)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("pic_map_json_parse_failed", error=str(exc))
        return PicRegistry({}, store=store)
    if not isinstance(data, dict):
        _log.warning("pic_map_json_not_a_dict", got=type(data).__name__)
        return PicRegistry({}, store=store)
    table: dict[str, PicEntry] = {}
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        try:
            cc_raw = val.get("cc_emails", [])
            cc_emails = [str(x) for x in cc_raw] if isinstance(cc_raw, list) else []
            table[key.lower()] = PicEntry(
                pic_name=str(val["pic_name"]),
                pic_email=str(val["pic_email"]),
                pic_whatsapp=str(val["pic_whatsapp"]),
                chatwoot_team_id=int(val["chatwoot_team_id"])
                if val.get("chatwoot_team_id") is not None
                else None,
                cc_emails=cc_emails,
            )
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("pic_map_entry_invalid", dept=key, error=str(exc))
    return PicRegistry(table, store=store)
