"""P3 tasks 2-3 — the new case fields reach the warehouse, and only additively.

The load-bearing assertion here is `test_the_status_column_is_still_the_chatwoot
_status_not_the_case_state`. Four existing views read `status`. `case_state` is
a NEW column beside it, never a redefinition of it, and that is the whole
reason those views stay honest.

The second theme is nullability. Every row synced before this package has none
of these attributes. Mapping absent to `""` or to a default fabricates history
-- and a REQUIRED column would fail the entire load job on the first old
conversation the sync touches.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.chat.case_fields import CASE_FIELD_NAMES
from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA
from chatbot.features.metrics.mapping import map_chatwoot_conversation_to_row

NEW_COLUMNS = (
    "case_detail",
    "case_state",
    "escalated_to",
    "vehicle_plate",
    "vehicle_chassis",
    "purchased_from_dealer",
    "delay_reason",
    "wip_issue",
    "wip_action_taken",
    "wip_next_action",
)


def _conv(labels: list[str] | None = None, **custom: Any) -> dict[str, Any]:
    return {
        "id": 42,
        "status": "open",
        "created_at": 1_780_000_000,
        "last_activity_at": 1_780_003_600,
        "meta": {"sender": {"id": 1}},
        "messages": [{"source_id": "wa-123"}],
        "custom_attributes": custom,
        "labels": labels if labels is not None else ["dept_sales"],
    }


def _row(labels: list[str] | None = None, **custom: Any):
    row = map_chatwoot_conversation_to_row(_conv(labels, **custom))
    assert row is not None
    return row


# --- task 2: mapping ------------------------------------------------------


def test_case_detail_is_read_from_custom_attributes():
    assert _row(case_detail="Sales: Refund: Booking — Status").case_detail == (
        "Sales: Refund: Booking — Status"
    )


def test_case_state_is_read_from_the_case_state_attribute():
    assert _row(case_state="WIP").case_state == "WIP"


def test_the_status_column_is_still_the_chatwoot_status_not_the_case_state():
    """The design decision, asserted. Four existing views read `status`."""
    row = _row(case_state="TEMP_CLOSED")
    assert row.status == "open"
    assert row.case_state == "TEMP_CLOSED"


def test_a_conversation_with_no_new_attributes_maps_every_one_to_none():
    row = _row()
    for column in NEW_COLUMNS:
        if column == "escalated_to":
            continue  # derived, covered below
        assert getattr(row, column) is None, f"{column} should be None"


def test_absent_attributes_map_to_none_not_to_empty_string():
    row = _row()
    assert row.vehicle_plate is None
    assert row.vehicle_plate != ""


def test_a_blank_attribute_value_maps_to_none():
    assert _row(vehicle_plate="   ").vehicle_plate is None


def test_a_malformed_attribute_value_maps_to_none_rather_than_failing_the_row():
    """One bad value must not cost the whole conversation. The sync loads
    thousands of rows per run; raising here would drop every one after it."""
    assert _row(wip_issue="x" * 5000).wip_issue is None


def test_values_are_normalised_on_the_way_in():
    assert _row(vehicle_plate="wxy 1234").vehicle_plate == "WXY1234"


def test_escalated_to_is_derived_as_dealer_when_a_dealer_label_is_present():
    row = _row(labels=["dept_sales", "dealer_komang_motor"])
    assert row.escalated_to == "dealer"


def test_escalated_to_is_none_when_no_dealer_label_is_present():
    assert _row(labels=["dept_sales"]).escalated_to == "none"


def test_escalated_to_is_never_hq_until_q5_is_answered():
    """Even if somebody sets the attribute by hand."""
    row = _row(labels=["dept_sales"], escalated_to="hq")
    assert row.escalated_to != "hq"


# --- task 3: schema -------------------------------------------------------


def test_all_ten_columns_are_present_in_conversations_schema():
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    for column in NEW_COLUMNS:
        assert column in names, f"{column} missing from CONVERSATIONS_SCHEMA"


def test_all_new_columns_are_nullable():
    """A REQUIRED column fails the whole load job on the first pre-P3 row."""
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    for column in NEW_COLUMNS:
        assert by_name[column].mode == "NULLABLE", f"{column} is not NULLABLE"


def test_all_new_columns_are_strings():
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    for column in NEW_COLUMNS:
        assert by_name[column].field_type == "STRING"


def test_no_existing_column_changed_type_or_mode():
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert by_name["status"].field_type == "STRING"
    assert by_name["conversation_id"].mode == "REQUIRED"
    assert by_name["channel"].mode == "REQUIRED"


def test_the_field_spec_and_the_schema_do_not_drift():
    """CASE_FIELDS is the source of truth; the schema must carry all of it."""
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    for field in CASE_FIELD_NAMES:
        assert field in names, f"{field} is in CASE_FIELDS but not the schema"
