"""Pure channel-volume anomaly flagging over baseline stats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import AnomalyRow


@dataclass(frozen=True)
class Anomaly:
    channel: str
    current_volume: int
    baseline_mean: float
    z_score: float


def flag_anomalies(rows: list[AnomalyRow], k: float, min_baseline: int) -> list[Anomaly]:
    out: list[Anomaly] = []
    for r in rows:
        if r.baseline_stddev is None or r.baseline_stddev <= 0 or r.baseline_mean is None:
            continue
        if r.baseline_mean < min_baseline:
            continue
        z = (r.current_volume - r.baseline_mean) / r.baseline_stddev
        if z > k:
            out.append(Anomaly(r.channel, r.current_volume, r.baseline_mean, z))
    return out
