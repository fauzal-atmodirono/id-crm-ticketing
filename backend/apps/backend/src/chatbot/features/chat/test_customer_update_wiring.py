"""The customer-update clock inside the SLA scan.

Same injected-clock/fetch/callback pattern as test_sla.py. What these assert
that test_customer_update.py cannot: that the scan fires the transition once,
dedupes on the audit trail like every other breach, and stays completely dark
with the flag off.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.customer_update import (
    CUSTOMER_UPDATE_DUE_STATE,
    CUSTOMER_UPDATE_WARNING_STATE,
)
from chatbot.features.chat.sla import scan_conversations
from chatbot.platform.config import Settings

_NOW = datetime(2026, 8, 19, 18, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        # Pushed far out so the ordinary breaches never fire and every
        # assertion below is unambiguously about the customer-update clock.
        "sla_response_hours": 1000,
        "sla_resolution_hours": 1000,
        "chatwoot_inbox_id": 1,
        "escalation_customer_update_enabled": True,
        "escalation_customer_update_hours": 4.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float) -> int:
    return int((_NOW - timedelta(hours=hours_ago)).timestamp())


def _iso(hours_ago: float) -> str:
    return (_NOW - timedelta(hours=hours_ago)).isoformat()


def _conv(conv_id: int = 501, *, replied_hours_ago: float | None = 5, **attrs: Any):
    custom: dict[str, Any] = dict(attrs)
    if replied_hours_ago is not None:
        custom["escalation_replied_at"] = _iso(replied_hours_ago)
    return {
        "id": conv_id,
        "status": "open",
        "created_at": _epoch(30),
        "first_reply_created_at": _epoch(29),
        "custom_attributes": custom,
    }


async def _scan(conv: dict[str, Any], settings: Settings, audit: InMemoryAuditLog):
    return await scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv])


async def test_an_unanswered_customer_fires_the_transition() -> None:
    audit = InMemoryAuditLog()

    fired = await _scan(_conv(), _settings(), audit)

    assert [e.to_state for e in fired] == [CUSTOMER_UPDATE_DUE_STATE]
    assert "answered" in fired[0].remark


async def test_it_fires_only_once() -> None:
    audit = InMemoryAuditLog()
    settings = _settings()
    conv = _conv()

    await _scan(conv, settings, audit)
    second = await _scan(conv, settings, audit)

    assert second == []


async def test_the_warning_fires_inside_the_window() -> None:
    audit = InMemoryAuditLog()

    fired = await _scan(_conv(replied_hours_ago=2.5), _settings(), audit)

    assert [e.to_state for e in fired] == [CUSTOMER_UPDATE_WARNING_STATE]


async def test_a_customer_who_was_updated_fires_nothing() -> None:
    audit = InMemoryAuditLog()
    conv = _conv(replied_hours_ago=9, customer_updated_at=_iso(1))

    assert await _scan(conv, _settings(), audit) == []


async def test_a_case_with_no_dealer_reply_fires_nothing() -> None:
    audit = InMemoryAuditLog()

    assert await _scan(_conv(replied_hours_ago=None), _settings(), audit) == []


async def test_the_flag_off_fires_nothing() -> None:
    audit = InMemoryAuditLog()
    settings = _settings(escalation_customer_update_enabled=False)

    assert await _scan(_conv(), settings, audit) == []


async def test_the_alert_callback_receives_the_breach() -> None:
    """The clock rides the same alert path as every other breach, so an
    operator with WhatsApp alerts on hears about it the same way."""
    audit = InMemoryAuditLog()
    calls: list[tuple[str, str, str, list[str]]] = []

    await scan_conversations(
        _settings(),
        audit,
        now=_NOW,
        fetch=lambda _s: [{**_conv(), "labels": ["dept_aftersales"]}],
        alert=lambda *args: calls.append(args),
    )

    assert len(calls) == 1
    assert calls[0][1] == CUSTOMER_UPDATE_DUE_STATE
    assert calls[0][3] == ["dept_aftersales"]
