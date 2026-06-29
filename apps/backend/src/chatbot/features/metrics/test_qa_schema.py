from chatbot.features.metrics.qa_schema import QA_LABELS_SCHEMA, qa_view_ddls


def test_schema_field_names_and_order() -> None:
    assert [f.name for f in QA_LABELS_SCHEMA] == [
        "conversation_id",
        "accuracy",
        "quality",
        "reviewer",
        "notes",
        "labeled_at",
    ]


def test_schema_types() -> None:
    by_name = {f.name: f for f in QA_LABELS_SCHEMA}
    assert by_name["conversation_id"].field_type == "STRING"
    assert by_name["accuracy"].field_type == "INT64"
    assert by_name["quality"].field_type == "INT64"
    assert by_name["labeled_at"].field_type == "TIMESTAMP"


def test_v_quality_view_joins_conversations_with_avgs() -> None:
    ddls = qa_view_ddls("proj", "ds", "qa_labels", "conversations")
    assert set(ddls) == {"v_quality"}
    sql = ddls["v_quality"]
    assert "`proj.ds.v_quality`" in sql
    assert "`proj.ds.qa_labels`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "USING (conversation_id)" in sql
    assert "AVG(q.accuracy) AS avg_accuracy" in sql
    assert "AVG(q.quality) AS avg_quality" in sql
    assert "GROUP BY c.channel" in sql
