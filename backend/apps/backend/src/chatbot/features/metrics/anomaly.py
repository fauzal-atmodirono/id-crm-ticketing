"""Pure channel-volume anomaly flagging over baseline stats.

Two grains, two independent thresholds, one shared shape:

- **Daily** (`flag_anomalies`, `anomaly_zscore_k` = 3.0,
  `anomaly_min_baseline` = 20) over `v_channel_anomaly`. Unchanged since it
  shipped; the deployed anomaly page and the report scheduler both read it.
- **Hourly** (`evaluate_hourly_anomalies` / `flag_hourly_anomalies`,
  `anomaly_hourly_zscore_k` = 3.5, `anomaly_hourly_min_baseline` = 5) over
  `v_channel_anomaly_hourly`, added by P9 task 4 as a strictly additive path.

The hourly threshold is deliberately LOOSER than the daily one. An hour holds
a fraction of a day's conversations, so its bucket-to-bucket variance is much
larger; reusing 3.0 at hourly grain produces a stream of detections nobody can
act on, and the first thing an operator does with an alert they cannot act on
is switch it off -- taking the daily detections with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import AnomalyRow, HourlyAnomalyRow


@dataclass(frozen=True)
class Anomaly:
    channel: str
    current_volume: int
    baseline_mean: float
    z_score: float


def flag_anomalies(rows: list[AnomalyRow], k: float, min_baseline: int) -> list[Anomaly]:
    """Daily-grain detection. **Do not change this function.**

    It is the one the deployed `ProtonAnomaly.vue` page and
    `scheduler.run_report_job`'s anomaly email both go through, at
    `anomaly_zscore_k`/`anomaly_min_baseline`. P9 task 4 added the hourly grain
    below as a sibling for exactly that reason -- widening this one to serve
    both grains would have meant one threshold across two very different noise
    profiles, which is either a flood at hourly or blindness at daily.
    """
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


# ---------------------------------------------------------------------------
# P9 task 4 -- hourly grain
# ---------------------------------------------------------------------------

# Every evaluated hour gets exactly one of these. Three of the four are NOT
# "flagged", and they are three separate values rather than one falsy verdict
# because a dashboard has to distinguish "we looked and it was fine" from "we
# could not look" -- the same reason P5 has `no_data` and `v_csat_by_agent` has
# `is_rankable`. Collapsing them turns an honest uncertainty into a false
# assurance.
HOURLY_STATUS_FLAGGED = "flagged"
HOURLY_STATUS_NORMAL = "normal"
# Suppressed by the minimum-volume floor. NOT "normal": nothing was concluded
# about this hour, there simply was not enough of it to conclude anything from.
HOURLY_STATUS_INSUFFICIENT_VOLUME = "insufficient_volume"
# No usable baseline: a NULL or zero standard deviation, i.e. fewer than two
# days of same-hour history or every one of them identical. Also not "normal".
HOURLY_STATUS_NO_BASELINE = "no_baseline"


@dataclass(frozen=True)
class HourlyAnomaly:
    """One evaluated (channel, hour-of-day) bucket.

    `z_score` is None whenever `status` is `no_baseline` -- there is nothing to
    divide by, and 0.0 would read as "dead average" rather than "unknown".
    `min_baseline` travels on the result so the page can say *which* floor
    suppressed the hour rather than printing a hardcoded number.
    """

    channel: str
    hour_of_day: int
    current_volume: int
    baseline_mean: float | None
    baseline_stddev: float | None
    baseline_days: int
    z_score: float | None
    status: str
    min_baseline: int


def evaluate_hourly_anomalies(
    rows: list[HourlyAnomalyRow], k: float, min_baseline: int
) -> list[HourlyAnomaly]:
    """Classify EVERY hour in `rows`, not only the anomalous ones.

    Two properties here are the whole design, and both look like details a
    later simplification would remove:

    **1. The baseline is the same hour across preceding days, never the
    trailing hours of today.** This function does not choose the baseline --
    `v_channel_anomaly_hourly` does, by grouping its `base` CTE on
    `(channel, hour_of_day)` over the preceding `HOURLY_BASELINE_DAYS` days and
    joining `USING (channel, hour_of_day)`. A trailing-hours baseline is the
    obvious thing to build and it flags every lunchtime dip and every morning
    ramp, because those are the shape of a normal day rather than a deviation
    from it. A detector that fires on the normal daily shape gets muted, and a
    muted detector never fires again on the thing it was built for.

    **2. The minimum-volume floor is mandatory, not a refinement.** An hour
    whose baseline is below `min_baseline` is never flagged however large its
    deviation looks, because at 03:00 a channel's baseline may be 0.3 cases and
    two cases is then a z-score of 6 -- without the floor this detector alerts
    every night, on nothing. Note the floor is applied to the BASELINE only:
    flagging is upward-only (`z > k` requires `current_volume >
    baseline_mean`), so `baseline_mean >= min_baseline` already implies
    `current_volume > min_baseline` for anything that could be flagged. A
    second test against `current_volume` would be unreachable code, not extra
    safety.

    Returns one `HourlyAnomaly` per input row, in input order. Callers that
    only want the detections use `flag_hourly_anomalies`; the anomaly page
    wants all of them, so the suppressed hours can be labelled on screen.
    """
    out: list[HourlyAnomaly] = []
    for r in rows:
        mean, stddev = r.baseline_mean, r.baseline_stddev
        if mean is None or stddev is None or stddev <= 0:
            status, z = HOURLY_STATUS_NO_BASELINE, None
        elif mean < min_baseline:
            # Floor first, threshold second. The other order computes a z-score
            # of 6 for a 03:00 baseline of 0.3 and then has to remember to
            # throw it away.
            status, z = HOURLY_STATUS_INSUFFICIENT_VOLUME, (r.current_volume - mean) / stddev
        else:
            z = (r.current_volume - mean) / stddev
            status = HOURLY_STATUS_FLAGGED if z > k else HOURLY_STATUS_NORMAL
        out.append(
            HourlyAnomaly(
                channel=r.channel,
                hour_of_day=r.hour_of_day,
                current_volume=r.current_volume,
                baseline_mean=mean,
                baseline_stddev=stddev,
                baseline_days=r.baseline_days,
                z_score=z,
                status=status,
                min_baseline=min_baseline,
            )
        )
    return out


def flag_hourly_anomalies(
    rows: list[HourlyAnomalyRow], k: float, min_baseline: int
) -> list[HourlyAnomaly]:
    """Only the hours that are genuine detections -- the push/alert path."""
    return [
        h
        for h in evaluate_hourly_anomalies(rows, k, min_baseline)
        if h.status == HOURLY_STATUS_FLAGGED
    ]
