from chatbot.features.metrics import bigquery_schema
from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls


def test_schema_has_expected_fields() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    assert names == {
        "conversation_id",
        "channel",
        "created_at",
        "updated_at",
        "status",
        "resolved_by",
        "csat_score",
        "nps_score",
        "synced_at",
        "division",
        "category",
        "subcategory",
        "department",
        "agent_id",
        "pic",
        "sla_minutes",
        "sla_deadline",
        "first_response_at",
        "resolved_at",
        "reopen_count",
        "dealer",  # new Phase-3 field
        "dealer_escalated_at",  # Task 10
        "case_type",
        "vehicle_model",
        "first_response_working_minutes",
        "resolution_working_minutes",
    }


def test_view_ddls_keys_and_targets() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert set(ddls) == {
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
        # Phase-3 additions
        "v_peak_hours",
        "v_complaint_type_ranking",
        "v_tasks_per_agent",
        "v_first_response_by_channel",
        "v_case_lifecycle",
        "v_state_trend",
        "v_resolution_sla_buckets",
        # Task 12: v_dealer_escalation
        "v_dealer_escalation",
        "v_dealer_escalation_slowest_cases",
        "v_case_aging",
        # Task 13: v_volume_by_type_division and v_category_by_vehicle_model
        "v_volume_by_type_division",
        "v_category_by_vehicle_model",
        # Task 2 (Package E) reopened: day-grain siblings for week/day
        # granularity -- see the comments beside v_state_trend and
        # v_volume_by_type_division in bigquery_schema.py for why these
        # are separate views rather than widening the month-grain ones.
        "v_state_trend_daily",
        "v_volume_by_type_division_daily",
    }
    assert "`proj.ds.v_volume_by_month_channel`" in ddls["v_volume_by_month_channel"]
    assert "`proj.ds.conversations`" in ddls["v_volume_by_month_channel"]


def test_view_ddls_include_anomaly_view() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert "v_channel_anomaly" in ddls
    assert "v_channel_anomaly" in ddls["v_channel_anomaly"]


def test_view_ddls_contain_expected_aggregates() -> None:
    ddls = view_ddls("proj", "ds")
    assert "COUNT(*)" in ddls["v_volume_by_month_channel"]
    assert "resolved_by='bot'" in ddls["v_resolution_split"].replace('"', "'")
    assert "transfer_to_agent" in ddls["v_resolution_split"]
    assert "satisfied_rate" in ddls["v_csat"]
    assert "csat_score >= 4" in ddls["v_csat"].replace(">=4", ">= 4")


def test_schema_includes_nps_score() -> None:
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert "nps_score" in by_name
    assert by_name["nps_score"].field_type == "INT64"


def test_v_nps_view_present_with_buckets_and_formula() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert "v_nps" in ddls
    sql = ddls["v_nps"]
    assert "`proj.ds.v_nps`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "COUNTIF(nps_score >= 9)" in sql  # promoters
    assert "COUNTIF(nps_score BETWEEN 7 AND 8)" in sql  # passives
    assert "nps_score <= 6" in sql  # detractors
    assert "SAFE_DIVIDE" in sql and "* 100 AS nps" in sql


def test_conversations_schema_has_dimension_columns() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    for col in [
        "division",
        "category",
        "subcategory",
        "agent_id",
        "pic",
        "department",
        "sla_minutes",
        "sla_deadline",
        "first_response_at",
        "resolved_at",
        "reopen_count",
    ]:
        assert col in names, col
    # new columns must be nullable
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert by_name["division"].mode in ("NULLABLE", "")


def test_view_ddls_include_report_views() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    for name in [
        "v_volume_by_division",
        "v_dept_pic_performance",
        "v_sla_achievement",
        "v_reopen_rate",
        "v_resolution_time",
        "v_nps_by_agent",
        "v_volume_daily",
        "v_volume_weekly",
    ]:
        assert name in ddls
        assert name in ddls[name]  # DDL creates the view of that name
    assert "division" in ddls["v_volume_by_division"]
    assert "reopen_count" in ddls["v_reopen_rate"]


