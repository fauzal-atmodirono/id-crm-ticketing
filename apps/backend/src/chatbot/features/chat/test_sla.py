"""Unit tests for the SLA-timer escalation engine (features/chat/sla.py).

Uses INJECTED short thresholds + a fake clock + a mocked Chatwoot fetch, so no
real 8h/48h waits are needed. Settings are constructed with explicit overrides
(Settings(_env_file=None, ...)) to stay isolated from any local .env.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.sla import (
    FIRST_RESPONSE_STATE,
    NO_RESPONSE_BREACH,
    REMINDER_WARNING_STATE,
    UNRESOLVED_BREACH,
    scan_conversations,
    start_sla_scheduler,
)
from chatbot.platform.config import Settings

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "sla_scan_interval_minutes": 15,
        "chatwoot_inbox_id": 1,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float, now: datetime = _NOW) -> int:
    return int((now - timedelta(hours=hours_ago)).timestamp())


def _conv(conv_id: int, **fields: Any) -> dict[str, Any]:
    conv: dict[str, Any] = {"id": conv_id, "status": "open"}
    conv.update(fields)
    return conv


def test_no_response_breach_fires_once_and_dedups() -> None:
    """OPEN, no agent reply, older than response threshold -> ONE breach; a second
    scan does NOT double-fire."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=8, sla_resolution_hours=1000)
    conv = _conv(101, status="open", created_at=_epoch(10))  # 10h old > 8h

    def fetch(_s: Settings) -> list[dict[str, Any]]:
        return [conv]

    fired = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=fetch))
    assert len(fired) == 1
    assert fired[0].to_state == NO_RESPONSE_BREACH
    assert fired[0].ticket_id == "101"
    assert fired[0].actor == "sla-engine"

    # Re-run: dedup must prevent a second no-response breach.
    fired_again = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=fetch))
    assert fired_again == []
    entries = asyncio.run(audit.list_for_ticket("101"))
    assert sum(1 for e in entries if e.to_state == NO_RESPONSE_BREACH) == 1


def test_unresolved_breach_fires() -> None:
    """Not resolved and older than the resolution threshold -> unresolved breach."""
    audit = InMemoryAuditLog()
    # High response threshold so ONLY the unresolved rule can fire here.
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=48)
    conv = _conv(202, status="pending", created_at=_epoch(50))  # 50h old > 48h

    fired = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv]))
    assert len(fired) == 1
    assert fired[0].to_state == UNRESOLVED_BREACH
    assert fired[0].ticket_id == "202"


def test_resolved_conversation_no_breach() -> None:
    """A resolved conversation never breaches."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=1, sla_resolution_hours=1)
    conv = _conv(303, status="resolved", created_at=_epoch(100))

    fired = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv]))
    assert fired == []


def test_recently_responded_conversation_no_no_response_breach() -> None:
    """An OPEN conversation with a first agent reply does NOT no-response breach."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=8, sla_resolution_hours=1000)
    conv = _conv(
        404,
        status="open",
        created_at=_epoch(10),
        first_reply_created_at=_epoch(9),  # agent already replied
    )
    fired = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv]))
    assert fired == []


def test_young_conversation_no_breach() -> None:
    """A conversation younger than both thresholds does not breach."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=8, sla_resolution_hours=48)
    conv = _conv(505, status="open", created_at=_epoch(1))  # 1h old
    fired = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv]))
    assert fired == []


def test_per_conversation_sla_label_overrides_resolution_default() -> None:
    """A sla_<int> label (minutes) overrides the global resolution default."""
    audit = InMemoryAuditLog()
    # Global resolution is huge, but the label says 60 minutes, so a 2h-old
    # unresolved conversation breaches on the per-conversation SLA.
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=10000)
    conv = _conv(606, status="open", created_at=_epoch(2), labels=["sla_60"])
    fired = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv]))
    assert len(fired) == 1
    assert fired[0].to_state == UNRESOLVED_BREACH


def test_first_response_audit_marker_suppresses_no_response_breach() -> None:
    """A prior FIRST_RESPONSE audit marker (from the webhook) counts as responded."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=8, sla_resolution_hours=1000)
    asyncio.run(
        audit.append(
            AuditEntry(
                ticket_id="707",
                session_id="chatwoot-conv-707",
                actor="agent",
                from_state="",
                to_state=FIRST_RESPONSE_STATE,
                at=_NOW.isoformat(),
                remark="first agent response",
            )
        )
    )
    conv = _conv(707, status="open", created_at=_epoch(10))
    fired = asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv]))
    assert fired == []


