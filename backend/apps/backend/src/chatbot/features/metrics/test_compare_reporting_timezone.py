"""P4 task 4 — the script an operator runs before switching the timezone.

Switching re-buckets every historical figure on every dashboard. This is the
evidence that Monday's movement was expected. The fourth test is a safety
property, not a style check: an operator runs this against production while
deciding, so it must not be able to change production.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[7] / "scripts" / "compare-reporting-timezone.py"
)
_spec = importlib.util.spec_from_file_location("cmp_tz", _PATH)
cmp_tz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmp_tz)


def _sql(from_tz="UTC", to_tz="Asia/Kuala_Lumpur"):
    return cmp_tz.build_sql("p", "d", "conversations", "2026-07-01", "2026-07-31", from_tz, to_tz)


def test_identical_timezones_report_zero_movement():
    rows = [
        {"bucket_before": "2026-07-01", "bucket_after": "2026-07-01", "cases": 10},
        {"bucket_before": "2026-07-02", "bucket_after": "2026-07-02", "cases": 5},
    ]
    total, moved, pct = cmp_tz.summarise(rows)
    assert (total, moved, pct) == (15, 0, 0.0)
    assert "No cases change bucket." in cmp_tz.render(rows, "UTC", "UTC")


def test_a_utc_to_myt_comparison_reports_the_cases_that_move():
    rows = [
        {"bucket_before": "2026-07-01", "bucket_after": "2026-07-01", "cases": 90},
        {"bucket_before": "2026-07-01", "bucket_after": "2026-07-02", "cases": 10},
    ]
    total, moved, pct = cmp_tz.summarise(rows)
    assert (total, moved) == (100, 10)
    assert pct == pytest.approx(10.0)


def test_the_output_names_both_the_source_and_destination_bucket():
    rows = [{"bucket_before": "2026-07-01", "bucket_after": "2026-07-02", "cases": 7}]
    out = cmp_tz.render(rows, "UTC", "Asia/Kuala_Lumpur")
    assert "2026-07-01" in out and "2026-07-02" in out
    assert "UTC -> Asia/Kuala_Lumpur" in out


def test_the_script_is_read_only_and_creates_no_views():
    """The safety property."""
    sql = _sql()
    assert cmp_tz.assert_read_only(sql) is sql
    lowered = sql.lower()
    # Note the trailing spaces: a naive "create" substring check matches
    # `created_at` and would reject every legitimate query. The module's own
    # token list has the same trailing spaces for exactly this reason.
    for forbidden in ("create ", "replace", "drop ", "delete ", "insert ", "update "):
        assert forbidden not in lowered


def test_a_non_select_query_is_refused():
    with pytest.raises(cmp_tz.NotReadOnly):
        cmp_tz.assert_read_only("CREATE OR REPLACE VIEW x AS SELECT 1")
    with pytest.raises(cmp_tz.NotReadOnly):
        cmp_tz.assert_read_only("SELECT 1; DROP TABLE conversations")


def test_a_summary_line_states_the_total_percentage_of_rows_affected():
    rows = [
        {"bucket_before": "2026-07-01", "bucket_after": "2026-07-01", "cases": 75},
        {"bucket_before": "2026-07-01", "bucket_after": "2026-07-02", "cases": 25},
    ]
    out = cmp_tz.render(rows, "UTC", "Asia/Kuala_Lumpur")
    assert "25 of 100 cases (25.0%)" in out
    assert "Totals are unchanged" in out


def test_the_date_window_is_parameterised_not_interpolated():
    """These come from an operator's shell; they are never pasted into SQL."""
    sql = _sql()
    assert "@from_date" in sql and "@to_date" in sql
    assert "2026-07-01" not in sql


def test_utc_produces_the_bare_date_call_on_both_sides():
    assert "DATE(created_at) AS bucket_before" in _sql(from_tz="UTC")
    assert "DATE(created_at, 'Asia/Kuala_Lumpur') AS bucket_after" in _sql()
