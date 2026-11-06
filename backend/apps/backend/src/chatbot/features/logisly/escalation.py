"""File a Logisly driver escalation as a ready-to-work Chatwoot ticket.

When the driver agent escalates (`escalate_to_operations_tool`), the
operations person who picks the conversation up should not have to read the
whole WhatsApp thread or replay a voice note to know what happened. This
posts one private note with the reason, the agent's summary (which already
says what the photo or voice note showed), the trip, the tracking alert that
started the conversation and the driver's reports so far; labels the
conversation by reason; sets its priority; and records the escalation on the
trip itself.

Called from the backend's /chat/turn handoff branch for `crm-<id>` sessions
only, and fail-open: a Chatwoot outage costs the ticket its note, never the
handoff (the agent service still reopens the conversation for a human).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.logisly import trips as trips_module
from chatbot.features.logisly.channel import ChatwootDriverChannel

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

WIB = timezone(timedelta(hours=7))

# reason -> (label in Chatwoot, priority, heading in Bahasa Indonesia)
REASONS: dict[str, tuple[str, str, str]] = {
    "safety_incident": ("safety-incident", "urgent", "Kecelakaan / risiko keselamatan"),
    "cargo_issue": ("cargo-issue", "high", "Muatan rusak / bermasalah"),
    "trip_blocked": ("trip-blocked", "high", "Truk tidak bisa melanjutkan perjalanan"),
    "help_request": ("driver-request", "medium", "Driver minta dihubungi"),
}
ESCALATION_LABEL = "logisly-escalation"


def _wib(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(WIB).strftime("%H:%M")
    except ValueError:
        return iso


def order_for_session(session_id: str, state: dict[str, Any]) -> str | None:
    active = trips_module.ACTIVE_SESSIONS.get(session_id) or {}
    return state.get("order_id") or active.get("order_id") or None


def build_ticket_note(session_id: str, state: dict[str, Any]) -> str:
    reason = state.get("handoff_reason") or "help_request"
    _, priority, heading = REASONS.get(reason, ("", "medium", reason))
    summary = (state.get("handoff_summary") or "").strip() or "(tidak ada ringkasan)"
    lines = [
        f"🚨 Eskalasi driver: {heading} (prioritas {priority.upper()})",
        "",
        "Ringkasan AI:",
        summary,
    ]

    order_id = order_for_session(session_id, state)
    trip = trips_module.TRIPS.get(order_id) if order_id else None
    if trip is not None:
        lines += [
            "",
            f"Trip {trip['order_id']} · {trip['driver_name']} · {trip['plate']} ({trip['truck_type']})",
            f"Rute: {trip['origin']} → {trip['destination']}",
            f"Penerima: {trip['consignee']}",
            f"Muatan: {trip['cargo']}",
            f"Posisi terakhir: {trip['location']} · ETA {trip['eta']}",
        ]
        active = trips_module.ACTIVE_SESSIONS.get(session_id)
        if active:
            label = trips_module.ANOMALIES[active["anomaly"]]["label"]
            lines += [
                "",
                f"Alert pelacakan: {label}",
                f'Pesan pembuka ke driver: "{active["opening"]}"',
            ]
        reports = [e for e in trip["events"] if not str(e["status"]).startswith("alert:")]
        if reports:
            lines += ["", "Laporan driver:"]
            lines += [f"• {_wib(e['at'])} WIB · {e['status']} · {e['note']}" for e in reports[-5:]]
    else:
        lines += ["", "Trip: belum teridentifikasi (driver belum menyebut nomor order)."]
    return "\n".join(lines)


async def file_escalation(
    settings: Settings,
    session_id: str,
    state: dict[str, Any],
    channel: ChatwootDriverChannel | None = None,
) -> None:
    """Record the escalation on the trip and post the ticket to Chatwoot."""
    reason = state.get("handoff_reason") or "help_request"
    label, priority, heading = REASONS.get(reason, ("driver-request", "medium", reason))
    order_id = order_for_session(session_id, state)
    if order_id:
        summary = (state.get("handoff_summary") or heading).strip()
        trips_module.TRIPS.update(order_id, "issue", f"Eskalasi ke operasional: {summary}")

    if not session_id.startswith("crm-"):
        return
    try:
        conversation_id = int(session_id.removeprefix("crm-"))
    except ValueError:
        return
    cw = channel or ChatwootDriverChannel(settings)
    note = build_ticket_note(session_id, state)
    try:
        await cw.add_private_note(conversation_id, note)
        await cw.add_labels(conversation_id, [ESCALATION_LABEL, label])
        await cw.set_priority(conversation_id, priority)
    except Exception as exc:  # fail-open: the handoff itself must still happen
        _log.warning(
            "logisly_escalation_ticket_failed", conversation_id=conversation_id, error=str(exc)
        )
        return
    _log.info(
        "logisly_escalation_filed",
        conversation_id=conversation_id,
        reason=reason,
        order_id=order_id,
    )
