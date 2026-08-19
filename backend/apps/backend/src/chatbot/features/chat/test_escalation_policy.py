"""The escalation ladder's step table: who each rung addresses, and which
rung is due."""

from __future__ import annotations

import json

from chatbot.features.chat.escalation_policy import (
    DEFAULT_STEPS,
    PHONE,
    due_step,
    load_steps,
    resolve_recipients,
    step_by_no,
)
from chatbot.features.chat.pic_store import DealerRecord, ProtonNetRecord


def _dealer(**contacts: str) -> DealerRecord:
    base = {
        "cre": "cre@kl.my",
        "sales_aftersales_mgr": "sam@kl.my",
        "principal": "dp@kl.my",
        "owner": "owner@kl.my",
    }
    base.update(contacts)
    return DealerRecord(
        dealer="kl",
        contacts={k: v for k, v in base.items() if v},
        region="central",
    )


_PRONET = ProtonNetRecord(region="central", area_regional_mgr="arm@proton.my", hod="hod@proton.my")


# --- the table itself -------------------------------------------------------


def test_the_default_table_is_the_sop_matrix() -> None:
    assert [s.step_no for s in DEFAULT_STEPS] == [1, 2, 3, 4, 5]
    assert [s.delay_working_hours for s in DEFAULT_STEPS] == [0.0, 2.0, 4.0, 8.0, 8.0]
    assert step_by_no(DEFAULT_STEPS, 3).to_roles == ("principal",)
    assert step_by_no(DEFAULT_STEPS, 4).to_roles == ("owner",)
    assert step_by_no(DEFAULT_STEPS, 5).channel == PHONE


def test_step_two_sends_nothing_it_is_the_dealers_own_window() -> None:
    step2 = step_by_no(DEFAULT_STEPS, 2)

    assert step2.to_roles == () and step2.cc_roles == ()


# --- recipient resolution ---------------------------------------------------


def test_step_one_reaches_the_desk_and_ccs_the_principal_and_pronet() -> None:
    to, cc = resolve_recipients(step_by_no(DEFAULT_STEPS, 1), _dealer(), _PRONET)

    assert to == ["cre@kl.my", "sam@kl.my"]
    assert cc == ["dp@kl.my", "arm@proton.my", "hod@proton.my"]


def test_each_rung_reaches_someone_more_senior() -> None:
    to3, _ = resolve_recipients(step_by_no(DEFAULT_STEPS, 3), _dealer(), _PRONET)
    to4, _ = resolve_recipients(step_by_no(DEFAULT_STEPS, 4), _dealer(), _PRONET)

    assert to3 == ["dp@kl.my"]
    assert to4 == ["owner@kl.my"]


def test_a_missing_role_skips_and_is_never_filled_from_the_cc_list() -> None:
    """An empty To means 'skip this step'. Promoting a CC would send a
    '2ND REMINDER, respond immediately' to the service desk that has been
    reading the thread all along."""
    to, cc = resolve_recipients(step_by_no(DEFAULT_STEPS, 4), _dealer(owner=""), _PRONET)

    assert to == []
    assert "owner@kl.my" not in cc
    assert cc  # the CC list is still resolved; only the To is empty


def test_an_address_is_never_both_to_and_cc() -> None:
    """One dealer, one mailbox for two roles: they get the mail once, in the
    To line, not twice."""
    dealer = _dealer(principal="dp@kl.my", owner="dp@kl.my")

    to, cc = resolve_recipients(step_by_no(DEFAULT_STEPS, 4), dealer, _PRONET)

    assert to == ["dp@kl.my"]
    assert "dp@kl.my" not in cc


def test_no_dealer_record_at_all_resolves_to_nothing_rather_than_raising() -> None:
    to, cc = resolve_recipients(step_by_no(DEFAULT_STEPS, 3), None, None)

    assert to == [] and cc == []


def test_a_legacy_group_dealer_still_gets_step_one() -> None:
    """The live proton config predates roles: one group, no contacts map. The
    ladder must not break it -- step 1 goes exactly where it goes today."""
    legacy = DealerRecord(dealer="kl", emails=["desk@kl.my"])

    to, _ = resolve_recipients(step_by_no(DEFAULT_STEPS, 1), legacy, None)

    assert to == ["desk@kl.my"]


def test_a_legacy_group_dealer_skips_the_senior_rungs() -> None:
    legacy = DealerRecord(dealer="kl", emails=["desk@kl.my"])

    assert resolve_recipients(step_by_no(DEFAULT_STEPS, 3), legacy, None)[0] == []
    assert resolve_recipients(step_by_no(DEFAULT_STEPS, 4), legacy, None)[0] == []


# --- which rung is due ------------------------------------------------------


def test_only_the_next_rung_is_ever_due() -> None:
    """A 24-hour outage advances the ladder one rung, not four."""
    assert due_step(DEFAULT_STEPS, 40.0, current_step=1).step_no == 2
    assert due_step(DEFAULT_STEPS, 40.0, current_step=2).step_no == 3
    assert due_step(DEFAULT_STEPS, 40.0, current_step=4).step_no == 5


def test_nothing_is_due_before_its_delay() -> None:
    assert due_step(DEFAULT_STEPS, 3.9, current_step=2) is None
    assert due_step(DEFAULT_STEPS, 4.0, current_step=2).step_no == 3


def test_the_top_of_the_ladder_stays_the_top() -> None:
    assert due_step(DEFAULT_STEPS, 999.0, current_step=5) is None


# --- operator overrides -----------------------------------------------------


def test_an_operator_can_retune_the_timers() -> None:
    raw = json.dumps(
        [
            {"step_no": 1, "delay_working_hours": 0, "to_roles": ["cre"], "cc_roles": []},
            {
                "step_no": 2,
                "delay_working_hours": 1,
                "to_roles": ["principal"],
                "cc_roles": ["hod"],
                "label": "REMINDER",
            },
        ]
    )

    steps = load_steps(raw)

    assert [s.delay_working_hours for s in steps] == [0.0, 1.0]
    assert steps[1].to_roles == ("principal",)


def test_malformed_json_falls_back_to_the_whole_default_table() -> None:
    """A half-parsed ladder silently drops a rung -- and the rung most likely
    to be dropped is the one the operator was editing."""
    assert load_steps("{not json") == DEFAULT_STEPS
    assert load_steps("[]") == DEFAULT_STEPS
    assert load_steps('{"step_no": 1}') == DEFAULT_STEPS
    assert load_steps("") == DEFAULT_STEPS


def test_an_unknown_role_rejects_the_whole_table() -> None:
    raw = json.dumps(
        [{"step_no": 1, "delay_working_hours": 0, "to_roles": ["dealer_ceo"], "cc_roles": []}]
    )

    assert load_steps(raw) == DEFAULT_STEPS


def test_an_unknown_channel_rejects_the_whole_table() -> None:
    raw = json.dumps(
        [{"step_no": 1, "delay_working_hours": 0, "to_roles": ["cre"], "channel": "carrier-pigeon"}]
    )

    assert load_steps(raw) == DEFAULT_STEPS


def test_steps_are_sorted_however_the_operator_wrote_them() -> None:
    raw = json.dumps(
        [
            {"step_no": 3, "delay_working_hours": 4, "to_roles": ["principal"]},
            {"step_no": 1, "delay_working_hours": 0, "to_roles": ["cre"]},
        ]
    )

    assert [s.step_no for s in load_steps(raw)] == [1, 3]
