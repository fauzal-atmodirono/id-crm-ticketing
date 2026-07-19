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
