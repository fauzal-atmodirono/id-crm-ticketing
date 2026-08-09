"""P5 task 2 — four outcomes, and why the fourth one is the whole point.

`test_a_none_actual_is_no_data_and_never_missed` and
`test_a_zero_actual_is_distinguishable_from_a_missing_actual` are this
package's reason for existing. An abandon rate of 0% and an abandon rate that
cannot be measured are different statements, and only one of them is true
today -- there is no call queue to abandon anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from chatbot.features.metrics.attainment import evaluate


@dataclass(frozen=True)
class _Target:
    value: float
    comparator: str = "lte"
    unit: str | None = "minutes"
    attainment_pct: float | None = None


def test_an_actual_inside_an_lte_target_is_met():
    assert evaluate(90.0, _Target(120)).status == "met"


def test_an_actual_outside_an_lte_target_is_missed():
    result = evaluate(150.0, _Target(120))
    assert result.status == "missed"
    assert result.is_failure is True


def test_an_actual_above_a_gte_target_is_met():
    assert evaluate(95.0, _Target(90, comparator="gte")).status == "met"


def test_an_actual_below_a_gte_target_is_missed():
    assert evaluate(80.0, _Target(90, comparator="gte")).status == "missed"


def test_a_none_actual_is_no_data_and_never_missed():
    """The load-bearing test. A metric with no source has not failed."""
    result = evaluate(None, _Target(120))
    assert result.status == "no_data"
    assert result.is_failure is False
    assert result.actual is None


def test_a_none_target_is_no_target_and_never_missed():
    result = evaluate(50.0, None)
    assert result.status == "no_target"
    assert result.is_failure is False


def test_a_target_whose_value_is_unset_is_no_target():
    assert evaluate(50.0, _Target(value=None)).status == "no_target"


def test_a_zero_actual_is_distinguishable_from_a_missing_actual():
    """0% abandon and un-measurable abandon are different statements."""
    measured = evaluate(0.0, _Target(5))
    unmeasured = evaluate(None, _Target(5))

    assert measured.status == "met"
    assert measured.actual == 0.0
    assert unmeasured.status == "no_data"
    assert unmeasured.actual is None
    assert measured.status != unmeasured.status


def test_only_missed_counts_as_a_failure():
    """`status != "met"` is the bug this guards: it sweeps no_data and
    no_target into the failure bucket."""
    assert evaluate(150.0, _Target(120)).is_failure is True
    for not_a_failure in (evaluate(None, _Target(120)), evaluate(5.0, None)):
        assert not_a_failure.is_failure is False


def test_an_attainment_pct_target_compares_the_percentage_not_the_raw_value():
    """"90% of cases within 2 hours" is a different question from "the average
    case is within 2 hours"."""
    target = _Target(value=120, comparator="gte", attainment_pct=90.0)
    assert evaluate(95.0, target).status == "met"
    assert evaluate(85.0, target).status == "missed"
    assert evaluate(95.0, target).target == 90.0


def test_the_variance_is_signed_so_a_slide_can_show_direction():
    assert evaluate(150.0, _Target(120)).variance == 30.0
    assert evaluate(90.0, _Target(120)).variance == -30.0


def test_equality_counts_as_met_for_both_comparators():
    """A 2-hour target is met at exactly 2 hours, not missed by rounding."""
    assert evaluate(120.0, _Target(120)).status == "met"
    assert evaluate(90.0, _Target(90, comparator="gte")).status == "met"


def test_the_unit_is_carried_through_so_the_slide_can_label_the_number():
    assert evaluate(90.0, _Target(120, unit="working_minutes")).unit == "working_minutes"
