"""SLA-timer escalation engine for PROTON on Chatwoot+Zammad.

PROTON migrated off Zendesk (which fires SLA triggers natively) to Chatwoot,
which has NO server-side SLA engine. This module replaces that missing capability
with an in-app APScheduler job that periodically scans the Chatwoot conversations
and fires SLA breaches INTERNALLY, recording each as an append-only transition on
the case ``AuditLogPort`` (and optionally alerting the PIC over WhatsApp/Twilio).

Two breaches are detected:

* ``SLA_BREACH_NO_RESPONSE`` — a conversation still ``open`` with no first agent
  reply, older than the response threshold.
* ``SLA_BREACH_UNRESOLVED`` — a conversation not yet ``resolved``, older than the
  resolution threshold.

Source of truth
    The live Chatwoot conversations list (``metrics.sync.fetch_conversations``,
    scoped to ``chatwoot_inbox_id``). Age is derived from the conversation's
    ``created_at`` (unix epoch seconds). "Has a first agent response" is derived
    from the conversation itself: Chatwoot surfaces ``first_reply_created_at`` once
    an agent replies (mirrors ``metrics.mapping``), so an ``open`` conversation
    with that field unset and no ``FIRST_RESPONSE`` audit entry counts as
    un-responded.

Thresholds
    Per-conversation ``sla_minutes`` (from the ``sla_<int>`` label or the
    ``custom_attributes.sla_minutes`` value written at escalation time) is
    preferred for the RESOLUTION threshold when present; otherwise the global
    ``sla_resolution_hours`` default applies. The NO-RESPONSE threshold always
    uses the global ``sla_response_hours`` (first-response SLA is a policy, not a
    per-ticket resolution budget).

Dedup
    Append-and-scan: before firing, the engine reads the ticket's audit trail via
    ``AuditLogPort.list_for_ticket`` and skips if an entry with the same breach
    ``to_state`` already exists. This is the simplest robust mechanism (no extra
    Firestore collection or fired-marker store to keep consistent) and works
    identically against the in-memory and Firestore audit logs, since the audit
    log is already the durable record of everything the engine does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from chatbot.features.chat.ports import AuditEntry, AuditLogPort
from chatbot.features.metrics.mapping import _chatwoot_sla_minutes
from chatbot.features.metrics.sync import fetch_conversations

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Breach markers double as the audit ``to_state`` AND the dedup key.
NO_RESPONSE_BREACH = "SLA_BREACH_NO_RESPONSE"
UNRESOLVED_BREACH = "SLA_BREACH_UNRESOLVED"
TIER2_ESCALATION = "TIER2_ESCALATION"
FIRST_RESPONSE_STATE = "FIRST_RESPONSE"
REMINDER_WARNING_STATE = "REMINDER_WARNING"

_SECONDS_PER_HOUR = 3600


def _conversation_age_seconds(conv: dict[str, Any], now: datetime) -> float | None:
    """Age of a Chatwoot conversation in seconds, from its epoch ``created_at``."""
    created = conv.get("created_at")
    if not isinstance(created, (int, float, str)):
        return None
    try:
        created_at = datetime.fromtimestamp(float(created), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None
    return (now - created_at).total_seconds()


def _has_first_agent_response(conv: dict[str, Any], prior_states: set[str]) -> bool:
    """True when an agent has already replied to this conversation.

    Derived from the conversation itself (Chatwoot sets ``first_reply_created_at``
    on the first outgoing agent message) OR from a previously recorded
    ``FIRST_RESPONSE`` audit transition (the webhook path records it), so the scan
    agrees with the live-webhook signal even if the API field lags.
    """
    if conv.get("first_reply_created_at"):
        return True
    return FIRST_RESPONSE_STATE in prior_states


async def _prior_states(audit: AuditLogPort, ticket_id: str) -> set[str]:
    """All ``to_state`` values already recorded for a ticket (dedup + response)."""
    entries = await audit.list_for_ticket(ticket_id)
    return {e.to_state for e in entries}


async def scan_conversations(  # noqa: PLR0912, PLR0915
    settings: Settings,
    audit: AuditLogPort,
    *,
    now: datetime | None = None,
    fetch: Callable[[Settings], list[dict[str, Any]]] | None = None,
    alert: Callable[[str, str, str], Any] | None = None,
    level2_alert: Callable[[str, str, str], Any] | None = None,
) -> list[AuditEntry]:
    """Scan Chatwoot conversations once and fire any un-fired SLA breaches.

    Returns the list of breach ``AuditEntry`` rows appended this run (empty when
    nothing breached / everything already fired). ``now``, ``fetch`` and ``alert``
    are injectable so tests use short thresholds, a fake clock, and a mocked
    Chatwoot fetch instead of waiting real hours.
    """
    clock = now or datetime.now(UTC)
    conversations = (fetch or fetch_conversations)(settings)
    response_threshold = settings.sla_response_hours * _SECONDS_PER_HOUR
    resolution_threshold_default = settings.sla_resolution_hours * _SECONDS_PER_HOUR

    fired: list[AuditEntry] = []
    for conv in conversations:
        conv_id = conv.get("id")
        if conv_id is None:
            continue
        ticket_id = str(conv_id)
        status = str(conv.get("status") or "")
        age = _conversation_age_seconds(conv, clock)
        if age is None:
            continue

        prior = await _prior_states(audit, ticket_id)
        session_id = f"chatwoot-conv-{ticket_id}"

        # Per-conversation SLA (minutes) overrides the global resolution default.
        sla_minutes = _chatwoot_sla_minutes(conv, _labels(conv))
        resolution_threshold = (
            sla_minutes * 60 if sla_minutes is not None else resolution_threshold_default
        )

        # 8h no first agent response: conversation still open, no agent reply yet.
        if (
            status == "open"
            and not _has_first_agent_response(conv, prior)
            and age > response_threshold
            and NO_RESPONSE_BREACH not in prior
        ):
            entry = await _fire(
                audit,
                ticket_id=ticket_id,
                session_id=session_id,
                to_state=NO_RESPONSE_BREACH,
                remark=(
                    f"No first agent response after "
                    f"{settings.sla_response_hours}h (age {age / _SECONDS_PER_HOUR:.1f}h)"
                ),
                clock=clock,
                alert=alert,
            )
            fired.append(entry)

        # 48h unresolved: conversation not resolved and older than the resolution SLA.
        if status != "resolved" and age > resolution_threshold and UNRESOLVED_BREACH not in prior:
            entry = await _fire(
                audit,
                ticket_id=ticket_id,
                session_id=session_id,
                to_state=UNRESOLVED_BREACH,
                remark=(
                    f"Unresolved past SLA ({resolution_threshold / _SECONDS_PER_HOUR:.1f}h; "
                    f"age {age / _SECONDS_PER_HOUR:.1f}h)"
                ),
                clock=clock,
                alert=alert,
            )
            fired.append(entry)

        # Tier-2 escalation: if an UNRESOLVED_BREACH was already fired and is
        # older than escalation_tier2_hours, fire the level2_alert callback
        # EXACTLY ONCE — gated on TIER2_ESCALATION not yet in the audit trail.
        # After firing, we record a TIER2_ESCALATION audit entry so subsequent
        # scans skip it (mirrors the UNRESOLVED_BREACH dedup pattern exactly).
        if (
            level2_alert is not None
            and UNRESOLVED_BREACH in prior
            and TIER2_ESCALATION not in prior
        ):
            tier2_threshold = settings.escalation_tier2_hours * _SECONDS_PER_HOUR
            # Find the age of the UNRESOLVED_BREACH entry.
            all_entries = await audit.list_for_ticket(ticket_id)
            breach_entry = next(
                (e for e in all_entries if e.to_state == UNRESOLVED_BREACH), None
            )
            if breach_entry is not None:
                try:
                    breach_at = datetime.fromisoformat(breach_entry.at)
                    breach_age = (clock - breach_at).total_seconds()
                except (ValueError, TypeError):
                    breach_age = 0.0
                if breach_age >= tier2_threshold:
                    remark = (
                        f"Unresolved breach older than "
                        f"{settings.escalation_tier2_hours}h; re-alerting level-2"
                    )
                    # Record the marker BEFORE calling the alert so a crash in
                    # the alert callback does not leave the dedup entry missing.
                    tier2_entry = AuditEntry(
                        ticket_id=ticket_id,
                        session_id=session_id,
                        actor="sla-engine",
                        from_state=UNRESOLVED_BREACH,
                        to_state=TIER2_ESCALATION,
                        at=clock.isoformat(),
                        remark=remark,
                    )
                    await audit.append(tier2_entry)
                    _log.info(
                        "sla_tier2_escalation_recorded", ticket_id=ticket_id
                    )
                    try:
                        result = level2_alert(
                            ticket_id,
                            TIER2_ESCALATION,
                            remark,
                        )
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        _log.warning(
                            "level2_alert_failed",
                            ticket_id=ticket_id,
                            error=str(exc),
                        )

        # Phase 6: fire a one-time "approaching SLA" warning reminder when the
        # conversation is inside the warning window (not yet breached). Rides the
        # same `alert` callback as breaches (→ WhatsApp when configured). Deduped
        # via REMINDER_WARNING_STATE in the audit trail, exactly like the breaches.
        if settings.tasks_reminder_whatsapp_enabled and status != "resolved":
            warning_threshold_sec = settings.tasks_reminder_warning_minutes * 60
            resolution_remaining = resolution_threshold - age
            if (
                0 < resolution_remaining < warning_threshold_sec
                and REMINDER_WARNING_STATE not in prior
            ):
                reminder_entry = await _fire(
                    audit,
                    ticket_id=ticket_id,
                    session_id=session_id,
                    to_state=REMINDER_WARNING_STATE,
                    remark=(
                        f"SLA reminder: {resolution_remaining / 60:.0f} min until resolution SLA "
                        f"(warning threshold {settings.tasks_reminder_warning_minutes} min)"
                    ),
                    clock=clock,
                    alert=alert,
                )
                fired.append(reminder_entry)

    if fired:
        _log.info("sla_scan_fired", count=len(fired))
    return fired


def _labels(conv: dict[str, Any]) -> list[str]:
    raw = conv.get("labels")
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return []


async def _fire(
    audit: AuditLogPort,
    *,
    ticket_id: str,
    session_id: str,
    to_state: str,
    remark: str,
    clock: datetime,
    alert: Callable[[str, str, str], Any] | None,
) -> AuditEntry:
    """Append one breach transition and (best-effort) alert the PIC."""
    entry = AuditEntry(
        ticket_id=ticket_id,
        session_id=session_id,
        actor="sla-engine",
        from_state="OPEN",
        to_state=to_state,
        at=clock.isoformat(),
        remark=remark,
    )
    await audit.append(entry)
    _log.info("sla_breach_recorded", ticket_id=ticket_id, breach=to_state)
    if alert is not None:
        try:
            result = alert(ticket_id, to_state, remark)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:  # an alert failure must never abort the scan
            _log.warning("sla_alert_failed", ticket_id=ticket_id, error=str(e))
    return entry


def run_sla_scan_job(
    settings: Settings,
    audit: AuditLogPort,
    *,
    twilio_adapter: TwilioChannelAdapter | None = None,
) -> list[AuditEntry]:
    """Synchronous entry point for the scheduler. Best-effort: never raises."""
    alert = _build_pic_alert(settings, twilio_adapter)
    level2_alert = _build_level2_alert(settings, twilio_adapter)
    try:
        return asyncio.run(
            scan_conversations(settings, audit, alert=alert, level2_alert=level2_alert)
        )
    except Exception as e:  # a failed scheduled scan must never crash the app
        _log.error("sla_scan_job_failed", error=str(e))
        return []


def _build_pic_alert(
    settings: Settings, twilio_adapter: TwilioChannelAdapter | None
) -> Callable[[str, str, str], Any] | None:
    """Build the optional PIC WhatsApp alert callback (reuses the Twilio path).

    Returns None when no PIC number / Twilio adapter is configured, so no alert is
    attempted. The audit transition is always recorded regardless.
    """
    pic = settings.sla_pic_whatsapp
    if not pic or twilio_adapter is None:
        return None
    to = "whatsapp:" + pic.removeprefix("whatsapp:")

    async def _alert(ticket_id: str, to_state: str, remark: str) -> None:
        await twilio_adapter.send_message(
            conversation_id=to,
            text=f"⚠️ SLA breach ({to_state}) on case {ticket_id}. {remark}",
        )

    return _alert


def _build_level2_alert(
    settings: Settings, twilio_adapter: TwilioChannelAdapter | None
) -> Callable[[str, str, str], Any] | None:
    """Build the optional level-2 WhatsApp alert callback.

    Uses ``escalation_level2_whatsapp`` for the target number. Returns None
    when no level-2 number or Twilio adapter is configured.
    """
    target = settings.escalation_level2_whatsapp
    if not target or twilio_adapter is None:
        return None
    to = "whatsapp:" + target.removeprefix("whatsapp:")

    async def _alert(ticket_id: str, to_state: str, remark: str) -> None:  # noqa: ARG001
        await twilio_adapter.send_message(
            conversation_id=to,
            text=f"⚠️ TIER-2 escalation on case {ticket_id}. {remark}",
        )

    return _alert


def start_sla_scheduler(
    settings: Settings,
    audit: AuditLogPort,
    *,
    twilio_adapter: TwilioChannelAdapter | None = None,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the in-app SLA scan scheduler when enabled; else return None.

    Mirrors ``metrics.scheduler.start_metrics_scheduler``: guarded behind
    ``sla_engine_enabled`` (default False, so nothing runs unless opted in) and
    injectable (``scheduler`` / ``job``) for tests.
    """
    if not settings.sla_engine_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_sla_scan_job(settings, audit, twilio_adapter=twilio_adapter))
    sched.add_job(
        run,
        trigger="interval",
        minutes=settings.sla_scan_interval_minutes,
        id="chatwoot_sla_scan",
        next_run_time=datetime.now(UTC),  # first scan shortly after startup
        replace_existing=True,
    )
    sched.start()
    _log.info(
        "sla_scheduler_started",
        interval_minutes=settings.sla_scan_interval_minutes,
        response_hours=settings.sla_response_hours,
        resolution_hours=settings.sla_resolution_hours,
    )
    return sched
