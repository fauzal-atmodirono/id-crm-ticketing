from chatbot.features.metrics.faq_schema import FAQ_FEEDBACK_SCHEMA, faq_view_ddls


def test_schema_columns() -> None:
    names = {f.name for f in FAQ_FEEDBACK_SCHEMA}
    assert names == {"article_id", "session_id", "helpful", "score", "at"}


def test_faq_quality_view() -> None:
    ddls = faq_view_ddls("p", "d", "faq_feedback")
    assert "v_faq_quality" in ddls
    sql = ddls["v_faq_quality"]
    assert "article_id" in sql and "helpful_rate" in sql and "avg_score" in sql
