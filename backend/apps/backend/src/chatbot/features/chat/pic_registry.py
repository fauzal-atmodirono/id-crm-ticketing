"""dept→PIC lookup registry (Phase 2, item 12).

Loaded once from the PIC_MAP_JSON environment variable (a JSON object keyed by
department slug). Lookup normalises the key to lowercase so callers do not need
to worry about casing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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
    # P2 task 7: who tier-2 wakes up. Empty falls back to the PIC.
    escalation_manager_email: str = ""
    escalation_manager_whatsapp: str = ""


@dataclass(frozen=True)
class PicResolution:
    """Who to tell, and whether anybody is actually there."""

    entry: PicEntry | None
    recipients: list[str]
    all_offline: bool

    @property
    def tier2_recipients(self) -> list[str]:
        """Who tier-2 wakes up.

        Falls back to the original recipients when no manager is configured:
        better the same people twice than nobody. The caller logs the
        fallback so an unconfigured department is visible rather than silent.
        """
        if self.entry is not None and self.entry.escalation_manager_email:
            return [self.entry.escalation_manager_email]
        return list(self.recipients)


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
                    escalation_manager_email=record.escalation_manager_email,
                    escalation_manager_whatsapp=record.escalation_manager_whatsapp,
                )
        return self._table.get(key)

    async def resolve(
        self, department: str, *, presence: Any | None = None
    ) -> PicResolution:
        """Resolve a department to the people who should actually be told.

        `presence`, when supplied, WIDENS the recipient list: an offline PIC's
        colleagues are added so somebody at their desk sees it. It never
        narrows -- the PIC is always included, whatever their status, and every
        failure mode (presence unreachable, PIC absent from Chatwoot, empty
        agent list) resolves to "notify everyone we know about".

        That asymmetry is the whole safety argument. A presence check that
        could shrink the list to nothing would be capable of causing exactly
        the silent unescalated complaint this package exists to eliminate, so
        it is built so that it cannot.

        `all_offline` is reported rather than acted on here; tier-2 uses it to
        shorten its timer, because when nobody is on duty the full waiting
        window is spent on hours where no one was ever going to respond.
        """
        entry = await self.lookup(department)
        if entry is None:
            return PicResolution(entry=None, recipients=[], all_offline=False)

        recipients = [entry.pic_email] if entry.pic_email else []
        if presence is None:
            return PicResolution(
                entry=entry, recipients=recipients, all_offline=False
            )

        try:
            agents = await presence.fetch_agents()
        except Exception as exc:
            _log.warning("escalation_presence_fetch_failed", error=str(exc))
            return PicResolution(
                entry=entry, recipients=recipients, all_offline=False
            )

        status_by_email = {
            str(getattr(a, "email", "") or "").strip().lower():
                str(getattr(a, "availability_status", "") or "").lower()
            for a in agents or []
        }

        def _present(email: str) -> bool | None:
            """True/False when Chatwoot knows this person, None when it does
            not. A PIC who is not a Chatwoot agent at all (a dealer principal)
            has no presence to read, and unknown is not the same as offline."""
            status = status_by_email.get(email.strip().lower())
            if status is None:
                return None
            return status in ("online", "busy")

        pic_present = _present(entry.pic_email)
        known = [pic_present] if pic_present is not None else []

        for cc in entry.cc_emails:
            cc_present = _present(cc)
            if cc_present is not None:
                known.append(cc_present)
            # Only pull a colleague in when the PIC is known to be away --
            # otherwise the CC group's own gate (escalation_cc_pic) decides.
            if pic_present is False and cc_present:
                recipients.append(cc)

        # Unknown presence is not "everyone is offline". With nothing known,
        # claiming the department is dark would shorten the tier-2 timer on a
        # guess.
        all_offline = bool(known) and not any(known)
        return PicResolution(
            entry=entry, recipients=recipients, all_offline=all_offline
        )


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
