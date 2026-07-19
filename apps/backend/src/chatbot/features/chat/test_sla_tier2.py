"""Unit tests for the tier-2 higher-level escalation (Phase 2, item 15).

Uses the same injected-clock/fetch/callback pattern as test_sla.py.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.sla import (
    UNRESOLVED_BREACH,
    scan_conversations,
)
from chatbot.platform.config import Settings

_NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "sla_scan_interval_minutes": 15,
        "chatwoot_inbox_id": 1,
        "escalation_tier2_hours": 4.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float, now: datetime = _NOW) -> int:
    return int((now - timedelta(hours=hours_ago)).timestamp())


def _conv(conv_id: int, **fields: Any) -> dict[str, Any]:
    conv: dict[str, Any] = {"id": conv_id, "status": "open"}
    conv.update(fields)
    return conv


async def test_level2_alert_fires_when_unresolved_breach_older_than_tier2() -> None:
    """UNRESOLVED_BREACH older than tier2 hours → level2_alert fires."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=48,
                         escalation_tier2_hours=4.0)
    # Conv is 56h old (48h breach + 8h more, well past 4h tier2 window)
    conv = _conv(301, status="pending", created_at=_epoch(56))
    # Pre-seed the UNRESOLVED_BREACH audit entry as if the first scan already fired it
    breach_at = _NOW - timedelta(hours=8)  # breach fired 8h ago, tier2 = 4h → should re-alert
    await audit.append(AuditEntry(
        ticket_id="301",
        session_id="chatwoot-conv-301",
        actor="sla-engine",
        from_state="OPEN",
        to_state=UNRESOLVED_BREACH,
        at=breach_at.isoformat(),
        remark="test breach",
    ))

    level2_calls: list[tuple[str, str, str]] = []

    def level2(ticket_id: str, to_state: str, remark: str) -> None:
        level2_calls.append((ticket_id, to_state, remark))

    # This scan should NOT re-fire UNRESOLVED_BREACH (already recorded), but SHOULD
    # fire the level2 alert because the breach is older than tier2_hours.
    fired = await scan_conversations(
        settings, audit, now=_NOW, fetch=lambda _s: [conv], level2_alert=level2
    )
    assert fired == []  # no new breach entries
    assert len(level2_calls) == 1
    assert level2_calls[0][0] == "301"


async def test_level2_alert_does_not_fire_when_breach_too_recent() -> None:
    """If the breach is younger than tier2_hours, level2 must NOT fire yet."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=48,
                         escalation_tier2_hours=4.0)
    conv = _conv(302, status="pending", created_at=_epoch(50))
    breach_at = _NOW - timedelta(hours=2)  # breach fired 2h ago, tier2 = 4h → not yet
    await audit.append(AuditEntry(
        ticket_id="302", session_id="chatwoot-conv-302",
        actor="sla-engine", from_state="OPEN", to_state=UNRESOLVED_BREACH,
        at=breach_at.isoformat(), remark="test breach",
    ))

    level2_calls: list[tuple[str, str, str]] = []
    def level2(ticket_id: str, to_state: str, remark: str) -> None:
        level2_calls.append((ticket_id, to_state, remark))

    await scan_conversations(
        settings, audit, now=_NOW, fetch=lambda _s: [conv], level2_alert=level2
    )
    assert level2_calls == []


async def test_level2_alert_does_not_fire_when_none() -> None:
    """Passing level2_alert=None is a safe no-op (backward compat)."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=48)
    conv = _conv(303, status="pending", created_at=_epoch(60))
    await audit.append(AuditEntry(
        ticket_id="303", session_id="chatwoot-conv-303",
        actor="sla-engine", from_state="OPEN", to_state=UNRESOLVED_BREACH,
        at=(_NOW - timedelta(hours=10)).isoformat(), remark="",
    ))
    # Should not raise even without level2_alert
    fired = await scan_conversations(
        settings, audit, now=_NOW, fetch=lambda _s: [conv]
    )
    assert fired == []  # no new breach
