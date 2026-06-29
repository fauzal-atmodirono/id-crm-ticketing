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
        "synced_at",
    }


def test_view_ddls_keys_and_targets() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert set(ddls) == {"v_volume_by_month_channel", "v_resolution_split", "v_csat"}
    assert "`proj.ds.v_volume_by_month_channel`" in ddls["v_volume_by_month_channel"]
    assert "`proj.ds.conversations`" in ddls["v_volume_by_month_channel"]


def test_view_ddls_contain_expected_aggregates() -> None:
    ddls = view_ddls("proj", "ds")
    assert "COUNT(*)" in ddls["v_volume_by_month_channel"]
    assert "resolved_by='bot'" in ddls["v_resolution_split"].replace('"', "'")
    assert "transfer_to_agent" in ddls["v_resolution_split"]
    assert "satisfied_rate" in ddls["v_csat"]
    assert "csat_score >= 4" in ddls["v_csat"].replace(">=4", ">= 4")
