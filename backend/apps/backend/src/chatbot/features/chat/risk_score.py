"""Case risk score (§2.1.3) — a weighted sum, on purpose.

Prioritisation only helps if the operator trusts it, and they cannot trust a
number they cannot interrogate. So this is a transparent weighted sum whose
parts are individually reportable via `contributions()`: a case scored 82
because SLA proximity contributed 35, it is a complaint (20), it has been
reopened twice (18) and escalated once (9).

Deliberately NOT a model. There is no labelled outcome data in this system to
train one on -- no record of which cases actually went wrong -- so a learned
scorer would be fitting noise and would cost the explainability that makes the
score usable. Revisit when outcomes are being recorded.

Pure: no I/O, no clock, no config lookup. The weights come in as an argument so
a tenant can retune priorities without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass

# Each weight is the maximum points that signal can contribute. They sum to 100
# so a "everything is as bad as it gets" case scores exactly 100 and the
# headline number needs no rescaling to be read as a percentage.
DEFAULT_WEIGHTS: dict[str, float] = {
    "case_type": 20.0,
    "sla_proximity": 35.0,
    "reopen": 20.0,
    "escalation_depth": 15.0,
    "sentiment": 10.0,
}

# Reopens and escalations past these counts stop adding points. Beyond a
# handful the case is already maximally urgent, and letting them accumulate
# would let one pathological case dominate every queue it appears in.
_REOPEN_CAP = 3
_DEPTH_CAP = 2

_ELEVATED_CASE_TYPES = {"complaint", "compliment & feedback"}


@dataclass(frozen=True)
class RiskSignals:
    """What we know about a case. Every field is optional: this is scored on
    live conversations where most attributes are simply unset, and an absent
    signal must contribute nothing rather than a default."""

    case_type: str | None = None
    # 0.0 = just arrived, 1.0 = exactly at the SLA deadline, >1.0 = breached.
    sla_fraction_elapsed: float | None = None
    reopen_count: int | None = None
    escalation_depth: int | None = None
    # P7 will supply this; nothing writes sentiment today, so it stays inert.
    negative_sentiment: bool | None = None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _valid_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Fall back to the defaults on any malformed map.

    A bad config value must not take scoring down with it -- an unscored queue
    is worse than a queue scored on default weights.
    """
    if not weights:
        return DEFAULT_WEIGHTS
    try:
        return {key: float(weights.get(key, DEFAULT_WEIGHTS[key])) for key in DEFAULT_WEIGHTS}
    except (TypeError, ValueError):
        return DEFAULT_WEIGHTS


def contributions(
    signals: RiskSignals, weights: dict[str, float] | None = None
) -> dict[str, float]:
    """Points contributed by each signal. Sums to `score()`.

    This is the point of the whole module: the operator is told which signals
    drove the number, in the same units as the number.
    """
    w = _valid_weights(weights)

    case_type = (signals.case_type or "").strip().lower()
    type_fraction = 1.0 if case_type in _ELEVATED_CASE_TYPES else 0.0

    sla_fraction = (
        _clamp(float(signals.sla_fraction_elapsed))
        if signals.sla_fraction_elapsed is not None
        else 0.0
    )

    reopens = max(0, int(signals.reopen_count or 0))
    reopen_fraction = _clamp(reopens / _REOPEN_CAP)

    depth = max(0, int(signals.escalation_depth or 0))
    depth_fraction = _clamp(depth / _DEPTH_CAP)

    sentiment_fraction = 1.0 if signals.negative_sentiment else 0.0

    return {
        "case_type": w["case_type"] * type_fraction,
        "sla_proximity": w["sla_proximity"] * sla_fraction,
        "reopen": w["reopen"] * reopen_fraction,
        "escalation_depth": w["escalation_depth"] * depth_fraction,
        "sentiment": w["sentiment"] * sentiment_fraction,
    }


def score(signals: RiskSignals, weights: dict[str, float] | None = None) -> int:
    """Risk in 0-100. Deterministic, and always equal to the sum of
    `contributions()` so the explanation explains the number."""
    total = sum(contributions(signals, weights).values())
    return int(round(_clamp(total, 0.0, 100.0)))
