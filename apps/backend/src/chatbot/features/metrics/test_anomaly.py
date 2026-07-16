from chatbot.features.metrics.anomaly import flag_anomalies
from chatbot.features.metrics.query_port import AnomalyRow


def test_flags_spike_above_threshold() -> None:
    rows = [AnomalyRow("web", current_volume=200, baseline_mean=100.0, baseline_stddev=20.0)]
    out = flag_anomalies(rows, k=3.0, min_baseline=20)
    assert len(out) == 1 and out[0].channel == "web"
    assert out[0].z_score == 5.0


def test_ignores_within_threshold() -> None:
    rows = [AnomalyRow("web", current_volume=140, baseline_mean=100.0, baseline_stddev=20.0)]
    assert flag_anomalies(rows, k=3.0, min_baseline=20) == []


def test_ignores_low_baseline() -> None:
    rows = [AnomalyRow("web", current_volume=999, baseline_mean=5.0, baseline_stddev=1.0)]
    assert flag_anomalies(rows, k=3.0, min_baseline=20) == []


def test_ignores_zero_or_missing_stddev() -> None:
    rows = [
        AnomalyRow("web", 200, 100.0, 0.0),
        AnomalyRow("wa", 200, 100.0, None),
    ]
    assert flag_anomalies(rows, k=3.0, min_baseline=20) == []
