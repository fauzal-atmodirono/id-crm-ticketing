"""The ladder sweep: one rung per pass, stamped before sent, and stopped by
anything that means the dealer has engaged."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.escalation_ladder import (
    LADDER_STEP_ATTR,
    step_sent_attr,
    sweep_ladder,
)
from chatbot.features.chat.pic_store import DealerRecord, ProtonNetRecord
from chatbot.platform.config import Settings

_NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)

_DEALER = DealerRecord(
    dealer="kl",
    contacts={
        "cre": "cre@kl.my",
        "sales_aftersales_mgr": "sam@kl.my",
        "principal": "dp@kl.my",
        "owner": "owner@kl.my",
    },
    region="central",
)
_PRONET = ProtonNetRecord(region="central", area_regional_mgr="arm@proton.my", hod="hod@proton.my")


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "escalation_policy_enabled": True,
        "escalation_policy_dry_run": False,
        "sla_working_hours_enabled": False,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def _conv(
    *,
    escalated_hours_ago: float = 5,
    step: int | None = None,
    labels: list[str] | None = None,
    status: str = "open",
    **attrs: Any,
) -> dict[str, Any]:
    custom: dict[str, Any] = {
        "escalation_notified_at": (_NOW - timedelta(hours=escalated_hours_ago)).isoformat(),
        **attrs,
    }
    if step is not None:
        custom[LADDER_STEP_ATTR] = step
    return {
        "id": 42,
        "status": status,
        "inbox_id": 4,
        "labels": labels if labels is not None else ["escalate", "dealer_kl", "dept_aftersales"],
        "custom_attributes": custom,
    }


class _Notifier:
    """Records what the sweep asked for, in call order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def send_ladder_step(self, *, conv_id, step, to, cc, title, body, elapsed_working_hours):
        self.calls.append(("send", {"step_no": step.step_no, "to": to, "cc": cc}))
        return True, ""

    async def raise_phone_task(self, *, conv_id, step, contacts, deadline):
        self.calls.append(("phone", {"step_no": step.step_no, "contacts": contacts}))
        return True


class _Store:
    def __init__(self, record: Any) -> None:
        self._record = record

    async def get(self, _key: str) -> Any:
        return self._record


def _deps(notifier: _Notifier, stamps: list[tuple[str, dict]] | None = None) -> dict[str, Any]:
    async def _set_attributes(conv_id: str, attributes: dict) -> None:
        if stamps is not None:
            stamps.append((conv_id, attributes))
        notifier.calls.append(("stamp", attributes))

    return {
        "notifier": notifier,
        "dealer_store": _Store(_DEALER),
        "pronet_store": _Store(_PRONET),
        "set_attributes": _set_attributes,
        "now": _NOW,
    }


# --- advancing --------------------------------------------------------------


async def test_a_case_past_four_working_hours_gets_the_first_reminder() -> None:
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=5, step=2)], settings=_settings(), **_deps(notifier)
    )

    assert [a["step_no"] for a in acted] == [3]
    sends = [c for c in notifier.calls if c[0] == "send"]
    assert sends[0][1]["to"] == ["dp@kl.my"]
    assert "owner@kl.my" in sends[0][1]["cc"]


async def test_one_rung_per_sweep_even_after_a_long_outage() -> None:
    """40 hours of downtime must advance the ladder by one step, not four --
    emailing a Dealer Owner about a case they were never given a chance to
    see is the exact failure the ladder exists to prevent."""
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=40, step=1)], settings=_settings(), **_deps(notifier)
    )

    assert [a["step_no"] for a in acted] == [2]


async def test_the_stamp_is_written_before_the_send() -> None:
    """A crash between the two loses a reminder; the other order sends one
    twice."""
    notifier = _Notifier()

    await sweep_ladder([_conv(step=2)], settings=_settings(), **_deps(notifier))

    kinds = [c[0] for c in notifier.calls]
    assert kinds.index("stamp") < kinds.index("send")


