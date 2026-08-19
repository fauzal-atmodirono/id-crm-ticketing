"""The operator-editable ladder policy: precedence, the sweep honouring it,
and the in-flight view agreeing with the engine."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from chatbot.features.chat.escalation_ladder import (
    _startup_interval,
    describe_in_flight,
    sweep_ladder,
)
from chatbot.features.chat.ladder_policy_db import (
    LadderPolicyValues,
    build_engine,
    build_session_maker,
    init_ladder_policy_db,
)
from chatbot.features.chat.ladder_policy_repository import (
    LadderPolicyRepository,
    resolve_ladder_config,
)
from chatbot.features.chat.pic_store import DealerRecord
from chatbot.platform.config import Settings

_NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)

_DEALER = DealerRecord(
    dealer="kl",
    contacts={"cre": "cre@kl.my", "principal": "dp@kl.my", "owner": "owner@kl.my"},
    region="central",
)


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "escalation_policy_enabled": False,
        "escalation_policy_dry_run": True,
        "escalation_policy_scan_interval_seconds": 300,
        "sla_working_hours_enabled": False,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/ladder.db")
    await init_ladder_policy_db(engine)
    return LadderPolicyRepository(build_session_maker(engine))


def _conv(**attrs: Any) -> dict[str, Any]:
    custom = {"escalation_notified_at": (_NOW - timedelta(hours=5)).isoformat(), **attrs}
    return {
        "id": 42,
        "status": "open",
        "inbox_id": 4,
        "labels": ["escalate", "dealer_kl"],
        "custom_attributes": custom,
    }


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def send_ladder_step(self, **kw: Any):
        self.calls.append(("send", kw["step"].step_no))
        return True, ""

    async def raise_phone_task(self, **kw: Any):
        self.calls.append(("phone", kw["step"].step_no))
        return True


class _Store:
    def __init__(self, record: Any = _DEALER) -> None:
        self._record = record

    async def get(self, _key: str) -> Any:
        return self._record


def _deps(notifier: _Notifier) -> dict[str, Any]:
    async def _set_attributes(_conv_id: str, _attributes: dict) -> None:
        return None

    return {
        "notifier": notifier,
        "dealer_store": _Store(),
        "pronet_store": _Store(None),
        "set_attributes": _set_attributes,
        "now": _NOW,
    }


# --- precedence -------------------------------------------------------------


async def test_an_empty_store_inherits_every_env_value(repo) -> None:
    """A tenant that never opens the page must behave exactly as before."""
    config = await repo.resolve(_settings(escalation_policy_enabled=True))

    assert config.enabled is True
    assert config.dry_run is True
    assert config.scan_interval_seconds == 300
    assert config.delay_overrides == {}
    assert config.from_store is False


async def test_a_stored_value_beats_env(repo) -> None:
    await repo.upsert(LadderPolicyValues(enabled=True, dry_run=False, scan_interval_seconds=60))

    config = await repo.resolve(_settings(escalation_policy_enabled=False))

    assert config.enabled is True and config.dry_run is False
    assert config.scan_interval_seconds == 60
    assert config.from_store is True


async def test_stored_false_is_honoured_not_treated_as_unset(repo) -> None:
    """The bug this guards: `if stored.enabled:` would read a deliberate
    False as 'not configured' and silently fall back to an env true."""
    await repo.upsert(LadderPolicyValues(enabled=False))

    assert (await repo.resolve(_settings(escalation_policy_enabled=True))).enabled is False


async def test_clearing_a_field_returns_it_to_env(repo) -> None:
    await repo.upsert(LadderPolicyValues(enabled=True, step3_hours=1.0))
    await repo.upsert(LadderPolicyValues(enabled=True))

    config = await repo.resolve(_settings())

    assert config.enabled is True
    assert config.delay_overrides == {}


async def test_the_row_is_a_singleton(repo) -> None:
    await repo.upsert(LadderPolicyValues(scan_interval_seconds=60))
    await repo.upsert(LadderPolicyValues(scan_interval_seconds=120))

    assert (await repo.get()).scan_interval_seconds == 120


async def test_no_repository_at_all_resolves_to_env() -> None:
    config = await resolve_ladder_config(None, _settings(escalation_policy_enabled=True))

    assert config.enabled is True and config.dry_run is True


# --- the sweep honours it ---------------------------------------------------


async def test_the_store_can_turn_the_ladder_on_without_touching_env(repo) -> None:
    """The whole point of the page: env says off, the operator says on."""
    await repo.upsert(LadderPolicyValues(enabled=True, dry_run=False))
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalation_step=2)],
        settings=_settings(escalation_policy_enabled=False),
        policy_repo=repo,
        **_deps(notifier),
    )

    assert [a["step_no"] for a in acted] == [3]


async def test_the_store_can_turn_it_off_while_env_says_on(repo) -> None:
    await repo.upsert(LadderPolicyValues(enabled=False))
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalation_step=2)],
        settings=_settings(escalation_policy_enabled=True, escalation_policy_dry_run=False),
        policy_repo=repo,
        **_deps(notifier),
    )

    assert acted == []
    assert notifier.calls == []


async def test_a_retuned_timer_changes_when_a_rung_is_due(repo) -> None:
    """5 working hours elapsed: with the SOP's 4h step 3 is due; push it to
    9h from the page and it is not."""
    await repo.upsert(LadderPolicyValues(enabled=True, dry_run=False, step3_hours=9.0))
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalation_step=2)], settings=_settings(), policy_repo=repo, **_deps(notifier)
    )

    assert acted == []


async def test_dry_run_from_the_store_sends_nothing(repo) -> None:
    await repo.upsert(LadderPolicyValues(enabled=True, dry_run=True))
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalation_step=2)],
        settings=_settings(escalation_policy_dry_run=False),
        policy_repo=repo,
        **_deps(notifier),
    )

    assert acted[0]["action"] == "dry_run"
    assert notifier.calls == []


# --- the in-flight view -----------------------------------------------------


async def test_in_flight_reports_a_climbing_case_and_its_next_rung(repo) -> None:
    await repo.upsert(LadderPolicyValues(enabled=True))

    rows = await describe_in_flight(
        [_conv(escalation_step=2)],
        settings=_settings(),
        dealer_store=_Store(),
        policy_repo=repo,
        now=_NOW,
    )

    assert rows[0]["state"] == "climbing"
    assert rows[0]["rung"] == 2
    assert rows[0]["next_step_no"] == 3
    assert rows[0]["next_due_in_working_hours"] == 0.0  # 5h elapsed, due at 4h


async def test_in_flight_gives_the_same_reasons_the_sweep_acts_on(repo) -> None:
    """A panel that disagreed with the engine would be worse than no panel."""
    replied = _conv(escalation_replied_at=_NOW.isoformat())
    resolved = {**_conv(), "status": "resolved"}
    no_dealer = {**_conv(), "labels": ["escalate", "dept_aftersales"]}

    rows = await describe_in_flight(
        [replied, resolved, no_dealer],
        settings=_settings(),
        dealer_store=_Store(),
        policy_repo=repo,
        now=_NOW,
    )

    assert [r["state"] for r in rows] == ["dealer_replied", "resolved", "no_dealer"]


async def test_in_flight_flags_a_dealer_record_that_is_missing(repo) -> None:
    rows = await describe_in_flight(
        [_conv()], settings=_settings(), dealer_store=_Store(None), policy_repo=repo, now=_NOW
    )

    assert rows[0]["state"] == "no_dealer"


async def test_in_flight_ignores_cases_that_were_never_escalated(repo) -> None:
    ordinary = {"id": 9, "status": "open", "labels": ["dept_sales"], "custom_attributes": {}}

    assert await describe_in_flight([ordinary], settings=_settings(), policy_repo=repo) == []


# --- the sweep tick ---------------------------------------------------------


def test_the_startup_interval_prefers_the_stored_value(repo) -> None:
    """APScheduler fixes an interval when the job is added, so this is the one
    ladder setting that cannot be resolved per sweep. It must at least come
    from the store rather than only from env."""
    asyncio.run(repo.upsert(LadderPolicyValues(scan_interval_seconds=45)))

    assert _startup_interval(_settings(escalation_policy_scan_interval_seconds=300), repo) == 45


def test_the_startup_interval_falls_back_to_env_when_the_store_is_unreachable() -> None:
    class _Boom:
        async def resolve(self, _settings):
            raise RuntimeError("postgres down")

    settings = _settings(escalation_policy_scan_interval_seconds=120)

    # An unreachable store must not stop the ladder being scheduled at all.
    assert _startup_interval(settings, _Boom()) == 120


def test_no_store_at_all_uses_the_env_interval() -> None:
    assert _startup_interval(_settings(escalation_policy_scan_interval_seconds=90), None) == 90
