from chatbot.platform.config import get_settings


def test_bigquery_settings_defaults() -> None:
    s = get_settings()
    assert s.bigquery_project_id == "lv-playground-genai"
    assert s.bigquery_dataset == "demo_proton"
    assert s.bigquery_conversations_table == "conversations"
