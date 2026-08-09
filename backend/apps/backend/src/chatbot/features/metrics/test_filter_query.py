"""P4 task 5 — dimension filters on the metrics endpoints.

The security pair is `test_filter_values_are_bound_as_parameters` and
`test_a_filter_value_containing_sql_syntax_is_harmless`. Both assert the
BINDING, not the rendered SQL: a test that greps the SQL string for the value
would pass on an interpolated query that happened to escape correctly, which is
exactly the bug it exists to catch.

`test_a_filter_a_view_cannot_honour_400s_naming_both` is the other principle --
silently ignoring a filter serves an unfiltered answer under a filtered header,
the same class of lie `reject_period` guarded against for dates.
"""

from __future__ import annotations

import pytest

from chatbot.features.metrics.filter_query import (
    MetricFilters,
    UnsupportedFilter,
    metric_filters,
)


def test_no_filters_produces_no_predicate():
    fragment, params = MetricFilters().predicates_for("v_case_aging")
    assert (fragment, params) == ("", {})


def test_a_blank_filter_is_not_a_filter():
    """An empty query param means the box was cleared, not that the user wants
    rows whose department is the empty string."""
    fragment, params = MetricFilters(department="   ").predicates_for("v_dept_pic_performance")
    assert (fragment, params) == ("", {})


def test_a_department_filter_narrows_the_result_set():
    fragment, params = MetricFilters(department="sales").predicates_for(
        "v_dept_pic_performance"
    )
    assert "department = @filter_department" in fragment
    assert params == {"filter_department": "sales"}


def test_a_channel_filter_narrows_the_result_set():
    fragment, params = MetricFilters(channel="Email").predicates_for("v_sla_achievement")
    assert "channel = @filter_channel" in fragment
    assert params == {"filter_channel": "Email"}


def test_two_filters_compose_as_an_and():
    """Filters narrow. Two filters mean both, never either."""
    fragment, params = MetricFilters(channel="Email", agent_id="7").predicates_for(
        "v_nps_by_agent"
    )
    assert " AND " in fragment
    assert set(params) == {"filter_channel", "filter_agent_id"}


def test_team_maps_onto_the_department_dimension():
    fragment, _ = MetricFilters(team="aftersales").predicates_for("v_dept_pic_performance")
    assert "department = @filter_team" in fragment


def test_a_filter_a_view_cannot_honour_400s_naming_both():
    with pytest.raises(UnsupportedFilter) as excinfo:
        MetricFilters(department="sales").predicates_for("v_volume_by_tag")
    detail = excinfo.value.detail
    assert "department" in detail
    assert "v_volume_by_tag" in detail
    assert excinfo.value.status_code == 400


def test_the_rejection_lists_what_the_view_does_support():
    with pytest.raises(UnsupportedFilter) as excinfo:
        MetricFilters(agent_id="7").predicates_for("v_dealer_escalation")
    assert "dealer" in excinfo.value.detail


def test_filter_values_are_bound_as_parameters_not_interpolated():
    """Asserts the binding, not the string."""
    fragment, params = MetricFilters(dealer="komang_motor").predicates_for(
        "v_dealer_escalation"
    )
    assert "komang_motor" not in fragment
    assert "@filter_dealer" in fragment
    assert params["filter_dealer"] == "komang_motor"


def test_a_filter_value_containing_sql_syntax_is_harmless():
    hostile = "x'; DROP TABLE conversations; --"
    fragment, params = MetricFilters(dealer=hostile).predicates_for("v_dealer_escalation")
    assert "DROP" not in fragment
    assert fragment == "dealer = @filter_dealer"
    assert params["filter_dealer"] == hostile


def test_an_unknown_view_does_not_crash_the_predicate_builder():
    """A view with no entry in the map is not yet classified; it must not 500.
    The filter still binds, and BigQuery rejects it loudly if the column really
    is absent."""
    fragment, params = MetricFilters(channel="Email").predicates_for("v_something_new")
    assert fragment == "channel = @filter_channel"
    assert params == {"filter_channel": "Email"}


def test_the_dependency_builds_the_same_shape():
    filters = metric_filters(agent_id="7", team=None, department=None, channel="Email", dealer=None)
    assert filters.active == {"agent_id": "7", "channel": "Email"}
