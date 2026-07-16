"""Pure Zendesk-ticket → conversation-row mapping for the metrics sync."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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

CATEGORY_TO_DIVISION = {
    "apps": "Apps",
    "app": "Apps",
    "sales": "Sales",
    "lead": "Sales",
    "aftersales": "Aftersales",
    "service": "Aftersales",
    "charging": "Charging",
    "charger": "Charging",
}
_CATEGORY_TAG = re.compile(r"^category_(.+)$")
_SUBCAT_TAG = re.compile(r"^subcat_(.+)$")
_DEPT_TAG = re.compile(r"^dept_(.+)$")
_PIC_TAG = re.compile(r"^pic_(.+)$")
_SLA_TAG = re.compile(r"^sla_(\d+)$")


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
    category: str | None = None
    subcategory: str | None = None
    division: str | None = None
    department: str | None = None
    pic: str | None = None
    agent_id: str | None = None
    sla_minutes: int | None = None
    sla_deadline: str | None = None
    first_response_at: str | None = None
    resolved_at: str | None = None
    reopen_count: int | None = None


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


def _first_tag(tags: list[str], pattern: re.Pattern[str]) -> str | None:
    for tag in tags:
        m = pattern.match(tag)
        if m:
            return m.group(1)
    return None


def _sla_deadline(created_at: str | None, sla_minutes: int | None) -> str | None:
    if not created_at or sla_minutes is None:
        return None
    base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (base + timedelta(minutes=sla_minutes)).astimezone(UTC).isoformat()


def _timing_from_metric_set(
    metric_set: object, created_at: str | None
) -> tuple[str | None, str | None, int | None]:
    if not isinstance(metric_set, dict):
        return (None, None, None)
    resolved_at = metric_set.get("solved_at")
    reopens = metric_set.get("reopens")
    reopen_count = int(reopens) if reopens is not None else None
    first_response_at: str | None = None
    reply = metric_set.get("reply_time_in_minutes")
    if created_at and isinstance(reply, dict) and reply.get("calendar") is not None:
        base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        first_response_at = (
            (base + timedelta(minutes=int(reply["calendar"]))).astimezone(UTC).isoformat()
        )
    return (
        str(resolved_at) if resolved_at else None,
        first_response_at,
        reopen_count,
    )


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

    # Derive dimension fields from tags
    category = _first_tag(tags, _CATEGORY_TAG)
    subcategory = _first_tag(tags, _SUBCAT_TAG)
    department = _first_tag(tags, _DEPT_TAG)
    sla_raw = _first_tag(tags, _SLA_TAG)
    sla_minutes = int(sla_raw) if sla_raw is not None else None
    assignee = ticket.get("assignee_id")
    agent_id = str(assignee) if assignee else None
    pic = _first_tag(tags, _PIC_TAG) or agent_id
    division = CATEGORY_TO_DIVISION.get(category.lower()) if category else None

    resolved_at, first_response_at, reopen_count = _timing_from_metric_set(
        ticket.get("metric_set"), str(created) if created else None
    )

    return ConversationRow(
        conversation_id=str(ticket.get("id")),
        channel=channel_from_external_id(external_id_str),
        created_at=str(created) if created else None,
        updated_at=str(updated) if updated else None,
        status=status,
        resolved_by=_resolved_by(status),
        csat_score=csat,
        nps_score=nps,
        category=category,
        subcategory=subcategory,
        division=division,
        department=department,
        pic=pic,
        agent_id=agent_id,
        sla_minutes=sla_minutes,
        sla_deadline=_sla_deadline(str(created) if created else None, sla_minutes),
        first_response_at=first_response_at,
        resolved_at=resolved_at,
        reopen_count=reopen_count,
    )
