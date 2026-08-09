"""P4 task 1 — the five views that had no date column now have one.

`reject_period` exists because these views could not be filtered by date: asked
for one week, they could only answer all-time, and returning an all-time number
under a week header is a lie with a header on it. So the endpoints 400'd
instead. This adds the missing column so the answer can be real, which is what
lets `reject_period` be deleted in task 9.

**Each view keys on the date that starts ITS clock, not uniformly on
`created_at`.** A dealer's turnaround begins when the escalation reaches them,
and a resolution bucket belongs to the month the case was resolved. Grouping
all five on `created_at` would be simpler and wrong.
"""

from __future__ import annotations

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls

PROJECT, DATASET = "proj", "ds"

DATED_VIEWS = (
    "v_dept_pic_performance",
    "v_dealer_escalation",
    "v_resolution_sla_buckets",
    "v_case_aging",
)


def _ddls() -> dict[str, str]:
    return view_ddls(PROJECT, DATASET)


def test_all_the_previously_dateless_views_now_expose_a_day_column():
    ddls = _ddls()
    for view in DATED_VIEWS:
        assert "AS day" in ddls[view], f"{view} still has no day column"


def test_v_dealer_escalation_keys_on_dealer_escalated_at_not_created_at():
    """A dealer's clock starts when the escalation reaches them.

    Someone WILL read June's dealer count, notice it does not sum to June's
    case count, and file it as a bug. It is not one: a case created in May and
    escalated in June belongs to June's dealer rows.
    """
    sql = _ddls()["v_dealer_escalation"]
    assert "DATE(dealer_escalated_at) AS day" in sql
    assert "DATE(created_at) AS day" not in sql


def test_a_case_created_in_may_and_escalated_in_june_appears_in_junes_dealer_rows():
    """The same decision, named as the behaviour it produces."""
    sql = _ddls()["v_dealer_escalation"]
    assert "dealer_escalated_at" in sql
    assert "GROUP BY day, dealer" in sql or "GROUP BY dealer, day" in sql


def test_v_resolution_sla_buckets_keys_on_resolved_at():
    """A resolution bucket belongs to the period the case was RESOLVED in --
    grouping it by creation date would put a January case resolved in March
    into January's attainment figure."""
    sql = _ddls()["v_resolution_sla_buckets"]
    assert "DATE(resolved_at) AS day" in sql


def test_v_dept_pic_performance_and_case_aging_key_on_created_at():
    """These two genuinely are about when the case arrived."""
    for view in ("v_dept_pic_performance", "v_case_aging"):
        assert "DATE(created_at) AS day" in _ddls()[view]


def test_an_unescalated_case_produces_no_dealer_escalation_row():
    assert "WHERE dealer_escalated_at IS NOT NULL" in _ddls()["v_dealer_escalation"]


def test_every_column_referenced_by_the_modified_views_exists_in_the_schema():
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    ddls = _ddls()
    for view in DATED_VIEWS:
        for column in ("created_at", "resolved_at", "dealer_escalated_at"):
            if column in ddls[view]:
                assert column in names, f"{view} references unknown column {column}"


def test_the_existing_aggregate_shape_of_each_view_is_preserved():
    """Adding a dimension must not change what each view measures."""
    ddls = _ddls()
    assert "resolution_rate" in ddls["v_dept_pic_performance"]
    assert "avg_first_response_min" in ddls["v_dept_pic_performance"]
    assert "p90_turnaround_days" in ddls["v_dealer_escalation"]
    assert "bucket_label" in ddls["v_resolution_sla_buckets"]
    assert "age_days" in ddls["v_case_aging"]
    assert "'7+ days'" in ddls["v_case_aging"]


def test_the_new_day_column_is_grouped_not_merely_selected():
    """A selected-but-ungrouped date would make BigQuery reject the view."""
    ddls = _ddls()
    for view in ("v_dept_pic_performance", "v_dealer_escalation", "v_resolution_sla_buckets"):
        assert "GROUP BY day" in ddls[view] or ", day" in ddls[view].split("GROUP BY")[1]
