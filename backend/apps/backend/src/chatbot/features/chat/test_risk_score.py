"""P2 task 10 — a case risk score an operator can argue with.

§2.1.3 asks for risk-based prioritisation. This is a weighted sum, deliberately:
an operator must be able to be told *why* a case scored 82, and there is no
labelled outcome data here to train anything else on. `contributions()` is the
design commitment, not a debugging aid.
"""

from __future__ import annotations

from chatbot.features.chat.risk_score import (
    DEFAULT_WEIGHTS,
    RiskSignals,
    contributions,
    score,
)


def test_a_fresh_inquiry_scores_low():
    assert score(RiskSignals(case_type="Inquiry")) < 25


def test_a_reopened_complaint_near_its_sla_deadline_scores_high():
    high = RiskSignals(
        case_type="Complaint",
        sla_fraction_elapsed=0.95,
        reopen_count=2,
        escalation_depth=1,
    )
    assert score(high) > 70


def test_a_complaint_outranks_an_inquiry_all_else_equal():
    common = {"sla_fraction_elapsed": 0.5, "reopen_count": 0}
    assert score(RiskSignals(case_type="Complaint", **common)) > score(
        RiskSignals(case_type="Inquiry", **common)
    )


def test_the_score_is_clamped_to_0_100():
    extreme = RiskSignals(
        case_type="Complaint",
        sla_fraction_elapsed=99.0,
        reopen_count=1000,
        escalation_depth=50,
        negative_sentiment=True,
    )
    assert score(extreme) == 100
    assert score(RiskSignals(sla_fraction_elapsed=-5.0, reopen_count=-3)) == 0


def test_missing_signals_degrade_gracefully_rather_than_raising():
    assert 0 <= score(RiskSignals()) <= 100
    assert 0 <= score(RiskSignals(case_type=None, sla_fraction_elapsed=None)) <= 100


def test_weights_are_configurable_without_a_code_change():
    doubled = {**DEFAULT_WEIGHTS, "case_type": DEFAULT_WEIGHTS["case_type"] * 2}
    signals = RiskSignals(case_type="Complaint")
    assert score(signals, weights=doubled) > score(signals)


def test_a_malformed_weight_map_falls_back_to_the_defaults():
    """A bad config value must not take scoring down with it."""
    assert score(RiskSignals(case_type="Complaint"), weights={"case_type": "high"}) == score(
        RiskSignals(case_type="Complaint")
    )


def test_the_score_is_deterministic_for_identical_signals():
    signals = RiskSignals(case_type="Complaint", sla_fraction_elapsed=0.6, reopen_count=1)
    assert len({score(signals) for _ in range(20)}) == 1


def test_the_contribution_of_each_signal_is_reportable():
    signals = RiskSignals(
        case_type="Complaint",
        sla_fraction_elapsed=0.9,
        reopen_count=2,
        escalation_depth=1,
        negative_sentiment=True,
    )
    breakdown = contributions(signals)

    assert set(breakdown) == set(DEFAULT_WEIGHTS)
    assert all(value >= 0 for value in breakdown.values())
    # The headline number IS the sum of the parts -- otherwise the explanation
    # would not explain the score.
    assert round(sum(breakdown.values())) == score(signals)


def test_the_biggest_contributor_is_identifiable():
    signals = RiskSignals(case_type="Inquiry", sla_fraction_elapsed=1.0)
    breakdown = contributions(signals)
    assert max(breakdown, key=lambda k: breakdown[k]) == "sla_proximity"


def test_sentiment_is_inert_until_p7_supplies_it():
    """Nothing writes sentiment today (verified in the gap analysis). An
    unset signal must contribute nothing rather than a default."""
    assert contributions(RiskSignals())["sentiment"] == 0
