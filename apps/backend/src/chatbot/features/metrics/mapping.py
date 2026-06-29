"""Pure Zendesk-ticket → conversation-row mapping for the metrics sync."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CHANNEL_BY_PREFIX = {
    "whatsapp": "WhatsApp",
    "email": "Email",
    "phone": "Phone",
    "sim": "Web",
    "zendesk": "Web",
    "chatwoot": "Web",
}
_AGENT_STATUSES = {"new", "open", "pending", "hold"}
_CSAT_TAG = re.compile(r"^csat_([1-5])$")
_NPS_TAG = re.compile(r"^nps_(10|[0-9])$")


@dataclass(frozen=True)
class ConversationRow:
    conversation_id: str
    channel: str
    created_at: str | None
    updated_at: str | None
    status: str
    resolved_by: str
    csat_score: int | None
    nps_score: int | None


def channel_from_external_id(external_id: str | None) -> str:
    if not external_id:
        return "Other"
    prefix = external_id.split("-", 1)[0]
    return _CHANNEL_BY_PREFIX.get(prefix, "Other")


def _resolved_by(status: str) -> str:
    return "agent" if status in _AGENT_STATUSES else "bot"


def _csat_from_tags(tags: list[str]) -> int | None:
    for tag in tags:
        m = _CSAT_TAG.match(tag)
        if m:
            return int(m.group(1))
    return None


def _nps_from_tags(tags: list[str]) -> int | None:
    for tag in tags:
        m = _NPS_TAG.match(tag)
        if m:
            return int(m.group(1))
    return None


def map_ticket_to_row(ticket: dict[str, object]) -> ConversationRow | None:
    """Map one Zendesk ticket to a conversation row, or None to skip it."""
    external_id = ticket.get("external_id")
    external_id_str = str(external_id) if external_id else None
    raw_tags = ticket.get("tags") or []
    tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
    csat = _csat_from_tags(tags)
    nps = _nps_from_tags(tags)
    if external_id_str is None and csat is None and nps is None:
        return None
    status = str(ticket.get("status") or "")
    created = ticket.get("created_at")
    updated = ticket.get("updated_at")
    return ConversationRow(
        conversation_id=str(ticket.get("id")),
        channel=channel_from_external_id(external_id_str),
        created_at=str(created) if created else None,
        updated_at=str(updated) if updated else None,
        status=status,
        resolved_by=_resolved_by(status),
        csat_score=csat,
        nps_score=nps,
    )