async def test_a_stamped_step_is_never_resent() -> None:
    notifier = _Notifier()
    conv = _conv(step=2, **{step_sent_attr(3): _NOW.isoformat()})

    acted = await sweep_ladder([conv], settings=_settings(), **_deps(notifier))

    assert acted == []
    assert notifier.calls == []


async def test_nothing_fires_before_the_delay_has_passed() -> None:
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=1, step=1)], settings=_settings(), **_deps(notifier)
    )

    assert acted == []


# --- stopping ---------------------------------------------------------------


async def test_a_dealer_reply_halts_the_ladder() -> None:
    notifier = _Notifier()
    conv = _conv(step=2, escalation_replied_at=_NOW.isoformat())

    assert await sweep_ladder([conv], settings=_settings(), **_deps(notifier)) == []


async def test_an_agent_marking_it_acknowledged_halts_the_ladder() -> None:
    notifier = _Notifier()
    conv = _conv(step=2, escalation_acknowledged_at=_NOW.isoformat())

    assert await sweep_ladder([conv], settings=_settings(), **_deps(notifier)) == []


async def test_a_resolved_case_halts_the_ladder() -> None:
    notifier = _Notifier()

    assert await sweep_ladder(
        [_conv(step=2, status="resolved")], settings=_settings(), **_deps(notifier)
    ) == []


async def test_a_case_without_the_escalate_label_is_not_a_candidate() -> None:
    notifier = _Notifier()

    assert await sweep_ladder(
        [_conv(step=2, labels=["dealer_kl"])], settings=_settings(), **_deps(notifier)
    ) == []


async def test_a_case_that_never_had_step_one_is_not_a_candidate() -> None:
    """No escalation_notified_at means EM-7 never fired. Starting the ladder
    at rung 3 would reach a Dealer Principal about a case nobody has been
    told about yet."""
    notifier = _Notifier()
    conv = {
        "id": 42,
        "status": "open",
        "labels": ["escalate", "dealer_kl"],
        "custom_attributes": {},
    }

    assert await sweep_ladder([conv], settings=_settings(), **_deps(notifier)) == []


# --- the acknowledgement window and the phone step --------------------------


async def test_the_acknowledgement_window_sends_nothing_but_still_advances() -> None:
    """Step 2 is the dealer's own 2-working-hour window, not something we
    send. Reaching it means the window closed unanswered."""
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=3, step=1)], settings=_settings(), **_deps(notifier)
    )

    assert acted[0]["action"] == "skipped"
    assert not [c for c in notifier.calls if c[0] == "send"]
    assert [c for c in notifier.calls if c[0] == "stamp"]


async def test_the_final_step_raises_a_phone_task_and_sends_no_mail() -> None:
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=9, step=4)], settings=_settings(), **_deps(notifier)
    )

    assert acted[0]["action"] == "phone_task"
    assert not [c for c in notifier.calls if c[0] == "send"]
    phone = next(c for c in notifier.calls if c[0] == "phone")
    assert phone[1]["contacts"] == ["dp@kl.my", "owner@kl.my"]


# --- missing configuration --------------------------------------------------


async def test_a_missing_owner_skips_the_rung_rather_than_mailing_the_cc_list() -> None:
    notifier = _Notifier()
    dealer = DealerRecord(
        dealer="kl",
        contacts={"cre": "cre@kl.my", "principal": "dp@kl.my"},
        region="central",
    )
    deps = _deps(notifier)
    deps["dealer_store"] = _Store(dealer)

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=9, step=3)], settings=_settings(), **deps
    )

    assert acted[0]["action"] == "skipped"
    assert not [c for c in notifier.calls if c[0] == "send"]


async def test_an_unknown_dealer_does_not_stop_the_sweep() -> None:
    """A dealer label whose record was deleted: this case is left alone (and
    the sweep carries on for everyone else). Deliberately NOT stamped as
    skipped -- if the record comes back, the ladder should resume properly
    rather than find rungs already marked sent."""
    notifier = _Notifier()
    deps = _deps(notifier)
    deps["dealer_store"] = _Store(None)

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=5, step=2), _conv(escalated_hours_ago=5, step=2)],
        settings=_settings(),
        **deps,
    )

    assert acted == []
    assert notifier.calls == []


