"""GET /metrics/{departments,callcenter,lifecycle,dealer-escalation,sla-buckets,
case-aging,volume-by-type,ai-cost} — gated report reads.

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
- P4: departments, callcenter, dealer-escalation, sla-buckets and case-aging
  now HONOUR a period. They used to 400 (`reject_period`, since deleted)
  because their views had no date dimension, so a period could only have been
  ignored -- and an all-time answer under a caller-supplied week header is a
  lie with a header on it, which made a loud rejection the honest option. P4
  added the `day` columns, so the real answer is now available and the
  rejection is gone. Blocks that STILL have no date dimension
  (`category_by_vehicle_model`, dealer `slowest_cases`) stay unfiltered and
  say so via their `BlockScope` rather than being quietly served inside a
  period-labelled response.
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

from chatbot.features.metrics.ai_cost import (
    AI_COST_VIEW,
    AiCostUsagePort,
    PriceLookup,
    build_ai_cost_report,
    build_ai_cost_usage_port,
)
from chatbot.features.metrics.filter_query import MetricFiltersQuery
from chatbot.features.metrics.period import previous_period
from chatbot.features.metrics.period_query import (
    PeriodQuery,
    block_delta,
    parse_period_or_400,
    wrap_period_response,
)
from chatbot.features.metrics.price_table import build_price_table

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


async def _ai_cost_payload(
    settings: Settings,
    cache: dict[str, Any],
    ai_cost_port: AiCostUsagePort | None,
    price_table: PriceLookup | None,
    period: Any,
) -> dict[str, Any]:
    """Read the cost inputs and build the report.

    Module-level rather than a closure so the router factory stays readable,
    and so the lazy dependency construction has exactly one home. The
    real readers are built on FIRST REQUEST and cached, not at boot: a tenant
    that never opens the cost report never constructs a BigQuery client or a
    Firestore client for it, and the existing `main.py` call site needs no
    change to get them.
    """
    if "deps" not in cache:
        cache["deps"] = (
            ai_cost_port or build_ai_cost_usage_port(settings),
            price_table or build_price_table(settings),
        )
    usage_port, prices = cache["deps"]
    rows, ok = await usage_port.fetch_token_usage(period)
    conversations = await usage_port.fetch_conversation_count(period)
    return await build_ai_cost_report(
        rows, prices, ok=ok, conversations=conversations, period=period
    )


def build_metrics_insights_router(
    port: MetricsQueryPort,
    settings: Settings,
    *,
    ai_cost_port: AiCostUsagePort | None = None,
    price_table: PriceLookup | None = None,
) -> APIRouter:
    """The two AI-cost dependencies are keyword-only with `None` defaults so
    `main.py`'s existing call site needs no change and the real deployment
    still gets the real readers -- see `_ai_cost_payload`, which builds them
    lazily off the same `metrics_provider` switch every other metrics adapter
    reads."""
    router = APIRouter(tags=["metrics"])
    _cost_cache: dict[str, Any] = {}

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
        filters: MetricFiltersQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        return asdict(await port.fetch_departments(period, filters))

    @router.get("/metrics/callcenter")
    async def callcenter(
        period_query: PeriodQuery,
        filters: MetricFiltersQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        return asdict(await port.fetch_callcenter(period, filters))

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
        filters: MetricFiltersQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        return asdict(await port.fetch_dealer_escalation(period, filters))

    @router.get("/metrics/sla-buckets")
    async def sla_buckets(
        period_query: PeriodQuery,
        filters: MetricFiltersQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        return asdict(await port.fetch_sla_buckets(period, filters))

    @router.get("/metrics/case-aging")
    async def case_aging(
        period_query: PeriodQuery,
        filters: MetricFiltersQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        return asdict(await port.fetch_case_aging(period, filters))

    @router.get("/metrics/after-hours")
    async def after_hours(
        period_query: PeriodQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """P1: how much volume arrives outside business hours, and how first
        response compares across that split.

        Period-capable: `v_volume_after_hours` is day-grain. The first-response
        block is month-grain and says so via its own scope rather than being
        silently served all-time under a period header.
        """
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        return asdict(await port.fetch_after_hours(period))

    @router.get("/metrics/by-tag")
    async def by_tag(
        period_query: PeriodQuery,
        tag: str | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """4.80: volume per label.

        The `note` is part of the payload, not a caption someone might forget:
        a case with three labels is counted three times, so these figures
        overlap and must not be summed into a total.
        """
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)
        metrics = await port.fetch_by_tag(period, tag)
        return {**asdict(metrics), "note": metrics.note}

    @router.get("/metrics/ai-cost")
    async def ai_cost(
        period_query: PeriodQuery,
        filters: MetricFiltersQuery,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """4.28.2: what the AI costs, and everything that figure cannot see.

        The payload has **no total** -- `priced_subtotal_usd` plus a
        `completeness` block naming every unmetered and unpriceable surface.
        See `ai_cost.py`; the incompleteness is structural on purpose, because
        the largest single line item (`chat.turn`) produces no usage row at
        all and a total would silently omit it.

        `conversations` ships in the same response as the subtotal so
        cost-per-conversation is answerable from ONE response rather than by
        joining two -- and it ships with `cost_per_conversation_basis`, which
        says the numerator is partial.

        The five standard dimension filters are declared (an undeclared param
        is invisible to the handler, so "ignore" and "reject" become
        indistinguishable) and any of them that is actually supplied is a 400:
        a Gemini call knows its product surface, not the agent or dealer of
        the conversation that triggered it, so filtering by one could only be
        ignored. Off by default -- `ai_cost_reporting_enabled`.
        """
        _require_key(x_api_key)
        if not getattr(settings, "ai_cost_reporting_enabled", False):
            raise HTTPException(status_code=404, detail="AI cost reporting is not enabled")
        period = parse_period_or_400(period_query)
        # Raises UnsupportedFilter (400) for any supplied filter; a no-op when
        # none was.
        filters.predicates_for(AI_COST_VIEW)
        return await _ai_cost_payload(settings, _cost_cache, ai_cost_port, price_table, period)

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
