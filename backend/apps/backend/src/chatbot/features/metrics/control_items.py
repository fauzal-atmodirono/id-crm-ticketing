"""The fourteen rows of PRO-NET's control-item page (C1 p48).

This is the slide the client reads first, so what it does with a metric it
cannot measure matters more than what it does with one it can.

**Nine rows have a source. Five do not.** The five report `no_data` with a
client-facing reason, never `0`, and never `missed`. The abandon-rate row is
the specific case worth stating: there is no call queue instrumented, so there
is nothing to abandon and nothing to count. Rendering `0%` would claim we
abandon no calls -- excellent performance, and false. `no_data` says "not
measured", which is what is true.

Each spec names its source view and target key, or -- where absent -- the
reason, in the sentence the client should read rather than a code reference.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlItemSpec:
    number: int
    label: str
    # None when nothing can measure this yet.
    source_view: str | None
    metric_field: str | None
    target_key: str | None
    # Client-facing. Required whenever source_view is None.
    blocking_reason: str | None = None

    @property
    def measurable(self) -> bool:
        return self.source_view is not None


# Order is the client's, not ours -- the slide is read row by row against the
# printed original.
CONTROL_ITEMS: list[ControlItemSpec] = [
    ControlItemSpec(1, "Total cases received", "v_volume_by_month_channel", "volume", None),
    ControlItemSpec(2, "Cases resolved", "v_resolution_split", "total", None),
    ControlItemSpec(
        3, "First response within target", "v_first_response_by_channel",
        "avg_first_response_min", "first_response",
    ),
    ControlItemSpec(
        4, "Resolution within target", "v_resolution_sla_buckets", "cases", "resolution",
    ),
    ControlItemSpec(
        5, "Reopen rate", "v_reopen_rate", "reopen_rate", "reopen_rate",
    ),
    ControlItemSpec(
        6, "CSAT", "v_csat", "avg_score", "csat",
    ),
    ControlItemSpec(
        7, "Complaint resolution attainment", "v_resolution_sla_buckets", "cases",
        "resolution_complaint",
    ),
    ControlItemSpec(
        8, "Inquiry resolution attainment", "v_resolution_sla_buckets", "cases",
        "resolution_inquiry",
    ),
    ControlItemSpec(
        9, "Dealer escalation turnaround", "v_dealer_escalation",
        "avg_turnaround_days", "dealer_turnaround",
    ),
    # --- the five with no source -------------------------------------------
    ControlItemSpec(
        10, "Call abandon rate", None, None, "abandon_rate",
        blocking_reason=(
            "No call queue is instrumented, so there is no queue to abandon and "
            "nothing to count. This is not a zero-abandon result; it is not "
            "measured (gap R9)."
        ),
    ),
    ControlItemSpec(
        11, "Average speed of answer", None, None, "speed_of_answer",
        blocking_reason=(
            "Answer time is a call-queue measurement and no queue exists yet "
            "(gap R9)."
        ),
    ),
    ControlItemSpec(
        12, "Service level (calls answered in N seconds)", None, None, "service_level",
        blocking_reason=(
            "Service level is measured against a call queue, which is not yet "
            "instrumented (gap R9)."
        ),
    ),
    ControlItemSpec(
        13, "Calls offered", None, None, None,
        blocking_reason=(
            "Offered-call volume comes from the telephony platform's queue "
            "statistics, which are not yet integrated (gap R9)."
        ),
    ),
    ControlItemSpec(
        14, "Escalations to HQ", None, None, None,
        blocking_reason=(
            "What counts as an HQ escalation is not yet defined in the case "
            "model (client question Q5), so this is unclassified rather than "
            "zero."
        ),
    ),
]

MEASURABLE_ITEMS = [item for item in CONTROL_ITEMS if item.measurable]
UNMEASURABLE_ITEMS = [item for item in CONTROL_ITEMS if not item.measurable]
