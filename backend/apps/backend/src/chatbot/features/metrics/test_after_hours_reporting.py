"""P1 tasks 7-8 — the in-hours flag reaches the warehouse, and the two views.

The agent stamps `received_in_business_hours` at intake; until now nothing
carried it into BigQuery, so "how much of our volume arrives after hours?" —
the question that justifies staffing changes — could not be answered at all.

The nullability decision is the load-bearing one. Every row synced before P1
has no attribute, and mapping absent -> False would silently reclassify the
entire history as after-hours. Absent must stay absent, and the views must
bucket it as `unknown`, never as `after_hours`.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls
from chatbot.features.metrics.mapping import map_chatwoot_conversation_to_row

PROJECT, DATASET = "proj", "ds"


def _conv(**custom: Any) -> dict[str, Any]:
    """A Chatwoot conversation dict complete enough for the mapper to accept."""
    return {
        "id": 42,
        "status": "open",
        "created_at": 1_780_000_000,
        "last_activity_at": 1_780_003_600,
        "meta": {"sender": {"id": 1}},
        "messages": [{"source_id": "wa-123"}],
        "custom_attributes": custom,
        "labels": ["dept_sales"],
    }


def _row(**custom: Any):
    return map_chatwoot_conversation_to_row(_conv(**custom))


# --- task 7: the columns --------------------------------------------------


def test_a_conversation_with_the_attribute_maps_to_the_boolean_column():
    row = _row(received_in_business_hours=True)
    assert row is not None
    assert row.received_in_business_hours is True


def test_a_conversation_without_the_attribute_maps_to_none_not_false():
    row = _row()
    assert row is not None
    assert row.received_in_business_hours is None


def test_a_string_true_from_chatwoot_custom_attributes_maps_to_boolean_true():
    """Chatwoot round-trips custom attributes as strings often enough that
    trusting the JSON type here would drop the flag on real tenants."""
    row = _row(received_in_business_hours="true")
    assert row is not None
    assert row.received_in_business_hours is True


def test_a_string_false_maps_to_boolean_false_not_truthy():
    row = _row(received_in_business_hours="false")
    assert row is not None
    assert row.received_in_business_hours is False


def test_the_local_arrival_timestamp_is_carried_through():
    row = _row(received_at_local="2026-07-03T18:00:00+08:00")
    assert row is not None
    assert row.received_at_local == "2026-07-03T18:00:00+08:00"


def test_the_new_columns_are_nullable_so_historical_rows_still_load():
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert by_name["received_in_business_hours"].field_type == "BOOLEAN"
    assert by_name["received_in_business_hours"].mode == "NULLABLE"
    assert by_name["received_at_local"].field_type == "TIMESTAMP"
    assert by_name["received_at_local"].mode == "NULLABLE"


# --- task 8: the views ----------------------------------------------------


def _ddls() -> dict[str, str]:
    return view_ddls(PROJECT, DATASET)


def test_both_views_appear_in_view_ddls():
    ddls = _ddls()
    assert "v_first_response_by_hours_split" in ddls
    assert "v_volume_after_hours" in ddls


def test_every_column_referenced_by_the_new_views_exists_in_conversations_schema():
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    ddls = _ddls()
    for view in ("v_first_response_by_hours_split", "v_volume_after_hours"):
        sql = ddls[view]
        for column in (
            "received_in_business_hours",
            "first_response_working_minutes",
            "created_at",
            "channel",
        ):
            if column in sql:
                assert column in names, f"{view} references unknown column {column}"


def test_the_hours_split_view_reads_first_response_working_minutes():
    assert "first_response_working_minutes" in _ddls()["v_first_response_by_hours_split"]


def test_rows_with_a_null_in_hours_flag_are_bucketed_as_unknown_not_after_hours():
    for view in ("v_first_response_by_hours_split", "v_volume_after_hours"):
        sql = _ddls()[view]
        assert "'unknown'" in sql, f"{view} must have an explicit unknown bucket"
        assert "IS NULL" in sql, f"{view} must test the flag for NULL explicitly"


def test_the_after_hours_volume_view_groups_by_a_date_so_it_is_period_capable():
    assert "FORMAT_DATE" in _ddls()["v_volume_after_hours"]
