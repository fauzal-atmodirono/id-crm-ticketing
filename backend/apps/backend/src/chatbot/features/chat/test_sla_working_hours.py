"""P1 task 3 — SLA enforcement on the working-hours clock.

The golden case, asserted in both directions: a complaint arriving 18:00 Friday
against a 2-working-hour target does NOT breach on Friday evening, and DOES
breach on Monday morning once two hours of working time have actually elapsed.
Today's engine breaches it on Friday evening and pages a PIC at home.

Every test here runs the flag both ways where the distinction matters, because
the ship-dark guarantee is that `sla_working_hours_enabled=False` reproduces
today's behaviour exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.sla import NO_RESPONSE_BREACH, UNRESOLVED_BREACH, scan_conversations
from chatbot.features.chat.sla_clock import InboxCache
from chatbot.platform.config import Settings

# Mon-Fri 09:00-18:00 UTC, weekend closed.
INBOX_9_TO_6 = {
    "working_hours_enabled": True,
    "timezone": "UTC",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18,
         "close_minutes": 0, "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)
    ] + [
        {"day_of_week": d, "closed_all_day": True} for d in (0, 6)
    ],
}

NO_HOURS = {"working_hours_enabled": False}

FRIDAY_1800 = datetime(2026, 7, 3, 18, 0, tzinfo=UTC)
# Deliberately past the threshold, not exactly on it: the engine compares
# `age > threshold` strictly, so a fixture sitting exactly on the boundary
# tests nothing.
FRIDAY_2030 = datetime(2026, 7, 3, 20, 30, tzinfo=UTC)
# Fri 18:00 is the close, so zero working minutes accrue on Friday. Two
# working hours therefore land at 11:00 Monday; 11:30 clears it.
MONDAY_1130 = datetime(2026, 7, 6, 11, 30, tzinfo=UTC)


class _FakeLog:
    """Stands in for the ConversationLogPort's inbox fetch."""

    def __init__(self, inbox: Any = INBOX_9_TO_6, raises: bool = False):
        self._inbox = inbox
        self._raises = raises
        self.calls: list[Any] = []

    async def get_inbox_working_hours(self, inbox_id):
        self.calls.append(inbox_id)
        if self._raises:
            raise RuntimeError("chatwoot down")
        return self._inbox


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 2,
        "sla_resolution_hours": 48,
        "sla_scan_interval_minutes": 15,
        "chatwoot_inbox_id": 1,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _conv(created: datetime, conv_id: int = 1, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": conv_id,
        "created_at": int(created.timestamp()),
        "status": "open",
        "inbox_id": 7,
    }
    base.update(fields)
    return base


async def _scan(*, now, convs, settings, log=None, cache=None):
    audit = InMemoryAuditLog()
    fired = await scan_conversations(
        settings,
        audit,
        now=now,
        fetch=lambda _s: convs,
        inbox_cache=cache if cache is not None else InboxCache(log or _FakeLog()),
    )
    return fired, audit


def _states(fired):
    return {e.to_state for e in fired}


# --- the golden case ------------------------------------------------------


async def test_a_friday_1800_arrival_does_not_breach_a_2_working_hour_target_on_friday_evening():
    fired, _ = await _scan(
        now=FRIDAY_2030,
        convs=[_conv(FRIDAY_1800)],
        settings=_settings(sla_working_hours_enabled=True),
    )
    assert NO_RESPONSE_BREACH not in _states(fired)


async def test_a_friday_1800_arrival_breaches_that_target_on_monday_morning():
    fired, _ = await _scan(
        now=MONDAY_1130,
        convs=[_conv(FRIDAY_1800)],
        settings=_settings(sla_working_hours_enabled=True),
    )
    assert NO_RESPONSE_BREACH in _states(fired)


async def test_the_same_friday_case_breaches_on_friday_evening_with_the_flag_off():
    """The behaviour being replaced — kept so the change is visible, not implied."""
    fired, _ = await _scan(
        now=FRIDAY_2030,
        convs=[_conv(FRIDAY_1800)],
        settings=_settings(sla_working_hours_enabled=False),
    )
    assert NO_RESPONSE_BREACH in _states(fired)


async def test_a_weekend_spanning_case_accrues_zero_working_minutes():
    saturday = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    sunday = datetime(2026, 7, 5, 23, 0, tzinfo=UTC)
    fired, _ = await _scan(
        now=sunday,
        convs=[_conv(saturday)],
        settings=_settings(sla_working_hours_enabled=True),
    )
    assert NO_RESPONSE_BREACH not in _states(fired)


# --- equivalence / degradation -------------------------------------------


async def test_an_inbox_with_no_working_hours_behaves_identically_flag_on_or_off():
    convs = [_conv(FRIDAY_1800)]
    log = _FakeLog(inbox=NO_HOURS)

    on, _ = await _scan(
        now=FRIDAY_2030, convs=convs,
        settings=_settings(sla_working_hours_enabled=True), log=log,
    )
    off, _ = await _scan(
        now=FRIDAY_2030, convs=convs,
        settings=_settings(sla_working_hours_enabled=False), log=_FakeLog(inbox=NO_HOURS),
    )
    assert _states(on) == _states(off)


