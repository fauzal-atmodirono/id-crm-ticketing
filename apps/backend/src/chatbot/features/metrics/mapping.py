"""Pure ticket/conversation → conversation-row mapping for the metrics sync.

Handles both the legacy Zendesk ticket shape (``map_ticket_to_row``) and the
Chatwoot conversation shape (``map_chatwoot_conversation_to_row``) that PROTON
uses after the Chatwoot+Zammad migration.
"""

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
_DIVISION_TAG = re.compile(r"^division_(.+)$")
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
    try:
        base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (base + timedelta(minutes=sla_minutes)).astimezone(UTC).isoformat()
    except ValueError:
        return None


def _timing_from_metric_set(
    metric_set: object, created_at: str | None
) -> tuple[str | None, str | None, int | None]:
    if not isinstance(metric_set, dict):
        return (None, None, None)
    resolved_at = metric_set.get("solved_at")
    reopens = metric_set.get("reopens")
    reopen_count: int | None = None
    if reopens is not None:
        try:
            reopen_count = int(reopens)
        except (ValueError, TypeError):
            reopen_count = None
    first_response_at: str | None = None
    reply = metric_set.get("reply_time_in_minutes")
    if created_at and isinstance(reply, dict) and reply.get("calendar") is not None:
        try:
            base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            first_response_at = (
                (base + timedelta(minutes=int(reply["calendar"]))).astimezone(UTC).isoformat()
            )
        except (ValueError, TypeError):
            first_response_at = None
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
    agent_id = str(assignee) if assignee is not None else None
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


# --- Chatwoot conversation -> conversation-row mapping ---------------------------
# A Chatwoot *conversation* is the unit of work (there is no ticket object). The
# AI classification is written back onto it as LABELS by ChatwootAdapter using the
# same tag-name convention parsed above (``category_*``, ``subcat_*``,
# ``division_*``, ``dept_*``, ``sla_<int>``). Timestamps are unix epoch seconds.
# A Chatwoot status of ``resolved`` means the AI/agent closed it; ``open`` /
# ``pending`` / ``snoozed`` mean a human is still handling it.
_CHATWOOT_AGENT_STATUSES = {"open", "pending", "snoozed"}


def _epoch_to_iso(value: object) -> str | None:
    """Convert a Chatwoot unix-epoch-seconds timestamp to an ISO-8601 string."""
    if value is None or not isinstance(value, (int, float, str)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def _chatwoot_source_id(conv: dict[str, object]) -> str | None:
    """Best-effort recover the source_id (== session_id) for channel inference.

    Chatwoot exposes it under a few shapes depending on the API surface; try the
    common ones. Falls back to None (→ channel "Other") when unavailable.
    """
    for key in ("source_id",):
        v = conv.get(key)
        if v:
            return str(v)
    meta = conv.get("meta")
    if isinstance(meta, dict):
        sender = meta.get("sender")
        if isinstance(sender, dict):
            ident = sender.get("identifier")
            if ident:
                return str(ident)
    add = conv.get("additional_attributes")
    if isinstance(add, dict):
        v = add.get("source_id") or add.get("session_id")
        if v:
            return str(v)
    return None


def _chatwoot_labels(conv: dict[str, object]) -> list[str]:
    raw = conv.get("labels")
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return []


def _chatwoot_agent_id(conv: dict[str, object]) -> str | None:
    meta = conv.get("meta")
    if not isinstance(meta, dict):
        return None
    assignee = meta.get("assignee")
    if isinstance(assignee, dict) and assignee.get("id") is not None:
        return str(assignee["id"])
    return None


def _chatwoot_sla_minutes(conv: dict[str, object], labels: list[str]) -> int | None:
    """Prefer the sla_<int> label; fall back to the custom_attributes value."""
    sla_raw = _first_tag(labels, _SLA_TAG)
    if sla_raw is not None:
        return int(sla_raw)
    ca = conv.get("custom_attributes")
    if isinstance(ca, dict) and ca.get("sla_minutes") is not None:
        try:
            return int(ca["sla_minutes"])
        except (ValueError, TypeError):
            return None
    return None


def map_chatwoot_conversation_to_row(conv: dict[str, object]) -> ConversationRow | None:
    """Map one Chatwoot conversation to a conversation row, or None to skip it.

    Reads the AI-classification LABELS (not Zendesk tags), ``meta.assignee.id``
    (not ``assignee_id``), and unix-epoch timestamps. Output schema is identical
    to ``map_ticket_to_row`` so ``bigquery_schema.CONVERSATIONS_SCHEMA`` fits.
    """
    labels = _chatwoot_labels(conv)
    csat = _csat_from_tags(labels)
    nps = _nps_from_tags(labels)
    source_id = _chatwoot_source_id(conv)
    if source_id is None and csat is None and nps is None and not labels:
        return None

    status = str(conv.get("status") or "")
    created_iso = _epoch_to_iso(conv.get("created_at"))
    updated_iso = _epoch_to_iso(conv.get("last_activity_at") or conv.get("updated_at"))

    category = _first_tag(labels, _CATEGORY_TAG)
    subcategory = _first_tag(labels, _SUBCAT_TAG)
    department = _first_tag(labels, _DEPT_TAG)
    # Prefer an explicit division_* label; else derive it from the category.
    division = _first_tag(labels, _DIVISION_TAG) or (
        CATEGORY_TO_DIVISION.get(category.lower()) if category else None
    )
    sla_minutes = _chatwoot_sla_minutes(conv, labels)

    agent_id = _chatwoot_agent_id(conv)
    pic = _first_tag(labels, _PIC_TAG) or agent_id

    # Timings we can derive from the Chatwoot conversation itself:
    #  - resolved_at  ← last_activity_at when the conversation is resolved
    #  - first_response_at ← first agent reply, when Chatwoot surfaces it
    # TODO(zammad-timing): the exact Zendesk-style metric_set (reply/resolution
    # calendar minutes, reopen count) lives on the joined Zammad ticket, not on
    # the Chatwoot conversation. A deep Zammad-ticket join is out of scope here;
    # these fields stay best-effort until that join is added.
    resolved_at = updated_iso if status == "resolved" else None
    first_response_at = _epoch_to_iso(conv.get("first_reply_created_at"))

    return ConversationRow(
        conversation_id=str(conv.get("id")),
        channel=channel_from_external_id(source_id),
        created_at=created_iso,
        updated_at=updated_iso,
        status=status,
        resolved_by="agent" if status in _CHATWOOT_AGENT_STATUSES else "bot",
        csat_score=csat,
        nps_score=nps,
        category=category,
        subcategory=subcategory,
        division=division,
        department=department,
        pic=pic,
        agent_id=agent_id,
        sla_minutes=sla_minutes,
        sla_deadline=_sla_deadline(created_iso, sla_minutes),
        first_response_at=first_response_at,
        resolved_at=resolved_at,
        reopen_count=None,  # TODO(zammad-timing): reopen count is a Zammad metric
    )
