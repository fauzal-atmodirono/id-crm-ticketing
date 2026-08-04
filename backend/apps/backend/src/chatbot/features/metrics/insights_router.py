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
  `previous_period()` so every consumer's "vs last week" reads the same
  window; `deltas` is computed once here (not left to each frontend
  component) so every consumer shows an identical percentage; `scopes`
  surfaces each block's `BlockScope` (Task 2) so a genuinely quiet period is
  distinguishable from a block that could not be filtered at all -- neither
  of which a bare row list can express.
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

import hmac
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException, Query

from chatbot.features.metrics.period import delta_pct, parse_period, previous_period

if TYPE_CHECKING:
    from chatbot.features.metrics.period import PeriodRange
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

    def _parse_period(
        from_: str | None, to: str | None, granularity: str | None
    ) -> PeriodRange | None:
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

    def _wrap_period_response(
        current: Any, previous: Any, deltas: dict[str, float | None]
    ) -> dict[str, Any]:
        return {
            "current": asdict(current),
            "previous": asdict(previous),
            "deltas": deltas,
            "scopes": {name: asdict(scope) for name, scope in current.scopes.items()},
        }

    @router.get("/metrics/departments")
    async def departments(
        x_api_key: str | None = Header(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        granularity: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("departments", _parse_period(from_, to, granularity))
        return asdict(await port.fetch_departments())

    @router.get("/metrics/callcenter")
    async def callcenter(
        x_api_key: str | None = Header(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        granularity: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("callcenter", _parse_period(from_, to, granularity))
        return asdict(await port.fetch_callcenter())

    @router.get("/metrics/lifecycle")
    async def lifecycle(
        x_api_key: str | None = Header(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        granularity: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = _parse_period(from_, to, granularity)
        if period is None:
            return asdict(await port.fetch_lifecycle())
        current = await port.fetch_lifecycle(period)
        previous = await port.fetch_lifecycle(previous_period(period))
        deltas = {
            "state_trend": delta_pct(
                _sum_field(current.state_trend, "cases"),
                _sum_field(previous.state_trend, "cases"),
            )
        }
        return _wrap_period_response(current, previous, deltas)

    @router.get("/metrics/dealer-escalation")
    async def dealer_escalation(
        x_api_key: str | None = Header(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        granularity: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("dealer-escalation", _parse_period(from_, to, granularity))
        return asdict(await port.fetch_dealer_escalation())

    @router.get("/metrics/sla-buckets")
    async def sla_buckets(
        x_api_key: str | None = Header(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        granularity: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("sla-buckets", _parse_period(from_, to, granularity))
        return asdict(await port.fetch_sla_buckets())

    @router.get("/metrics/case-aging")
    async def case_aging(
        x_api_key: str | None = Header(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        granularity: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        _reject_period("case-aging", _parse_period(from_, to, granularity))
        return asdict(await port.fetch_case_aging())

    @router.get("/metrics/volume-by-type")
    async def volume_by_type(
        x_api_key: str | None = Header(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        granularity: str | None = Query(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = _parse_period(from_, to, granularity)
        if period is None:
            return asdict(await port.fetch_volume_by_type_division())
        current = await port.fetch_volume_by_type_division(period)
        previous = await port.fetch_volume_by_type_division(previous_period(period))
        deltas = {
            "volume": delta_pct(
                _sum_field(current.volume, "volume"),
                _sum_field(previous.volume, "volume"),
            )
        }
        return _wrap_period_response(current, previous, deltas)

    return router
