"""GET /metrics/{departments,callcenter,lifecycle,dealer-escalation,sla-buckets,
case-aging,volume-by-type} — gated report reads.

Agent/PIC-level aggregates, so x-api-key gated (unlike /metrics/dashboard).

`from`/`to`/`granularity` query params add period filtering on top of Task 2's
range-aware query adapter:

- No period given -> today's bare shape, byte-identical (the SPA reads these
  payloads on a schedule we don't control; every existing consumer must keep
  working unchanged). Every handler below returns the plain `asdict(...)`
  verbatim on the `period is None` branch, rather than routing through
  `_wrap_period_response`, which is only reached once a period is supplied.
- A period given on an endpoint whose `MetricsQueryPort` method accepts one
  (`fetch_lifecycle`, `fetch_volume_by_type_division`) wraps the response as
  `{"current", "previous", "deltas", "scopes"}`. `previous` is fetched via
  `previous_period()` (concurrently with `current` -- they're independent
  reads) so every consumer's "vs last week" reads the same window; `deltas`
  is computed once here (not left to each frontend component) so every
  consumer shows an identical percentage; `scopes` surfaces *both* legs'
  `BlockScope` (Task 2) per block -- see `_wrap_period_response` -- so a
  genuinely quiet period is distinguishable from a block that could not be
  filtered at all, on *either* side of the comparison, not just the current
  one.
- A delta is only ever emitted when both the current and previous leg's
  scope for that block is "ok" (`_block_delta`). A percentage computed
  against a degraded leg (`unavailable`, `unsupported_granularity`) is
  worse than an absent one -- it looks trustworthy while silently comparing
  against wrong or missing data -- so it comes back `null` instead, the same
  suppression `delta_pct` already applies to a zero-previous denominator.
- A period given on an endpoint whose method takes no period at all
  (departments, callcenter, dealer-escalation, sla-buckets, case-aging --
  none of their underlying views have a date dimension, see
  `MetricsQueryPort`'s docstring) is a 400, not a silently-ignored filter.
  Accepting `from`/`to`/`granularity` and then serving an all-time answer
  under a caller-supplied week header is the exact failure this guards
  against: better a loud rejection than a number that looks period-scoped
  but isn't.
- Any `ValueError` out of `parse_period` (inverted range, unknown
  granularity, a partial from/to/granularity set) becomes a 400 naming what
  was wrong -- never a 500, never a fallback to unfiltered data.
"""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from chatbot.features.metrics.period import delta_pct, parse_period, previous_period

if TYPE_CHECKING:
    from chatbot.features.metrics.period import PeriodRange
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


def _period_query(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    granularity: str | None = Query(default=None),
) -> tuple[str | None, str | None, str | None]:
    """Shared `from`/`to`/`granularity` query-param declaration -- every
    insights endpoint takes the same three, so this collapses seven
    repetitions of the same `Query(...)` triplet into one dependency."""
    return from_, to, granularity


_PeriodQuery = Annotated[tuple[str | None, str | None, str | None], Depends(_period_query)]


def build_metrics_insights_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    def _require_key(x_api_key: str | None) -> None:
        key = settings.metrics_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode(), key.encode())
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def _parse_period(period_query: _PeriodQuery) -> PeriodRange | None:
        from_, to, granularity = period_query
        try:
            return parse_period(from_, to, granularity)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    def _reject_period(endpoint: str, period: PeriodRange | None) -> None:
        """Requirement 6: a method with no period support 400s rather than
        silently serving an all-time answer under a period-scoped header."""
        if period is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"/metrics/{endpoint} does not support period filtering "
                    "(its underlying view has no date dimension)"
                ),
            )

    def _sum_field(rows: list[Any], field_name: str) -> float:
        return float(sum(getattr(row, field_name) for row in rows))

    def _block_delta(current: Any, previous: Any, block_name: str, field_name: str) -> float | None:
        """`None` unless *both* legs' scope for this block is "ok" -- a
        delta against a degraded leg (unavailable / unsupported_granularity)
        is a wrong number wearing a correct-looking label, so it's
        suppressed rather than emitted."""
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

    def _wrap_period_response(
        current: Any, previous: Any, deltas: dict[str, float | None]
    ) -> dict[str, Any]:
        """`scopes` pairs each block's current-leg and previous-leg
        `BlockScope` (`{"current": ..., "previous": ...}`) rather than two
        separate sibling maps -- so Task 4 can render "current: ok /
        previous: unavailable" for one block without cross-referencing two
        top-level maps by key. Reflecting only `current`'s scope (the
        original implementation) let a degraded previous leg hide behind an
        "ok" label next to a delta computed from its silently-empty rows."""
        return {
            "current": asdict(current),
            "previous": asdict(previous),
            "deltas": deltas,
            "scopes": {
                name: {
                    "current": asdict(scope),
                    "previous": (
                        asdict(previous.scopes[name]) if name in previous.scopes else None
                    ),
                }
                for name, scope in current.scopes.items()
            },
        }

    @router.get("/metrics/departments")
    async def departments(
        period_query: _PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("departments", _parse_period(period_query))
        return asdict(await port.fetch_departments())

    @router.get("/metrics/callcenter")
    async def callcenter(
        period_query: _PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("callcenter", _parse_period(period_query))
        return asdict(await port.fetch_callcenter())

    @router.get("/metrics/lifecycle")
    async def lifecycle(
        period_query: _PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = _parse_period(period_query)
        if period is None:
            return asdict(await port.fetch_lifecycle())
        current, previous = await asyncio.gather(
            port.fetch_lifecycle(period), port.fetch_lifecycle(previous_period(period))
        )
        deltas = {"state_trend": _block_delta(current, previous, "state_trend", "cases")}
        return _wrap_period_response(current, previous, deltas)

    @router.get("/metrics/dealer-escalation")
    async def dealer_escalation(
        period_query: _PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("dealer-escalation", _parse_period(period_query))
        return asdict(await port.fetch_dealer_escalation())

    @router.get("/metrics/sla-buckets")
    async def sla_buckets(
        period_query: _PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("sla-buckets", _parse_period(period_query))
        return asdict(await port.fetch_sla_buckets())

    @router.get("/metrics/case-aging")
    async def case_aging(
        period_query: _PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("case-aging", _parse_period(period_query))
        return asdict(await port.fetch_case_aging())

    @router.get("/metrics/volume-by-type")
    async def volume_by_type(
        period_query: _PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = _parse_period(period_query)
        if period is None:
            return asdict(await port.fetch_volume_by_type_division())
        current, previous = await asyncio.gather(
            port.fetch_volume_by_type_division(period),
            port.fetch_volume_by_type_division(previous_period(period)),
        )
        deltas = {"volume": _block_delta(current, previous, "volume", "volume")}
        return _wrap_period_response(current, previous, deltas)

    return router
