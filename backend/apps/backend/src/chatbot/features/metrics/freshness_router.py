"""GET /metrics/freshness — what every report surface's numbers are as-of.

P9 task 5. One endpoint the pages read so each can render its own freshness
line, rather than each page hardcoding a guess about its own data source.

404 while `DASHBOARD_FRESHNESS_ENABLED` is off, so a tenant that has not opted
in gains no new surface at all -- the same shape `/metrics/ai-cost` and
`/metrics/anomalies/hourly` use.

Unauthenticated, like `/metrics/dashboard` and unlike the `/metrics/insights`
family: the payload is sync timing and configuration, no aggregates and no PII.

Mounted through `build_metrics_anomaly_router`, which `main.py` already wires
with `Settings`, so this is reachable over HTTP without a `main.py` change. It
is a plain factory and can be included directly instead whenever that file is
next touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from chatbot.features.metrics.freshness import surface_freshness

if TYPE_CHECKING:
    from chatbot.platform.config import Settings


def build_metrics_freshness_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/freshness")
    async def freshness() -> dict[str, Any]:
        """Per-surface `as_of` and `source`.

        Per-surface, never one global answer: a page's freshness is a property
        of its own data source, and one shared value would stamp a live surface
        as batch or a batch surface as live. `alert_stream` is the one that
        moves -- live while `INBOUND_ALERTS_ENABLED` is on, the existing
        60-second poll otherwise.
        """
        if not settings.dashboard_freshness_enabled:
            raise HTTPException(status_code=404, detail="Dashboard freshness is not enabled")
        return {
            "surfaces": {
                name: {
                    "as_of": f.as_of.isoformat() if f.as_of else None,
                    "source": f.source,
                    **f.as_payload(),
                }
                for name, f in surface_freshness(settings).items()
            }
        }

    return router
