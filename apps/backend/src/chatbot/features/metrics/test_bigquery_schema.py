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
    }


def test_view_ddls_keys_and_targets() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert set(ddls) == {"v_volume_by_month_channel", "v_resolution_split", "v_csat", "v_nps"}
    assert "`proj.ds.v_volume_by_month_channel`" in ddls["v_volume_by_month_channel"]
    assert "`proj.ds.conversations`" in ddls["v_volume_by_month_channel"]


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