async def test_a_failing_dealer_store_does_not_stop_the_sweep() -> None:
    class _Boom:
        async def get(self, _key: str) -> Any:
            raise RuntimeError("firestore down")

    notifier = _Notifier()
    deps = _deps(notifier)
    deps["dealer_store"] = _Boom()

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=5, step=2)], settings=_settings(), **deps
    )

    # No exception escapes, and the outage costs a delayed rung rather than a
    # rung stamped as sent to nobody.
    assert acted == []


# --- dry run ----------------------------------------------------------------


async def test_dry_run_stamps_nothing_and_sends_nothing() -> None:
    notifier = _Notifier()

    acted = await sweep_ladder(
        [_conv(step=2)],
        settings=_settings(escalation_policy_dry_run=True),
        **_deps(notifier),
    )

    assert acted[0]["action"] == "dry_run"
    assert acted[0]["to"] == ["dp@kl.my"]  # ...but it reports what it would have done
    assert notifier.calls == []


def test_dry_run_is_the_default_for_a_newly_enabled_ladder() -> None:
    """Asserted against the field default rather than a constructed Settings:
    an ESCALATION_POLICY_DRY_RUN in the ambient environment would otherwise
    decide the result, and the claim here is precisely about what an operator
    gets when they set nothing."""
    assert Settings.model_fields["escalation_policy_dry_run"].default is True


# --- working hours ----------------------------------------------------------


async def test_a_friday_evening_escalation_does_not_remind_on_saturday() -> None:
    notifier = _Notifier()
    friday_1600 = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)  # a Friday
    saturday_1000 = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)

    class _WeekdayInboxes:
        async def get(self, _inbox_id: Any) -> dict[str, Any]:
            return {
                "working_hours_enabled": True,
                "timezone": "UTC",
                "working_hours": [
                    {
                        "day_of_week": d,
                        "open_hour": 9,
                        "open_minutes": 0,
                        "close_hour": 17,
                        "close_minutes": 0,
                        "closed_all_day": d in (0, 6),  # Sunday, Saturday
                    }
                    for d in range(7)
                ],
            }

    conv = {
        "id": 42,
        "status": "open",
        "inbox_id": 4,
        "labels": ["escalate", "dealer_kl"],
        "custom_attributes": {
            "escalation_notified_at": friday_1600.isoformat(),
            LADDER_STEP_ATTR: 2,
        },
    }
    deps = _deps(notifier)
    deps["now"] = saturday_1000
    deps["inbox_cache"] = _WeekdayInboxes()

    acted = await sweep_ladder(
        [conv], settings=_settings(sla_working_hours_enabled=True), **deps
    )

    # 1 working hour elapsed (16:00-17:00 Friday), not 18 wall-clock hours.
    assert acted == []


async def test_a_case_with_no_dealer_is_not_climbed_at_all() -> None:
    """The ladder IS the dealer escalation policy. A case escalated to a
    department PIC with no dealer label has nobody to climb to -- and without
    this guard it walked silently to step 5 and raised a 'call the Dealer
    Principal' task for a dealer that does not exist.

    Found live on proton 2026-08-19: two real cases were sitting in exactly
    this state when the sweep was first armed.
    """
    notifier = _Notifier()
    conv = _conv(escalated_hours_ago=40, step=1, labels=["escalate", "dept_aftersales"])

    acted = await sweep_ladder([conv], settings=_settings(), **_deps(notifier))

    assert acted == []
    assert notifier.calls == []


async def test_an_unknown_dealer_record_is_not_climbed_either() -> None:
    """Same for a dealer label whose record was deleted, or a store outage:
    resume when it resolves rather than stamping rungs nobody received."""
    notifier = _Notifier()
    deps = _deps(notifier)
    deps["dealer_store"] = _Store(None)

    acted = await sweep_ladder(
        [_conv(escalated_hours_ago=40, step=1)], settings=_settings(), **deps
    )

    assert acted == []
