from chatbot.features.metrics.qa_schema import QA_LABELS_SCHEMA, qa_view_ddls


def test_schema_field_names_and_order() -> None:
    assert [f.name for f in QA_LABELS_SCHEMA] == [
        "conversation_id",
        "accuracy",
        "quality",
        "reviewer",
        "notes",
        "labeled_at",
        "channel",
        "rubric_greeting",
        "rubric_identification",
        "rubric_resolution",
        "rubric_closing",
        "rubric_compliance",
        "call_qa_percentage",
    ]


def test_schema_types() -> None:
    by_name = {f.name: f for f in QA_LABELS_SCHEMA}
    assert by_name["conversation_id"].field_type == "STRING"
    assert by_name["accuracy"].field_type == "INT64"
    assert by_name["quality"].field_type == "INT64"
    assert by_name["labeled_at"].field_type == "TIMESTAMP"
    assert by_name["channel"].field_type == "STRING"
    assert by_name["rubric_greeting"].field_type == "BOOL"
    assert by_name["rubric_identification"].field_type == "BOOL"
    assert by_name["rubric_resolution"].field_type == "BOOL"
    assert by_name["rubric_closing"].field_type == "BOOL"
    assert by_name["rubric_compliance"].field_type == "BOOL"
    assert by_name["call_qa_percentage"].field_type == "FLOAT64"
    # P8 task 7: every new column is nullable -- a pre-existing channel-agnostic
    # row (mode "NULLABLE" is the bigquery.SchemaField default) must stay valid.
    for name in (
        "channel",
        "rubric_greeting",
        "rubric_identification",
        "rubric_resolution",
        "rubric_closing",
        "rubric_compliance",
        "call_qa_percentage",
    ):
        assert by_name[name].mode == "NULLABLE"


def test_v_quality_view_joins_conversations_with_avgs() -> None:
    ddls = qa_view_ddls("proj", "ds", "qa_labels", "conversations")
    assert set(ddls) == {"v_quality", "v_call_qa"}
    sql = ddls["v_quality"]
    assert "`proj.ds.v_quality`" in sql
    assert "`proj.ds.qa_labels`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "USING (conversation_id)" in sql
    assert "AVG(q.accuracy) AS avg_accuracy" in sql
    assert "AVG(q.quality) AS avg_quality" in sql
    assert "GROUP BY c.channel" in sql


def test_v_call_qa_view_present_with_denominator_and_percentage() -> None:
    ddls = qa_view_ddls("proj", "ds", "qa_labels", "conversations")
    sql = ddls["v_call_qa"]
    assert "`proj.ds.v_call_qa`" in sql
    assert "`proj.ds.qa_labels`" in sql
    # Every rate metric returns its denominator: calls_reviewed (everyone
    # reviewed, complete or not) AND calls_scored (only the complete ones)
    # sit alongside the average, never just the average alone.
    assert "COUNT(*) AS calls_reviewed" in sql
    assert "COUNTIF(call_qa_percentage IS NOT NULL) AS calls_scored" in sql
    assert "AVG(call_qa_percentage) AS avg_call_qa_percentage" in sql
    assert "WHERE channel = 'Phone'" in sql
