from chatbot.platform.config import Settings


def test_qa_defaults() -> None:
    s = Settings()
    assert s.qa_provider == "noop"
    assert s.bigquery_qa_labels_table == "qa_labels"
    assert s.qa_api_key == ""
