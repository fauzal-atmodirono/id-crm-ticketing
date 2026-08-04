from chatbot.platform.config import Settings


def test_metrics_defaults_are_noop() -> None:
    s = Settings()
    assert s.metrics_provider == "noop"
    assert s.bigquery_turn_events_table == "turn_events"


def test_metrics_exclude_demo_seed_defaults_off() -> None:
    """Default-off: demo-seeded conversations sync unchanged unless an
    operator explicitly opts in via METRICS_EXCLUDE_DEMO_SEED."""
    assert Settings().metrics_exclude_demo_seed is False
