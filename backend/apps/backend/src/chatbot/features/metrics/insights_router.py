"""GET /metrics/{departments,callcenter,lifecycle,dealer-escalation,sla-buckets,
case-aging,volume-by-type} — gated report reads.

Agent/PIC-level aggregates, so x-api-key gated (unlike /metrics/dashboard).

`from`/`to`/`granularity` query params add period filtering on top of Task 2's
range-aware query adapter. The params, the 400 mapping, and the wrapped
envelope all live in `period_query.py` -- shared with `dashboard_router.py`
rather than duplicated (see that module's docstring for why):

- No period given -> today's bare shape, byte-identical (the SPA reads these
  payloads on a schedule we don't control; every existing consumer must keep
  working unchanged). Every handler below returns the plain `asdict(...)`
  verbatim on the `period is None` branch, rather than routing through
  `wrap_period_response`, which is only reached once a period is supplied.
- A period given on an endpoint whose `MetricsQueryPort` method accepts one
  (`fetch_lifecycle`, `fetch_volume_by_type_division`) wraps the response as
  `{"current", "previous", "deltas", "scopes"}`. `previous` is fetched via
  `previous_period()` (concurrently with `current` -- they're independent
  reads) so every consumer's "vs last week" reads the same window; `deltas`
  is computed once here (not left to each frontend component) so every
  consumer shows an identical percentage; `scopes` surfaces *both* legs'
  `BlockScope` (Task 2) per block -- see `wrap_period_response` -- so a
  genuinely quiet period is distinguishable from a block that could not be
  filtered at all, on *either* side of the comparison, not just the current
  one.
- Both legs of `/metrics/lifecycle` come back with an EMPTY `cases` list
  scoped `unsupported_granularity`, by design: `v_case_lifecycle` cannot be
  period-filtered, and this route's two-leg fan-out would otherwise
  full-scan it twice and serialise the entire all-time case list into a
  week-scoped payload the page never reads from (finding I5, see
  `query_adapter._lifecycle_cases_block`). The no-period branch above is
  unaffected -- that is the one the Case Lifecycle report reads.
- A delta is only ever emitted when both the current and previous leg's
  scope for that block is "ok" (`block_delta`). A percentage computed
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
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException

from chatbot.features.metrics.period import previous_period
from chatbot.features.metrics.period_query import (
    PeriodQuery,
    block_delta,
    parse_period_or_400,
    reject_period,
    wrap_period_response,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


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

    @router.get("/metrics/departments")
    async def departments(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        reject_period("departments", parse_period_or_400(period_query))
        return asdict(await port.fetch_departments())

    @router.get("/metrics/callcenter")
    async def callcenter(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        reject_period("callcenter", parse_period_or_400(period_query))
        return asdict(await port.fetch_callcenter())

    @router.get("/metrics/lifecycle")
    async def lifecycle(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        if period is None:
            return asdict(await port.fetch_lifecycle())
        current, previous = await asyncio.gather(
            port.fetch_lifecycle(period), port.fetch_lifecycle(previous_period(period))
        )
        deltas = {"state_trend": block_delta(current, previous, "state_trend", "cases")}
        return wrap_period_response(current, previous, deltas)

    @router.get("/metrics/dealer-escalation")
    async def dealer_escalation(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        reject_period("dealer-escalation", parse_period_or_400(period_query))
        return asdict(await port.fetch_dealer_escalation())

    @router.get("/metrics/sla-buckets")
    async def sla_buckets(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        reject_period("sla-buckets", parse_period_or_400(period_query))
        return asdict(await port.fetch_sla_buckets())

    @router.get("/metrics/case-aging")
    async def case_aging(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        reject_period("case-aging", parse_period_or_400(period_query))
        return asdict(await port.fetch_case_aging())

    @router.get("/metrics/volume-by-type")
    async def volume_by_type(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        if period is None:
            return asdict(await port.fetch_volume_by_type_division())
        current, previous = await asyncio.gather(
            port.fetch_volume_by_type_division(period),
            port.fetch_volume_by_type_division(previous_period(period)),
        )
        deltas = {"volume": block_delta(current, previous, "volume", "volume")}
        return wrap_period_response(current, previous, deltas)

    return router
