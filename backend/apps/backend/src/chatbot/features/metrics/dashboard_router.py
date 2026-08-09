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

**Freshness (P9 task 7, closing task 5's one named gap).** This is the §2.2.3
executive dashboard: the surface most often quoted in a meeting, and therefore
the one that most needs to say what its numbers are as-of. Task 5 stamped the
ten insights endpoints and both anomaly endpoints and could not stamp this one,
because `build_metrics_query_router` took no `Settings` and `main.py` was out of
that task's scope; it left a test asserting the gap rather than an optional
`settings=None` no call site passed, so changing this signature is what makes
the stamp real. `settings` is therefore **required, not optional** -- an
optional parameter here would let a future call site silently un-stamp the
dashboard again, which is the failure the whole freshness contract exists to
prevent.

The stamp obeys the same two rules as `insights_router._stamp_batch_freshness`:
`DASHBOARD_FRESHNESS_ENABLED` off returns the *same dict object* (the deployed
SPA parses this payload on a schedule nobody here controls, so "flag off is
byte-identical" has to hold literally), and `getattr` rather than attribute
access, because several of this router's own tests pass a minimal settings stub.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from chatbot.features.metrics.freshness import batch_freshness, stamp_freshness
from chatbot.features.metrics.period import previous_period
from chatbot.features.metrics.period_query import (
    PeriodQuery,
    block_delta,
    parse_period_or_400,
    wrap_period_response,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


def build_metrics_query_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    def _stamp(payload: dict[str, Any]) -> dict[str, Any]:
        """`batch_6h`, like every other BigQuery-backed response.

        The dashboard reads the same warehouse the reporting pages do, fed by
        the same Chatwoot->BigQuery sync, so it carries the same basis sentence:
        a difference against the live CRM of up to one sync interval is expected
        rather than an error.
        """
        return stamp_freshness(
            payload,
            batch_freshness(settings),
            enabled=bool(getattr(settings, "dashboard_freshness_enabled", False)),
        )

    @router.get("/metrics/dashboard")
    async def dashboard(period_query: PeriodQuery) -> dict[str, Any]:
        period = parse_period_or_400(period_query)
        if period is None:
            return _stamp(asdict(await port.fetch_dashboard()))
        current, previous = await asyncio.gather(
            port.fetch_dashboard(period), port.fetch_dashboard(previous_period(period))
        )
        deltas = {"volume": block_delta(current, previous, "volume", "volume")}
        return _stamp(wrap_period_response(current, previous, deltas))

    return router
