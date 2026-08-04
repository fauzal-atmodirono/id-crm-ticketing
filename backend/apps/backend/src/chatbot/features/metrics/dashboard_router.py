"""GET /metrics/dashboard — read-only aggregated metrics for the in-app dashboard.

Unauthenticated by design (POC): the payload is channel-level aggregates only,
no PII or message content.

Period support (Package E final fix, finding I1). This route used to declare
no query params at all while `MetricsQueryPort.fetch_dashboard` already
accepted a `PeriodRange`. FastAPI drops undeclared query params, so
`GET /metrics/dashboard?from=2026-07-17&to=2026-07-23&granularity=week`
returned **200 with all-time data** under a caller-supplied week header --
neither honouring the window nor rejecting it. Every other non-period
endpoint 400s via `reject_period`; this was the one endpoint missing the
guard, and it was missing it on the only route where the port could
actually have answered. Downstream, `fetch_dashboard(period)`,
`_dashboard_volume_block`'s period branch, `VolumeRow.bucket` and export's
`period_only` metadata were all unreachable over HTTP -- a half-built
feature that port-level tests made look alive.

Two contracts, deliberately:

- **No period -> byte-identical to before.** Same bare
  `asdict(await port.fetch_dashboard())`, same eight top-level keys, no
  `scopes`, no wrapper. This is the constraint the whole package has held
  to, because the deployed SPA (`0020-reports-native-merge.patch`'s
  overview panel, and `apps/frontend`'s dashboard view) reads this payload
  and none of them pass a period.
- **Period -> the same `{current, previous, deltas, scopes}` envelope the
  insights endpoints return**, from the same `period_query.py` helpers, so
  a consumer parses one shape across all period-capable routes.

Only the `volume` block can ever be genuinely period-scoped; the other
seven views have no date dimension and come back `"unfiltered"` in
`scopes`, which is what stops a caller rendering an all-time CSAT under a
week header. `deltas` therefore carries the one key that can be computed
(`volume`); `block_delta` suppresses it to `null` unless both legs are
"ok".

Cost note: a period request fetches both legs, so the seven all-time blocks
are queried twice for identical answers. That is accepted rather than
optimised -- `previous` must be a complete, honestly-scoped
`DashboardMetrics` for the envelope to mean anything, and returning it with
selectively-empty blocks would reintroduce exactly the "empty list that
isn't a real zero" ambiguity `BlockScope` exists to remove. The no-period
path -- the only one any deployed client uses today -- pays nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from chatbot.features.metrics.period import previous_period
from chatbot.features.metrics.period_query import (
    PeriodQuery,
    block_delta,
    parse_period_or_400,
    wrap_period_response,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort


def build_metrics_query_router(port: MetricsQueryPort) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/dashboard")
    async def dashboard(period_query: PeriodQuery) -> dict[str, Any]:
        period = parse_period_or_400(period_query)
        if period is None:
            return asdict(await port.fetch_dashboard())
        current, previous = await asyncio.gather(
            port.fetch_dashboard(period), port.fetch_dashboard(previous_period(period))
        )
        deltas = {"volume": block_delta(current, previous, "volume", "volume")}
        return wrap_period_response(current, previous, deltas)

    return router
