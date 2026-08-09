"""P3 tasks 6-7 — case_detail in the pivots, case_state as its own series.

Two design decisions are asserted here rather than merely written down:

1. **`v_state_trend` still reads Chatwoot's `status` and is untouched.**
   `v_case_state_trend` is a new view beside it. Redefining the old one would
   silently change every number already reported from it.

2. **Nothing is dropped for being null.** A case with no `case_detail` is
   bucketed `Unspecified`, and one with no `case_state` reports `unknown`.
   Filtering them out would make the pivot's total disagree with the headline
   count -- the exact C2 297-vs-264 discrepancy the gap analysis raised as
   question Q8.
"""

from __future__ import annotations

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls

PROJECT, DATASET = "proj", "ds"


def _ddls() -> dict[str, str]:
    return view_ddls(PROJECT, DATASET)


# --- task 6: case_detail --------------------------------------------------


def test_v_category_by_vehicle_model_now_groups_by_case_detail():
    sql = _ddls()["v_category_by_vehicle_model"]
    assert "case_detail" in sql
    assert "GROUP BY category, subcategory, vehicle_model, case_type, case_detail" in sql


def test_the_extended_view_still_groups_by_everything_it_did_before():
    sql = _ddls()["v_category_by_vehicle_model"]
    for column in ("category", "subcategory", "vehicle_model", "case_type"):
        assert column in sql


def test_v_concern_pivot_appears_in_view_ddls():
    assert "v_concern_pivot" in _ddls()


def test_v_concern_pivot_references_only_columns_that_exist():
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    sql = _ddls()["v_concern_pivot"]
    for column in ("division", "subcategory", "case_detail", "created_at"):
        assert column in sql
        assert column in names


def test_v_concern_pivot_includes_a_grand_total_row():
    """ROLLUP is what produces the subtotal and grand-total rows the client's
    pivot prints."""
    assert "ROLLUP" in _ddls()["v_concern_pivot"]


def test_rows_with_a_null_case_detail_are_bucketed_as_unspecified_not_dropped():
    """The test that decides whether the slide reconciles."""
    sql = _ddls()["v_concern_pivot"]
    assert "'Unspecified'" in sql
    assert "WHERE case_detail IS NOT NULL" not in sql


# --- task 7: case_state ---------------------------------------------------


def test_v_case_state_trend_appears_in_view_ddls():
    assert "v_case_state_trend" in _ddls()


def test_the_trend_reports_wip_and_temp_closed_as_distinct_series():
    sql = _ddls()["v_case_state_trend"]
    assert "'wip'" in sql
    assert "'temp_closed'" in sql
    assert "'closed'" in sql


def test_the_trend_carries_the_escalation_dimension():
    assert "escalated_to" in _ddls()["v_case_state_trend"]


def test_v_state_trend_is_unchanged_and_still_reads_the_chatwoot_status():
    """Four existing views read `status`; redefining it would silently change
    every number already reported."""
    sql = _ddls()["v_state_trend"]
    assert "status" in sql
    assert "case_state" not in sql


def test_a_null_case_state_is_reported_as_unknown_not_folded_into_open():
    sql = _ddls()["v_case_state_trend"]
    assert "'unknown'" in sql
    assert "case_state IS NULL" in sql


def test_v_case_aging_gains_case_state_without_changing_its_existing_buckets():
    sql = _ddls()["v_case_aging"]
    assert "case_state" in sql
    for bucket in ("'1-3 days'", "'4-6 days'", "'7+ days'"):
        assert bucket in sql
    assert "WHERE status IN ('open', 'pending')" in sql


def test_the_new_views_reference_only_real_columns():
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    for view in ("v_case_state_trend", "v_concern_pivot"):
        sql = _ddls()[view]
        for column in ("case_state", "case_detail", "escalated_to"):
            if column in sql:
                assert column in names, f"{view} references unknown column {column}"
