"""P5 task 3 — the control-item declaration.

`test_abandon_rate_reports_no_data_and_not_zero_percent` is the one to write
first and never delete. It is the specific false claim this package exists to
prevent: a 0% abandon rate is excellent performance, and we cannot measure
abandonment at all.
"""

from __future__ import annotations

from chatbot.features.metrics.attainment import evaluate
from chatbot.features.metrics.control_items import (
    CONTROL_ITEMS,
    MEASURABLE_ITEMS,
    UNMEASURABLE_ITEMS,
)


def test_there_are_exactly_fourteen_rows():
    assert len(CONTROL_ITEMS) == 14
    assert [i.number for i in CONTROL_ITEMS] == list(range(1, 15))


def test_nine_rows_are_measurable_and_five_are_not():
    assert len(MEASURABLE_ITEMS) == 9
    assert len(UNMEASURABLE_ITEMS) == 5


def test_every_unmeasurable_row_carries_a_blocking_reason():
    for item in UNMEASURABLE_ITEMS:
        assert item.blocking_reason, f"row {item.number} has no reason"


def test_each_blocking_reason_is_a_client_facing_sentence_not_a_code_reference():
    for item in UNMEASURABLE_ITEMS:
        reason = item.blocking_reason
        assert reason.endswith(".")
        assert len(reason.split()) >= 8, f"row {item.number}: too terse to be useful"
        for code_smell in ("None", "null", "TODO", "def ", "v_"):
            assert code_smell not in reason, f"row {item.number} leaks {code_smell!r}"


def test_abandon_rate_reports_no_data_and_not_zero_percent():
    """The specific false claim this package must not make."""
    abandon = next(i for i in CONTROL_ITEMS if "abandon" in i.label.lower())

    assert abandon.source_view is None
    assert evaluate(None, None).status in ("no_data", "no_target")
    assert evaluate(None, object()).status == "no_target"
    # and the reason says so in as many words
    assert "not a zero-abandon result" in abandon.blocking_reason


def test_the_hq_row_points_at_q5_rather_than_reporting_zero():
    hq = next(i for i in CONTROL_ITEMS if "HQ" in i.label)
    assert hq.source_view is None
    assert "Q5" in hq.blocking_reason
    assert "zero" in hq.blocking_reason


def test_every_measurable_row_names_a_source_view_and_field():
    for item in MEASURABLE_ITEMS:
        assert item.source_view and item.source_view.startswith("v_")
        assert item.metric_field


def test_items_7_and_8_read_the_existing_resolution_bucket_view():
    """The compatibility guard for the two rows that already work."""
    for number in (7, 8):
        item = next(i for i in CONTROL_ITEMS if i.number == number)
        assert item.source_view == "v_resolution_sla_buckets"
        assert item.target_key.startswith("resolution_")


def test_a_row_with_no_target_configured_reports_no_target_not_missed():
    """Absence of configuration is not a performance failure."""
    result = evaluate(42.0, None)
    assert result.status == "no_target"
    assert result.is_failure is False


def test_no_measurable_row_is_missing_its_label():
    for item in CONTROL_ITEMS:
        assert item.label.strip()
