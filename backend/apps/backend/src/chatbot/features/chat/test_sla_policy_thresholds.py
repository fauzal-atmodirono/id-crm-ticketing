"""tier2_hours / reminder_warning_minutes are operator-editable, env-backed.

Mirrors the response_hours store->env resolution chain exactly: a policy
value of None means "inherit the settings.* value" at every layer (dataclass
default, repository round-trip, and the scan_conversations resolution used
by the live SLA engine).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.sla import (
    REMINDER_WARNING_STATE,
    UNRESOLVED_BREACH,
    scan_conversations,
)
from chatbot.features.chat.sla_policy_db import (
    SlaPolicyValues,
    build_engine,
    build_session_maker,
    init_sla_policy_db,
)
from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository
from chatbot.platform.config import Settings

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def test_values_carry_the_two_new_fields():
    v = SlaPolicyValues(tier2_hours=6.0, reminder_warning_minutes=90.0)
    assert v.tier2_hours == 6.0
    assert v.reminder_warning_minutes == 90.0


def test_fields_default_to_none_meaning_inherit_env():
    v = SlaPolicyValues()
    assert v.tier2_hours is None
    assert v.reminder_warning_minutes is None


# --- Repository round-trip (mirrors test_sla_policy_repository.py) ----------


async def test_repository_upsert_and_resolve_roundtrip_new_fields(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/sla_policy_thresholds.db")
    await init_sla_policy_db(engine)
    repo = SlaPolicyRepository(build_session_maker(engine))

    await repo.upsert_tenant_default(tier2_hours=6.0, reminder_warning_minutes=90.0)
    default_values = await repo.get_tenant_default()
    assert default_values.tier2_hours == 6.0
    assert default_values.reminder_warning_minutes == 90.0

    # Inbox-specific row only overrides tier2_hours; reminder_warning_minutes
    # must fall through to the tenant default (field-by-field merge).
    await repo.upsert_for_inbox(42, tier2_hours=2.0)
    resolved = await repo.resolve(42)
    assert resolved.tier2_hours == 2.0
    assert resolved.reminder_warning_minutes == 90.0


# --- sla.py resolution chain (mirrors the response_hours tests in test_sla.py) --


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 1000,
        "sla_resolution_hours": 48,
        "sla_scan_interval_minutes": 15,
        "chatwoot_inbox_id": 1,
        "escalation_tier2_hours": 4.0,
        "tasks_reminder_warning_minutes": 5,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float, now: datetime = _NOW) -> int:
    return int((now - timedelta(hours=hours_ago)).timestamp())


def _conv(conv_id: int, **fields: Any) -> dict[str, Any]:
    conv: dict[str, Any] = {"id": conv_id, "status": "open"}
    conv.update(fields)
    return conv


class _FakePolicyRepo:
    """Lightweight in-test double for SlaPolicyRepository — no DB involved."""

    def __init__(self, values: SlaPolicyValues | None) -> None:
        self._values = values

    async def resolve(self, inbox_id: int | None) -> SlaPolicyValues | None:
        del inbox_id
        return self._values


def test_policy_store_tier2_hours_overrides_env() -> None:
    """A store tier2_hours must be used ahead of the (much larger) env default:
    an UNRESOLVED_BREACH that is only 2h old re-alerts because the store sets
    tier2_hours=1.0, even though settings.escalation_tier2_hours=4.0 would not
    yet have fired."""
    audit = InMemoryAuditLog()
    settings = _settings(escalation_tier2_hours=4.0)
    conv = _conv(2001, status="pending", created_at=_epoch(60), inbox_id=5)
    breach_at = _NOW - timedelta(hours=2)
    asyncio.run(
        audit.append(
            AuditEntry(
                ticket_id="2001",
                session_id="chatwoot-conv-2001",
                actor="sla-engine",
                from_state="OPEN",
                to_state=UNRESOLVED_BREACH,
                at=breach_at.isoformat(),
                remark="test breach",
            )
        )
    )
    policy_repo = _FakePolicyRepo(SlaPolicyValues(tier2_hours=1.0))
    level2_calls: list[str] = []

    def level2(ticket_id: str, _to_state: str, _remark: str) -> None:
        level2_calls.append(ticket_id)

    asyncio.run(
        scan_conversations(
            settings,
            audit,
            now=_NOW,
            fetch=lambda _s: [conv],
            level2_alert=level2,
            policy_repo=policy_repo,
        )
    )
    assert level2_calls == ["2001"]


def test_policy_store_unset_tier2_hours_falls_back_to_env() -> None:
    """An all-None policy (store row exists but tier2_hours unset) must behave
    identically to the no-policy-repo case: settings.escalation_tier2_hours
    still governs the re-alert timing."""
    audit = InMemoryAuditLog()
    settings = _settings(escalation_tier2_hours=4.0)
    conv = _conv(2002, status="pending", created_at=_epoch(60), inbox_id=5)
    breach_at = _NOW - timedelta(hours=2)  # younger than env's 4h tier2 threshold

    def make_audit() -> InMemoryAuditLog:
        a = InMemoryAuditLog()
        asyncio.run(
            a.append(
                AuditEntry(
                    ticket_id="2002",
                    session_id="chatwoot-conv-2002",
                    actor="sla-engine",
                    from_state="OPEN",
                    to_state=UNRESOLVED_BREACH,
                    at=breach_at.isoformat(),
                    remark="test breach",
                )
            )
        )
        return a

    level2_calls: list[str] = []

    def level2(ticket_id: str, _to_state: str, _remark: str) -> None:
        level2_calls.append(ticket_id)

    asyncio.run(
        scan_conversations(
            settings,
            make_audit(),
            now=_NOW,
            fetch=lambda _s: [conv],
            level2_alert=level2,
            policy_repo=_FakePolicyRepo(SlaPolicyValues()),
        )
    )
    assert level2_calls == [], "2h-old breach must not re-alert yet at env's 4h threshold"

    asyncio.run(
        scan_conversations(
            settings,
            make_audit(),
            now=_NOW,
            fetch=lambda _s: [conv],
            level2_alert=level2,
            policy_repo=None,
        )
    )
    assert level2_calls == [], "must match the policy_repo=None baseline exactly"


def test_policy_store_reminder_warning_minutes_overrides_env() -> None:
    """A store reminder_warning_minutes must be used ahead of env: the env value
    (5 min) is too small to catch a 1h-remaining conversation, but the store's
    120 min does."""
    audit = InMemoryAuditLog()
    settings = _settings(
        sla_response_hours=1000,
        sla_resolution_hours=48,
        tasks_reminder_whatsapp_enabled=True,
        tasks_reminder_warning_minutes=5,
    )
    # 47h old -> resolution_threshold=172800s; age=169200s; remaining=3600s (1h)
    conv = _conv(2003, status="open", created_at=_epoch(47), inbox_id=5)
    policy_repo = _FakePolicyRepo(SlaPolicyValues(reminder_warning_minutes=120.0))

    sent: list[str] = []

    async def fake_alert(ticket_id: str, to_state: str, remark: str, labels: list[str]) -> None:  # noqa: ARG001
        sent.append(to_state)

    asyncio.run(
        scan_conversations(
            settings,
            audit,
            now=_NOW,
            fetch=lambda _s: [conv],
            alert=fake_alert,
            policy_repo=policy_repo,
        )
    )
    assert REMINDER_WARNING_STATE in sent


def test_policy_store_unset_reminder_warning_minutes_falls_back_to_env() -> None:
    """An all-None policy row must leave tasks_reminder_warning_minutes governed
    entirely by env — a 1h-remaining conversation does NOT fire when env's
    warning window (5 min) is smaller than the remaining time."""
    audit = InMemoryAuditLog()
    settings = _settings(
        sla_response_hours=1000,
        sla_resolution_hours=48,
        tasks_reminder_whatsapp_enabled=True,
        tasks_reminder_warning_minutes=5,
    )
    conv = _conv(2004, status="open", created_at=_epoch(47), inbox_id=5)
    policy_repo = _FakePolicyRepo(SlaPolicyValues())

    sent: list[str] = []

    async def fake_alert(ticket_id: str, to_state: str, remark: str, labels: list[str]) -> None:  # noqa: ARG001
        sent.append(to_state)

    asyncio.run(
        scan_conversations(
            settings,
            audit,
            now=_NOW,
            fetch=lambda _s: [conv],
            alert=fake_alert,
            policy_repo=policy_repo,
        )
    )
    assert REMINDER_WARNING_STATE not in sent
