"""P4 tasks 7-8 — per-dealer first response, and the tag breakdown (§4.80).

Two honesty properties carry these:

* **A case with no first response has not missed the target — it has not
  answered it.** Counting open cases as failures makes the attainment rate fall
  as volume rises, which is backwards, and it would be read as a service
  regression.
* **Tag counts double-count by construction.** A case with three labels is in
  three buckets, so summing the tag column gives a number larger than the case
  count. The response says so, because a slide that totals them will otherwise
  be confidently wrong.
"""

from __future__ import annotations

from dataclasses import fields

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls
from chatbot.features.metrics.mapping import ConversationRow

PROJECT, DATASET = "proj", "ds"


def _ddls() -> dict[str, str]:
    return view_ddls(PROJECT, DATASET)


# --- task 7: per-dealer first response ------------------------------------


def test_v_first_response_by_dealer_appears_in_view_ddls():
    assert "v_first_response_by_dealer" in _ddls()


def test_it_reads_first_response_working_minutes():
    """Stored since Package E and read by nothing until P1. This is its
    second reader."""
    sql = _ddls()["v_first_response_by_dealer"]
    assert "first_response_working_minutes" in sql
    assert "dealer" in sql


def test_the_attainment_rate_is_the_percentage_meeting_the_threshold():
    sql = _ddls()["v_first_response_by_dealer"]
    assert "attainment_rate" in sql
    assert "SAFE_DIVIDE" in sql


def test_a_threshold_of_120_working_minutes_matches_the_two_working_hour_target():
    sql = view_ddls(PROJECT, DATASET, first_response_target_minutes=120)[
        "v_first_response_by_dealer"
    ]
    assert "120" in sql


def test_cases_with_no_first_response_are_excluded_from_the_rate_not_counted_as_failures():
    """The decision that matters. An open case has not missed the target."""
    sql = _ddls()["v_first_response_by_dealer"]
    assert "first_response_working_minutes IS NOT NULL" in sql


def test_the_denominator_is_reported_alongside_the_percentage():
    """100% over 3 cases and 100% over 3,000 are different statements."""
    assert "measured_cases" in _ddls()["v_first_response_by_dealer"]


# --- task 8: the tag breakdown --------------------------------------------


def test_the_labels_column_reaches_the_warehouse():
    """v_volume_by_tag unnests it, so it has to be loaded first."""
    assert "labels" in {f.name for f in CONVERSATIONS_SCHEMA}
    assert "labels" in {f.name for f in fields(ConversationRow)}


def test_the_labels_column_is_repeated_not_a_joined_string():
    """A comma-joined string cannot be UNNESTed, and splitting it in SQL would
    break on any label containing a comma."""
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert by_name["labels"].mode == "REPEATED"
    assert by_name["labels"].field_type == "STRING"


def test_v_volume_by_tag_unnests_the_labels_column():
    sql = _ddls()["v_volume_by_tag"]
    assert "UNNEST(labels)" in sql
    assert "AS tag" in sql


def test_a_case_with_three_labels_appears_under_each_of_them():
    """That is what UNNEST does, and it is the intended behaviour -- stated as
    a named test because it is also why the totals do not add up."""
    assert "UNNEST(labels)" in _ddls()["v_volume_by_tag"]


def test_the_tag_view_is_period_capable():
    assert "AS day" in _ddls()["v_volume_by_tag"]


def test_a_case_with_no_labels_does_not_vanish_silently():
    """UNNEST drops label-less rows. That is correct for a tag breakdown, but
    it means the view's total is NOT the case count -- which is exactly what
    the response note has to say."""
    sql = _ddls()["v_volume_by_tag"]
    assert "UNNEST(labels)" in sql