def test_v_peak_hours_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_peak_hours"]
    assert "`proj.ds.v_peak_hours`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "EXTRACT(HOUR" in sql
    assert "EXTRACT(DAYOFWEEK" in sql
    assert "volume" in sql


def test_v_complaint_type_ranking_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_complaint_type_ranking"]
    assert "`proj.ds.v_complaint_type_ranking`" in sql
    assert "category" in sql and "subcategory" in sql
    assert "COUNT(*)" in sql
    assert "ORDER BY" in sql


def test_v_tasks_per_agent_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_tasks_per_agent"]
    assert "`proj.ds.v_tasks_per_agent`" in sql
    assert "agent_id" in sql
    assert "COUNT(*) AS cases" in sql
    assert "avg_first_response_min" in sql


def test_v_first_response_by_channel_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_first_response_by_channel"]
    assert "`proj.ds.v_first_response_by_channel`" in sql
    assert "channel" in sql
    assert "avg_first_response_min" in sql
    assert "first_response_at" in sql


def test_v_case_lifecycle_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_case_lifecycle"]
    assert "`proj.ds.v_case_lifecycle`" in sql
    assert "created_at" in sql
    assert "resolved_at" in sql
    assert "resolution_minutes" in sql


def test_v_state_trend_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_state_trend"]
    assert "`proj.ds.v_state_trend`" in sql
    assert "status" in sql
    assert "FORMAT_DATE" in sql
    assert "COUNT(*)" in sql
    # Task 2 (Package E): `month_start` is no longer *filtered* on -- the
    # period path routes through v_state_trend_daily and `_query_block`'s
    # date_column kwarg was deleted (final fix, finding M1). The column
    # stays, and stays pinned here, because StateTrendRow declares it and
    # export.py emits it: a drifted DDL that dropped it would silently
    # blank an exported column with green tests.
    assert "AS month_start" in sql
    assert "GROUP BY month, month_start, status, division" in sql


def test_v_reopen_rate_includes_dealer() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_reopen_rate"]
    # existing view must now also group by dealer
    assert "dealer" in sql


def test_schema_dealer_field_is_nullable_string() -> None:
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert "dealer" in by_name
    assert by_name["dealer"].field_type == "STRING"
    assert by_name["dealer"].mode in ("NULLABLE", "")


def test_schema_has_case_type_and_vehicle_model_fields() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    assert "case_type" in names
    assert "vehicle_model" in names


def test_schema_has_working_minutes_fields() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    assert "first_response_working_minutes" in names
    assert "resolution_working_minutes" in names


def test_view_ddls_requires_sla_targets_and_creates_bucket_view() -> None:
    targets = '{"complaint": {"buckets_wh": [24, 48, 72], "labels": ["<24wh", "24-48wh", "48-72wh", ">72wh"]}}'
    ddls = view_ddls("proj", "ds", "conversations", targets)
    assert "v_resolution_sla_buckets" in ddls
    ddl = ddls["v_resolution_sla_buckets"]
    assert "resolution_working_minutes" in ddl
    assert "1440" in ddl  # 24wh * 60 minutes
    assert "case_type" in ddl


def test_view_ddls_malformed_sla_targets_yields_view_with_no_case_types() -> None:
    ddls = view_ddls("proj", "ds", "conversations", "{not valid json")
    assert "v_resolution_sla_buckets" in ddls  # view still created, just matches nothing


def test_sla_bucket_case_sql_skips_case_type_with_non_numeric_bucket_edge() -> None:
    # Valid top-level JSON, but "complaint"'s buckets_wh has a non-numeric entry
    # (a plausible operator typo, e.g. "8hr" instead of 8). This must not raise
    # -- the malformed case_type is simply excluded, same as a length-mismatch.
    targets = (
        '{"complaint": {"buckets_wh": ["not-a-number"], "labels": ["a", "b"]}, '
        '"inquiry": {"buckets_wh": [8], "labels": ["Within 8wh", ">8wh"]}}'
    )
    ddls = view_ddls("proj", "ds", "conversations", targets)  # must not raise
    ddl = ddls["v_resolution_sla_buckets"]
    assert "complaint" not in ddl  # malformed case_type excluded entirely
    assert "inquiry" in ddl  # sibling valid case_type still buckets normally
    assert "480" in ddl  # 8wh * 60 minutes, from the still-valid "inquiry" entry


def test_view_ddls_includes_dealer_escalation_and_case_aging() -> None:
    ddls = view_ddls("proj", "ds", "conversations", "{}")
    assert "v_dealer_escalation" in ddls
    assert "dealer_escalated_at" in ddls["v_dealer_escalation"]
    assert "v_dealer_escalation_slowest_cases" in ddls
    assert "conversation_id" in ddls["v_dealer_escalation_slowest_cases"]
    assert "v_case_aging" in ddls
    assert "bucket_label" in ddls["v_case_aging"]


def test_view_ddls_includes_volume_and_category_cross_tabs() -> None:
    ddls = view_ddls("proj", "ds", "conversations", "{}")
    assert "v_volume_by_type_division" in ddls
    assert "case_type" in ddls["v_volume_by_type_division"]
    assert "v_category_by_vehicle_model" in ddls
    assert "vehicle_model" in ddls["v_category_by_vehicle_model"]
    # Task 2 (Package E): same as v_state_trend above -- not filtered on
    # any more, but declared on the row type and exported, so still pinned.
    assert "AS month_start" in ddls["v_volume_by_type_division"]
    assert (
        "GROUP BY month, month_start, channel, case_type, division"
        in ddls["v_volume_by_type_division"]
    )


def test_day_grain_views_document_their_utc_bucketing() -> None:
    """Package E final fix, finding I4. `DATE(created_at)` on a TIMESTAMP
    is a UTC calendar day in BigQuery, while the Weekly Report picker
    builds its window from browser-local dates -- an 8-hour disagreement
    at both edges for a UTC+8 tenant. The semantics are deliberately
    unchanged (re-bucketing every historical figure in one deploy is worse
    than a known, documented offset); what must not happen is the
    disagreement being rediscovered at the acceptance gate as an
    unexplained mismatch. Pin the note so a future edit can't quietly
    drop it."""
    assert bigquery_schema.__doc__ is not None
    assert "UTC calendar day" in bigquery_schema.__doc__
    # and the views the note is about still exist to be read alongside it
    ddls = view_ddls("proj", "ds", "conversations")
    for view in ("v_volume_daily", "v_state_trend_daily", "v_volume_by_type_division_daily"):
        assert "DATE(created_at) AS day" in ddls[view]
