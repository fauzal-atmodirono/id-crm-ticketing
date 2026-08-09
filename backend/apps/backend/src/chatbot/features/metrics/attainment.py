"""Compare a measured value against a target. Four outcomes, not two.

The fourth state is the reason this module exists. A metric that **cannot be
measured** has not failed its target, and rendering it as a miss is a false
claim about performance made to the client on their headline slide.

The specific case: PRO-NET's control-item page has an abandon-rate row. There
is no call queue instrumented, so there is nothing to abandon and nothing to
count. `0%` would read as "we abandon no calls" -- excellent performance, and
untrue. `no_data` reads as "not measured", which is what is actually the case.

So `0` and `None` must never collapse into the same thing:

    evaluate(0.0,  target)  -> met/missed, 0 is a real measurement
    evaluate(None, target)  -> no_data,    there was nothing to measure

Pure: no I/O, no clock. `Target` comes from the targets store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["met", "missed", "no_data", "no_target"]

# `lte`: lower is better (response minutes, abandon rate).
# `gte`: higher is better (attainment rate, CSAT).
Comparator = Literal["lte", "gte"]


@dataclass(frozen=True)
class Attainment:
    status: Status
    actual: float | None
    target: float | None
    variance: float | None
    unit: str | None = None

    @property
    def is_failure(self) -> bool:
        """Only `missed` is a failure. Named, because `status != "met"` is the
        bug this module exists to prevent -- it sweeps `no_data` and
        `no_target` into the failure bucket."""
        return self.status == "missed"


def evaluate(actual: float | None, target: object | None) -> Attainment:
    """Compare `actual` against `target`.

    `no_data` when there is no measurement, `no_target` when nothing has been
    configured to compare against. Neither is a miss, and neither becomes one
    by omission: both are returned explicitly so a caller has to decide what to
    render rather than defaulting into "failed".

    Equality counts as met for both comparators -- a target of "within 2 hours"
    is met at exactly 2 hours, not missed by a rounding error.
    """
    if target is None:
        return Attainment("no_target", actual, None, None)

    target_value = getattr(target, "value", None)
    comparator = getattr(target, "comparator", "lte")
    unit = getattr(target, "unit", None)
    attainment_pct = getattr(target, "attainment_pct", None)

    if target_value is None:
        return Attainment("no_target", actual, None, None, unit)
    if actual is None:
        # NOT a miss. See the module docstring.
        return Attainment("no_data", None, float(target_value), None, unit)

    # An attainment-pct target compares the PERCENTAGE meeting the threshold,
    # not the raw value: "90% of cases within 2 hours" is a different question
    # from "the average case is within 2 hours", and comparing the raw value
    # against 90 would be nonsense.
    compare_against = float(attainment_pct) if attainment_pct is not None else float(target_value)

    actual_value = float(actual)
    # Signed, and in the direction the reader expects: positive means the
    # actual is above the target, whichever comparator applies. A slide can
    # render the arrow from this without knowing the comparator.
    variance = actual_value - compare_against

    if comparator == "gte":
        met = actual_value >= compare_against
    else:
        met = actual_value <= compare_against

    return Attainment(
        "met" if met else "missed", actual_value, compare_against, variance, unit
    )
