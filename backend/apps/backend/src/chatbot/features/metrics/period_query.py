"""FastAPI wiring shared by every period-capable `/metrics/*` route.

`period.py` holds the pure arithmetic; this holds the HTTP contract built
on top of it: the three query params, the uniform 400 mapping, the
rejection used by endpoints with no date dimension, and the
`{current, previous, deltas, scopes}` envelope.

Split out of `insights_router.py` (Package E final fix, finding I1). It
lived there as module-private helpers because the insights router was the
only period-capable router -- but `/metrics/dashboard` is its own module,
never got the params, and so silently served all-time data under a
caller-supplied week header: FastAPI **drops undeclared query params**, so
`GET /metrics/dashboard?from=...&to=...&granularity=week` returned 200 with
an unfiltered payload rather than either honouring or rejecting the window.
That is the exact failure `reject_period` was written to prevent, on the
one endpoint that lacked it, and it happened because the contract had one
implementation and two routers. It now has one implementation and one home:
a second copy is what drifts.

Nothing here closes over a port or settings -- these are pure functions of
their arguments, which is why they can be shared at module level rather
than rebuilt per-router.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Query

from chatbot.features.metrics.period import delta_pct, parse_period

if TYPE_CHECKING:
    from chatbot.features.metrics.period import PeriodRange


def period_query(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    granularity: str | None = Query(default=None),
) -> tuple[str | None, str | None, str | None]:
    """Shared `from`/`to`/`granularity` query-param declaration -- every
    period-capable endpoint takes the same three, so this collapses eight
    repetitions of the same `Query(...)` triplet into one dependency.

    Declaring them is load-bearing even on an endpoint that only rejects
    them: an undeclared param is invisible to the handler, so "ignore it"
    and "reject it" become indistinguishable at the source level and the
    ignoring one ships."""
    return from_, to, granularity


PeriodQuery = Annotated[tuple[str | None, str | None, str | None], Depends(period_query)]


def parse_period_or_400(query: PeriodQuery) -> PeriodRange | None:
    """`parse_period`, with every `ValueError` (inverted range, unknown
    granularity, a partial from/to/granularity set) mapped to a 400 naming
    what was wrong -- never a 500, never a silent fallback to unfiltered
    data."""
    from_, to, granularity = query
    try:
        return parse_period(from_, to, granularity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _sum_field(rows: list[Any], field_name: str) -> float:
    return float(sum(getattr(row, field_name) for row in rows))


def block_delta(current: Any, previous: Any, block_name: str, field_name: str) -> float | None:
    """`None` unless *both* legs' scope for this block is "ok" -- a delta
    against a degraded leg (unavailable / unsupported_granularity) is a
    wrong number wearing a correct-looking label, so it's suppressed rather
    than emitted."""
    current_scope = current.scopes.get(block_name)
    previous_scope = previous.scopes.get(block_name)
    if current_scope is None or previous_scope is None:
        return None
    if current_scope.status != "ok" or previous_scope.status != "ok":
        return None
    return delta_pct(
        _sum_field(getattr(current, block_name), field_name),
        _sum_field(getattr(previous, block_name), field_name),
    )


def wrap_period_response(
    current: Any, previous: Any, deltas: dict[str, float | None]
) -> dict[str, Any]:
    """`scopes` pairs each block's current-leg and previous-leg
    `BlockScope` (`{"current": ..., "previous": ...}`) rather than two
    separate sibling maps -- so a consumer can render "current: ok /
    previous: unavailable" for one block without cross-referencing two
    top-level maps by key. Reflecting only `current`'s scope (the original
    implementation) let a degraded previous leg hide behind an "ok" label
    next to a delta computed from its silently-empty rows."""
    return {
        "current": asdict(current),
        "previous": asdict(previous),
        "deltas": deltas,
        "scopes": {
            name: {
                "current": asdict(scope),
                "previous": (asdict(previous.scopes[name]) if name in previous.scopes else None),
            }
            for name, scope in current.scopes.items()
        },
    }