def test_breach_fires_pic_alert_callback() -> None:
    """The injected alert callback is invoked once per fired breach."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=8, sla_resolution_hours=1000)
    conv = _conv(808, status="open", created_at=_epoch(10))
    calls: list[tuple[str, str, str]] = []

    def alert(ticket_id: str, to_state: str, remark: str) -> None:
        calls.append((ticket_id, to_state, remark))

    fired = asyncio.run(
        scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv], alert=alert)
    )
    assert len(fired) == 1
    assert len(calls) == 1
    assert calls[0][0] == "808"
    assert calls[0][1] == NO_RESPONSE_BREACH


# --- scheduler guard -------------------------------------------------------


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})

    def start(self) -> None:
        self.started = True


def test_scheduler_not_started_when_disabled() -> None:
    settings = _settings(sla_engine_enabled=False)
    assert start_sla_scheduler(settings, InMemoryAuditLog()) is None


def test_scheduler_started_when_enabled() -> None:
    settings = _settings(sla_engine_enabled=True, sla_scan_interval_minutes=15)
    fake = _FakeScheduler()
    result = start_sla_scheduler(settings, InMemoryAuditLog(), scheduler=fake)
    assert result is fake
    assert fake.started is True
    assert len(fake.jobs) == 1
    job = fake.jobs[0]
    assert job["trigger"] == "interval"
    assert job["minutes"] == 15
    assert job["id"] == "chatwoot_sla_scan"


# --- Phase 6: WA reminder tests ---


def test_wa_reminder_fires_within_warning_window() -> None:
    """REMINDER_WARNING_STATE fires when remaining time is strictly inside the warning window.

    Setup: resolution SLA = 48h, warning = 120 min (2h), conv is 47h old → 1h (3600s)
    remaining, which is strictly less than 7200s warning threshold. Response threshold is
    set to 1000h so the no-response breach cannot interfere.
    """
    audit = InMemoryAuditLog()
    settings = _settings(
        sla_response_hours=1000,
        sla_resolution_hours=48,
        tasks_reminder_whatsapp_enabled=True,
        tasks_reminder_warning_minutes=120,
    )
    # 47h old → resolution_threshold = 48*3600 = 172800s; age = 47*3600 = 169200s
    # resolution_remaining = 3600s (1h) < warning_threshold = 7200s (2h) → FIRES
    conv = _conv(901, status="open", created_at=_epoch(47))

    sent: list[str] = []

    async def fake_alert(ticket_id: str, to_state: str, remark: str) -> None:  # noqa: ARG001
        sent.append(to_state)

    asyncio.run(
        scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv], alert=fake_alert)
    )
    assert REMINDER_WARNING_STATE in sent


def test_wa_reminder_dedups_across_scans() -> None:
    """REMINDER_WARNING_STATE dedup: running scan twice fires the reminder exactly once.

    The second scan reads the REMINDER_WARNING_STATE entry from the audit trail via
    ``_prior_states`` and skips re-firing (identical to the UNRESOLVED_BREACH dedup).
    """
    audit = InMemoryAuditLog()
    settings = _settings(
        sla_response_hours=1000,
        sla_resolution_hours=48,
        tasks_reminder_whatsapp_enabled=True,
        tasks_reminder_warning_minutes=120,
    )
    conv = _conv(902, status="open", created_at=_epoch(47))

    count = [0]

    async def counting_alert(ticket_id: str, to_state: str, remark: str) -> None:  # noqa: ARG001
        if to_state == REMINDER_WARNING_STATE:
            count[0] += 1

    asyncio.run(
        scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv], alert=counting_alert)
    )
    asyncio.run(
        scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv], alert=counting_alert)
    )
    assert count[0] == 1