async def test_an_inbox_fetch_failure_falls_back_to_wall_clock_and_does_not_raise():
    fired, _ = await _scan(
        now=FRIDAY_2030,
        convs=[_conv(FRIDAY_1800)],
        settings=_settings(sla_working_hours_enabled=True),
        log=_FakeLog(raises=True),
    )
    # Degrades to today's behaviour rather than to "nothing ever breaches".
    assert NO_RESPONSE_BREACH in _states(fired)


async def test_the_inbox_is_fetched_once_per_scan_not_once_per_conversation():
    log = _FakeLog()
    convs = [_conv(FRIDAY_1800, conv_id=i) for i in range(1, 26)]
    await _scan(
        now=MONDAY_1130, convs=convs,
        settings=_settings(sla_working_hours_enabled=True), log=log,
    )
    assert log.calls == [7], f"expected one fetch for inbox 7, got {log.calls}"


async def test_the_flag_off_makes_no_inbox_api_call_at_all():
    log = _FakeLog()
    await _scan(
        now=FRIDAY_2030,
        convs=[_conv(FRIDAY_1800)],
        settings=_settings(sla_working_hours_enabled=False),
        log=log,
    )
    assert log.calls == []


# --- the other thresholds use the same clock ------------------------------


async def test_per_channel_ack_minutes_are_interpreted_as_working_minutes_when_enabled():
    settings = _settings(
        sla_working_hours_enabled=True,
        sla_ack_minutes_by_channel_json='{"Channel::Api": 120}',
    )
    conv = _conv(FRIDAY_1800, channel="Channel::Api")

    at_friday_2000, _ = await _scan(now=FRIDAY_2030, convs=[conv], settings=settings)
    at_monday, _ = await _scan(now=MONDAY_1130, convs=[conv], settings=settings)

    assert NO_RESPONSE_BREACH not in _states(at_friday_2000)
    assert NO_RESPONSE_BREACH in _states(at_monday)


async def test_a_per_conversation_sla_minutes_label_is_interpreted_as_working_minutes():
    settings = _settings(sla_working_hours_enabled=True)
    conv = _conv(FRIDAY_1800, status="open", labels=["sla_120"])

    at_friday_2000, _ = await _scan(now=FRIDAY_2030, convs=[conv], settings=settings)
    at_monday, _ = await _scan(now=MONDAY_1130, convs=[conv], settings=settings)

    assert UNRESOLVED_BREACH not in _states(at_friday_2000)
    assert UNRESOLVED_BREACH in _states(at_monday)


async def test_the_resolution_threshold_uses_the_same_clock():
    settings = _settings(sla_working_hours_enabled=True, sla_resolution_hours=4)
    conv = _conv(FRIDAY_1800)

    at_friday_2000, _ = await _scan(now=FRIDAY_2030, convs=[conv], settings=settings)
    at_monday_late, _ = await _scan(
        now=MONDAY_1130 + timedelta(hours=4), convs=[conv], settings=settings
    )

    assert UNRESOLVED_BREACH not in _states(at_friday_2000)
    assert UNRESOLVED_BREACH in _states(at_monday_late)


async def test_no_inbox_cache_supplied_still_scans_without_raising():
    """Callers are not required to pass one; None means construct per scan."""
    audit = InMemoryAuditLog()
    fired = await scan_conversations(
        _settings(sla_working_hours_enabled=False),
        audit,
        now=FRIDAY_2030,
        fetch=lambda _s: [_conv(FRIDAY_1800)],
    )
    assert NO_RESPONSE_BREACH in _states(fired)


# --- P1 task 4: the per-inbox override reaches the engine -----------------


class _StubPolicyRepo:
    def __init__(self, values):
        self._values = values

    async def resolve(self, inbox_id):  # noqa: ARG002
        return self._values


async def test_a_per_inbox_opt_in_beats_a_global_switch_that_is_off():
    from chatbot.features.chat.sla_policy_db import SlaPolicyValues

    audit = InMemoryAuditLog()
    fired = await scan_conversations(
        _settings(sla_working_hours_enabled=False),
        audit,
        now=FRIDAY_2030,
        fetch=lambda _s: [_conv(FRIDAY_1800)],
        inbox_cache=InboxCache(_FakeLog()),
        policy_repo=_StubPolicyRepo(SlaPolicyValues(working_hours_enabled=True)),
    )
    assert NO_RESPONSE_BREACH not in _states(fired)


async def test_a_per_inbox_opt_out_beats_a_global_switch_that_is_on():
    from chatbot.features.chat.sla_policy_db import SlaPolicyValues

    audit = InMemoryAuditLog()
    fired = await scan_conversations(
        _settings(sla_working_hours_enabled=True),
        audit,
        now=FRIDAY_2030,
        fetch=lambda _s: [_conv(FRIDAY_1800)],
        inbox_cache=InboxCache(_FakeLog()),
        policy_repo=_StubPolicyRepo(SlaPolicyValues(working_hours_enabled=False)),
    )
    assert NO_RESPONSE_BREACH in _states(fired)


async def test_an_unset_override_inherits_the_global_switch():
    from chatbot.features.chat.sla_policy_db import SlaPolicyValues

    audit = InMemoryAuditLog()
    fired = await scan_conversations(
        _settings(sla_working_hours_enabled=True),
        audit,
        now=FRIDAY_2030,
        fetch=lambda _s: [_conv(FRIDAY_1800)],
        inbox_cache=InboxCache(_FakeLog()),
        policy_repo=_StubPolicyRepo(SlaPolicyValues(response_hours=2)),
    )
    assert NO_RESPONSE_BREACH not in _states(fired)
