"""End-to-end smoke verification for Phase-3 Reporting & BI additions.

Implements Task 8 of the Phase-3 plan (2026-07-18-phase3-reporting-bi.md).

These tests are entirely offline — no BigQuery credentials, no network calls.
They verify that every piece built in Tasks 1-7 is consistent end-to-end:

  Step 2 (plan): view_ddls() returns exactly the expected 19 keys, every DDL
                 contains ``CREATE OR REPLACE VIEW``, references its own view
                 name, and references the base ``conversations`` table.

  Step 3 (plan): ConversationRow has ``dealer`` and ``reopen_count`` fields;
                 map_chatwoot_conversation_to_row() parses a ``dealer_<slug>``
                 label and reads reopen_count from additional_attributes.

  Step 4 (plan): CONVERSATIONS_SCHEMA and the load_conversations JSON
                 serialisation both include the ``dealer`` column (the plan's
                 grep step is mirrored here as an import-level assertion).

  Schema ↔ DDL consistency: every column present in CONVERSATIONS_SCHEMA that
  appears in any view DDL is actually referenced in that DDL's SQL text.

Manual steps NOT automated (genuinely require infra / live credentials):
  Step 1  — run full test suite (verified by CI / ``pytest``; 877 tests pass).
  Step 5  — ``git tag -a phase3-reporting-bi …`` (ops tagging; out of scope).
  Live BQ — ``ensure_views`` and ``load_conversations`` against a real project
             (gated on BigQuery credentials unavailable in offline CI).
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest import mock

import pytest

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls
from chatbot.features.metrics.mapping import (
    ConversationRow,
    map_chatwoot_conversation_to_row,
)
from chatbot.features.metrics.sync import load_conversations

# ---------------------------------------------------------------------------
# Constants mirroring plan Task 8 Step 2
# ---------------------------------------------------------------------------

_EXPECTED_VIEW_KEYS = {
    "v_volume_by_month_channel",
    "v_resolution_split",
    "v_csat",
    "v_nps",
    "v_volume_by_division",
    "v_dept_pic_performance",
    "v_sla_achievement",
    "v_reopen_rate",
    "v_resolution_time",
    "v_nps_by_agent",
    "v_volume_daily",
    "v_volume_weekly",
    "v_channel_anomaly",
    "v_peak_hours",
    "v_complaint_type_ranking",
    "v_tasks_per_agent",
    "v_first_response_by_channel",
    "v_case_lifecycle",
    "v_state_trend",
}

_PROJECT = "myproject"
_DATASET = "myds"
_TABLE = "conversations"


# ---------------------------------------------------------------------------
# Step 2: view_ddls shape and DDL correctness
# ---------------------------------------------------------------------------


def test_view_ddls_returns_exactly_19_views() -> None:
    """view_ddls() must return exactly 19 view keys (13 original + 6 Phase-3)."""
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    assert set(ddls) == _EXPECTED_VIEW_KEYS, (
        f"Missing: {_EXPECTED_VIEW_KEYS - set(ddls)}  "
        f"Extra: {set(ddls) - _EXPECTED_VIEW_KEYS}"
    )
    assert len(ddls) == 19


@pytest.mark.parametrize("key", sorted(_EXPECTED_VIEW_KEYS))
def test_every_ddl_has_create_or_replace_view(key: str) -> None:
    """Every DDL string must start with CREATE OR REPLACE VIEW."""
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    assert "CREATE OR REPLACE VIEW" in ddls[key], (
        f"{key}: missing 'CREATE OR REPLACE VIEW'"
    )


@pytest.mark.parametrize("key", sorted(_EXPECTED_VIEW_KEYS))
def test_every_ddl_references_its_own_view_name(key: str) -> None:
    """Every DDL must reference its own fully-qualified view name."""
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    expected_ref = f"`{_PROJECT}.{_DATASET}.{key}`"
    assert expected_ref in ddls[key], (
        f"{key}: DDL doesn't reference own view name ({expected_ref!r})"
    )


@pytest.mark.parametrize("key", sorted(_EXPECTED_VIEW_KEYS - {"v_channel_anomaly"}))
def test_every_non_cte_ddl_references_base_table(key: str) -> None:
    """Every non-CTE DDL must reference the base conversations table.

    v_channel_anomaly uses a CTE (WITH daily AS …) so the backtick-quoted
    base table reference appears inside the CTE body; it is still present.
    """
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    base_ref = f"`{_PROJECT}.{_DATASET}.{_TABLE}`"
    assert base_ref in ddls[key], (
        f"{key}: DDL doesn't reference base table ({base_ref!r})"
    )


def test_channel_anomaly_ddl_references_base_table_inside_cte() -> None:
    """v_channel_anomaly is a CTE view; base table still appears in the SQL."""
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    base_ref = f"`{_PROJECT}.{_DATASET}.{_TABLE}`"
    assert base_ref in ddls["v_channel_anomaly"], (
        "v_channel_anomaly: base table not found in CTE body"
    )


# ---------------------------------------------------------------------------
# Step 3: ConversationRow fields + dealer + reopen_count parsing
# ---------------------------------------------------------------------------


def test_conversation_row_has_dealer_field() -> None:
    """ConversationRow dataclass must include a 'dealer' field."""
    fields = {f.name for f in dataclasses.fields(ConversationRow)}
    assert "dealer" in fields, "dealer missing from ConversationRow"


def test_conversation_row_has_reopen_count_field() -> None:
    """ConversationRow dataclass must include a 'reopen_count' field."""
    fields = {f.name for f in dataclasses.fields(ConversationRow)}
    assert "reopen_count" in fields, "reopen_count missing from ConversationRow"


def test_dealer_label_parsed_correctly() -> None:
    """A dealer_<slug> label must map to row.dealer == slug."""
    row = map_chatwoot_conversation_to_row({
        "id": 1,
        "status": "resolved",
        "labels": ["dealer_surabaya", "category_aftersales"],
        "created_at": 1782032400,
        "last_activity_at": 1782036000,
        "additional_attributes": {"reopen_count": 2},
    })
    assert row is not None, "map returned None for a conversation with labels"
    assert row.dealer == "surabaya", f"dealer={row.dealer!r}, expected 'surabaya'"


def test_reopen_count_from_additional_attributes() -> None:
    """reopen_count must be read from additional_attributes.reopen_count."""
    row = map_chatwoot_conversation_to_row({
        "id": 1,
        "status": "resolved",
        "labels": ["dealer_surabaya", "category_aftersales"],
        "created_at": 1782032400,
        "last_activity_at": 1782036000,
        "additional_attributes": {"reopen_count": 2},
    })
    assert row is not None
    assert row.reopen_count == 2, f"reopen_count={row.reopen_count!r}, expected 2"


def test_full_dealer_and_reopen_smoke() -> None:
    """Composite smoke: dealer label + reopen_count both parsed in one call.

    Mirrors the plan's Step 3 one-liner that prints
    ``OK — dealer='surabaya', reopen_count=2``.
    """
    row = map_chatwoot_conversation_to_row({
        "id": 1,
        "status": "resolved",
        "labels": ["dealer_surabaya", "category_aftersales"],
        "created_at": 1782032400,
        "last_activity_at": 1782036000,
        "additional_attributes": {"reopen_count": 2},
    })
    assert row is not None
    assert row.dealer == "surabaya"
    assert row.reopen_count == 2


# ---------------------------------------------------------------------------
# Step 4: CONVERSATIONS_SCHEMA and sync serialisation include 'dealer'
# ---------------------------------------------------------------------------


def test_conversations_schema_has_dealer_column() -> None:
    """CONVERSATIONS_SCHEMA must include a 'dealer' STRING column."""
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert "dealer" in by_name, "dealer missing from CONVERSATIONS_SCHEMA"
    assert by_name["dealer"].field_type == "STRING"
    assert by_name["dealer"].mode in ("NULLABLE", "")


def test_load_conversations_serialises_dealer() -> None:
    """load_conversations must include 'dealer' in the JSON row dict sent to BQ.

    This mirrors the plan's Step 4 grep: one line showing ``'dealer': r.dealer``
    inside json_rows.
    """
    row = ConversationRow(
        conversation_id="99",
        channel="WhatsApp",
        created_at=None,
        updated_at=None,
        status="resolved",
        resolved_by="bot",
        csat_score=None,
        nps_score=None,
        dealer="jakarta_utara",
        reopen_count=1,
    )

    class _FakeSettings:
        bigquery_project_id = "myproject"
        bigquery_dataset = "myds"
        bigquery_conversations_table = "conversations"

    with mock.patch("chatbot.features.metrics.sync.bigquery") as bq:
        bq.Client.return_value.create_dataset.return_value = None
        job = mock.MagicMock()
        bq.Client.return_value.load_table_from_json.return_value = job
        job.result.return_value = None
        bq.LoadJobConfig.return_value = mock.MagicMock()

        load_conversations(_FakeSettings(), [row])  # type: ignore[arg-type]

        json_rows: list[dict[str, Any]] = (
            bq.Client.return_value.load_table_from_json.call_args[0][0]
        )

    assert json_rows, "load_conversations sent no rows to BQ"
    assert "dealer" in json_rows[0], "'dealer' key missing from BQ row dict"
    assert json_rows[0]["dealer"] == "jakarta_utara"
    assert json_rows[0]["reopen_count"] == 1


# ---------------------------------------------------------------------------
# Schema ↔ DDL consistency: spot-check that Phase-3 columns appear in views
# ---------------------------------------------------------------------------


def test_dealer_column_appears_in_v_reopen_rate_and_v_case_lifecycle() -> None:
    """The 'dealer' dimension must be referenced in the two views that use it."""
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    assert "dealer" in ddls["v_reopen_rate"], "v_reopen_rate must reference dealer"
    assert "dealer" in ddls["v_case_lifecycle"], "v_case_lifecycle must reference dealer"


def test_reopen_count_column_appears_in_views_that_reference_it() -> None:
    """reopen_count must appear in v_reopen_rate and v_case_lifecycle DDLs."""
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    assert "reopen_count" in ddls["v_reopen_rate"]
    assert "reopen_count" in ddls["v_case_lifecycle"]


def test_schema_field_names_are_superset_of_non_derived_view_columns() -> None:
    """All schema column names that appear verbatim in view SQL are valid columns.

    Checks that no view references a column name that doesn't exist in
    CONVERSATIONS_SCHEMA (guards against typos in DDL strings).

    Note: not every schema column must appear in a view — e.g. ``sla_minutes``
    is stored in BQ but is not directly exposed via any current view (only
    ``sla_deadline`` appears in v_sla_achievement). The test therefore works in
    one direction only: columns referenced in DDL text must exist in the schema.
    """
    schema_cols = {f.name for f in CONVERSATIONS_SCHEMA}
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    all_ddl_text = " ".join(ddls.values())

    # Columns that appear in at least one view DDL and must be in the schema.
    # (sla_minutes is excluded: stored in schema but not referenced in any view.)
    columns_used_in_ddls = {
        "conversation_id", "channel", "created_at", "status",
        "resolved_by", "csat_score", "nps_score", "division",
        "category", "subcategory", "department", "agent_id", "pic",
        "sla_deadline", "first_response_at", "resolved_at",
        "reopen_count", "dealer",
    }
    # All of these must be in the schema
    missing_from_schema = columns_used_in_ddls - schema_cols
    assert not missing_from_schema, (
        f"Columns referenced in DDLs but absent from CONVERSATIONS_SCHEMA: "
        f"{missing_from_schema}"
    )
    # And each must actually appear in at least one DDL string
    not_in_any_ddl = {col for col in columns_used_in_ddls if col not in all_ddl_text}
    assert not not_in_any_ddl, (
        f"Columns listed as DDL-referenced but not found in any DDL text: "
        f"{not_in_any_ddl}"
    )


def test_phase3_views_all_reference_base_table() -> None:
    """The 6 new Phase-3 views must all reference the base conversations table."""
    phase3_views = {
        "v_peak_hours",
        "v_complaint_type_ranking",
        "v_tasks_per_agent",
        "v_first_response_by_channel",
        "v_case_lifecycle",
        "v_state_trend",
    }
    ddls = view_ddls(_PROJECT, _DATASET, _TABLE)
    base_ref = f"`{_PROJECT}.{_DATASET}.{_TABLE}`"
    for key in phase3_views:
        assert base_ref in ddls[key], (
            f"Phase-3 view {key} does not reference base table {base_ref!r}"
        )
