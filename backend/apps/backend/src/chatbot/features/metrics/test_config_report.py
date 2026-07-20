from chatbot.platform.config import Settings


def test_report_defaults_are_safe() -> None:
    s = Settings()
    assert s.report_enabled is False
    assert s.smtp_port == 587
    assert s.anomaly_zscore_k == 3.0
    assert s.anomaly_min_baseline == 20


def test_report_recipient_list_splits_and_strips() -> None:
    s = Settings(report_recipients="a@x.com, b@y.com ,")
    assert s.report_recipient_list() == ["a@x.com", "b@y.com"]
