from chatbot.platform.config import Settings


def test_metrics_defaults_are_noop() -> None:
    s = Settings()
    assert s.metrics_provider == "noop"
    assert s.bigquery_turn_events_table == "turn_events"
