"""Proton's five-step dealer escalation matrix, as data.

The SOP (`docs/client-materials/CRM Process Flow (1).xlsx`, Email tab) sets
out a ladder: escalate to the dealer, wait, remind the Principal, wait,
remind the Owner, then telephone. Each rung addresses a DIFFERENT person and
CCs a widening group, on working-hour timers.

Encoded as a table rather than as branching logic for two reasons. Proton's
own document already labels step 5 "NEW PROCESS", so the ladder has changed
once and will change again; and the timers are exactly the kind of thing an
operator should retune without an engineer editing conditionals -- the same
argument that made escalation routing and SLA policies operator-editable.

Pure: no I/O, no clock, no store. It answers two questions -- which step is
due, and who does that step address -- and the sweep
(`escalation_ladder.py`) does everything else.

Delays are measured in WORKING hours from the step-1 send, cumulatively, as
the SOP states them ("4 working hours after step 1", "4 working hours after
step 3", "cumulative 8 working hours"). Cumulative rather than per-step
because a ladder that measured each gap separately would drift every time a
sweep ran late.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.pic_store import DEALER_ROLES, PRONET_ROLES

if TYPE_CHECKING:
    from chatbot.features.chat.pic_store import DealerRecord, ProtonNetRecord

_log = structlog.get_logger(__name__)

EMAIL = "email"
PHONE = "phone"


@dataclass(frozen=True)
class EscalationStep:
    step_no: int
    # Working hours since the step-1 send at which this step becomes due.
    delay_working_hours: float
    to_roles: tuple[str, ...]
    cc_roles: tuple[str, ...]
    # Short label that goes in the subject, e.g. "1ST REMINDER". The step-1
    # label is empty: it is the escalation itself, not a reminder about one.
    label: str
    channel: str = EMAIL


# The SOP's matrix verbatim. Step 2 is the dealer's own acknowledgement
# window -- it is not something we send, so it has no recipients and the
# ladder never "fires" it; it is kept in the table because the 2-working-hour
# expectation is part of the policy an operator retunes, and because dropping
# it would renumber every step away from the numbers Proton uses in writing.
DEFAULT_STEPS: tuple[EscalationStep, ...] = (
    EscalationStep(
        step_no=1,
        delay_working_hours=0.0,
        to_roles=("cre", "sales_aftersales_mgr"),
        cc_roles=("principal", "area_regional_mgr", "hod"),
        label="",
    ),
    EscalationStep(
        step_no=2,
        delay_working_hours=2.0,
        to_roles=(),
        cc_roles=(),
        label="ACKNOWLEDGEMENT DUE",
    ),
    EscalationStep(
        step_no=3,
        delay_working_hours=4.0,
        to_roles=("principal",),
        cc_roles=("owner", "sales_aftersales_mgr", "cre", "area_regional_mgr", "hod"),
        label="1ST REMINDER",
    ),
    EscalationStep(
        step_no=4,
        delay_working_hours=8.0,
        to_roles=("owner",),
        cc_roles=("principal", "sales_aftersales_mgr", "cre", "area_regional_mgr", "hod"),
        label="2ND REMINDER",
    ),
    EscalationStep(
        step_no=5,
        delay_working_hours=8.0,
        to_roles=("principal", "owner"),
        cc_roles=("sales_aftersales_mgr", "cre", "area_regional_mgr", "hod"),
        label="FINAL ESCALATION - TELEPHONE",
        channel=PHONE,
    ),
)

_ALL_ROLES = set(DEALER_ROLES) | set(PRONET_ROLES)


def load_steps(raw_json: str) -> tuple[EscalationStep, ...]:
    """Parse an operator's step table, falling back to the SOP default.

    Fail-open in the strong sense: malformed JSON, a non-list, a row missing
    ``step_no``, or an unknown role name all yield DEFAULT_STEPS rather than
    a partial ladder. A half-parsed table is the dangerous outcome here --
    it would silently drop a rung, and the rung most likely to be dropped is
    the one an operator was editing.
    """
    if not raw_json or not raw_json.strip():
        return DEFAULT_STEPS

    try:
        rows = json.loads(raw_json)
        if not isinstance(rows, list) or not rows:
            raise ValueError("step table must be a non-empty list")

        steps: list[EscalationStep] = []
        for row in rows:
            to_roles = tuple(str(r) for r in row.get("to_roles", ()))
            cc_roles = tuple(str(r) for r in row.get("cc_roles", ()))
            unknown = (set(to_roles) | set(cc_roles)) - _ALL_ROLES
            if unknown:
                raise ValueError(f"unknown role(s): {sorted(unknown)}")
            channel = str(row.get("channel", EMAIL))
            if channel not in (EMAIL, PHONE):
                raise ValueError(f"unknown channel: {channel}")
            steps.append(
                EscalationStep(
                    step_no=int(row["step_no"]),
                    delay_working_hours=float(row["delay_working_hours"]),
                    to_roles=to_roles,
                    cc_roles=cc_roles,
                    label=str(row.get("label", "")),
                    channel=channel,
                )
            )
        return tuple(sorted(steps, key=lambda s: s.step_no))
    except Exception as exc:
        _log.warning("escalation_policy_steps_json_invalid", error=str(exc))
        return DEFAULT_STEPS


def resolve_recipients(
    step: EscalationStep,
    dealer: DealerRecord | None,
    pronet: ProtonNetRecord | None = None,
) -> tuple[list[str], list[str]]:
    """(to, cc) for *step*, skipping every role that is not configured.

    A CC role is NEVER promoted into the To line, however empty To ends up.
    The whole point of a rung is that it reaches a more senior person than
    the last one did; quietly re-addressing it to whoever happens to be
    configured would send a "2ND REMINDER, respond immediately" to the
    service desk that has been reading the thread all along, and would hide
    the fact that the Owner's address was never filled in.

    An empty To therefore means "skip this step", and the caller logs it.
    """

    def _lookup(role: str) -> str:
        if role in DEALER_ROLES:
            return dealer.contact(role) if dealer is not None else ""
        return pronet.contact(role) if pronet is not None else ""

    def _collect(roles: tuple[str, ...]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for role in roles:
            address = _lookup(role).strip()
            key = address.lower()
            if address and key not in seen:
                seen.add(key)
                out.append(address)
        return out

    to = _collect(step.to_roles)
    cc = [a for a in _collect(step.cc_roles) if a.lower() not in {t.lower() for t in to}]
    return to, cc


def due_step(
    steps: tuple[EscalationStep, ...],
    elapsed_working_hours: float,
    current_step: int,
) -> EscalationStep | None:
    """The single next rung that is due, or None.

    Returns the step immediately after ``current_step`` -- never the highest
    step whose delay has passed. A 24-hour outage must advance the ladder by
    one rung, not four: emailing a Dealer Owner about a case they were never
    given a chance to see is the exact failure this ladder exists to prevent.
    """
    for step in sorted(steps, key=lambda s: s.step_no):
        if step.step_no <= current_step:
            continue
        if elapsed_working_hours >= step.delay_working_hours:
            return step
        return None
    return None


def apply_delay_overrides(
    steps: tuple[EscalationStep, ...], overrides: dict[int, float]
) -> tuple[EscalationStep, ...]:
    """Return *steps* with operator-set delays laid over the table's own.

    Only the timings move. The roles a rung addresses stay exactly as the
    table declares them, because that is the SOP's contract and the reason
    the ladder is worth having -- an admin page that could point the 2nd
    reminder at the service desk would be a way to defeat the policy from
    inside the CRM.
    """
    if not overrides:
        return steps
    return tuple(
        replace(step, delay_working_hours=float(overrides[step.step_no]))
        if step.step_no in overrides
        else step
        for step in steps
    )


def step_by_no(steps: tuple[EscalationStep, ...], step_no: int) -> EscalationStep | None:
    for step in steps:
        if step.step_no == step_no:
            return step
    return None


def describe(step: EscalationStep, to: list[str], cc: list[str]) -> dict[str, Any]:
    """A log/dry-run friendly summary of what a step would do."""
    return {
        "step_no": step.step_no,
        "label": step.label,
        "channel": step.channel,
        "to": to,
        "cc": cc,
    }
