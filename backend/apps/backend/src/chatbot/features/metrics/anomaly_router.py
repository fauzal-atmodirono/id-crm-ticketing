"""GET /metrics/anomalies{,/hourly} — currently-flagged channel-volume anomalies.

Two grains, two thresholds. `/metrics/anomalies` is the daily detector the
deployed page already reads and is unchanged. `/metrics/anomalies/hourly` (P9
task 4) is the intra-day one §4.79 actually asks for; it 404s unless
`anomaly_hourly_enabled`, so a tenant that has not opted in sees no new surface
at all.

The hourly response returns **every** evaluated hour, not just the detections.
An hour suppressed by the minimum-volume floor has to appear labelled
`insufficient_volume` rather than be filtered out, because "we looked and it was
fine" and "there was not enough traffic to look" are different statements and a
page that shows only detections makes them identical.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from chatbot.features.metrics.anomaly import (
    HOURLY_STATUS_FLAGGED,
    evaluate_hourly_anomalies,
    flag_anomalies,
)
from chatbot.features.metrics.freshness import batch_freshness, stamp_freshness
from chatbot.features.metrics.freshness_router import build_metrics_freshness_router

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


def build_metrics_anomaly_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    def _stamp(payload: dict[str, Any]) -> dict[str, Any]:
        """P9 task 5. `batch_freshness`, not live: both endpoints below read
        `v_channel_anomaly*`, which the Chatwoot->BigQuery sync feeds on an
        interval. §3.5 calls the anomaly page real-time and the push on top of
        it is, but the figures underneath are as fresh as the last sync -- and
        stamping them `live_stream` because the page feels live is the
        per-surface lie the contract exists to prevent."""
        return stamp_freshness(
            payload, batch_freshness(settings), enabled=settings.dashboard_freshness_enabled
        )

    @router.get("/metrics/anomalies")
    async def anomalies() -> dict[str, Any]:
        rows = await port.fetch_anomalies()
        flagged = flag_anomalies(rows, settings.anomaly_zscore_k, settings.anomaly_min_baseline)
        return _stamp({"anomalies": [asdict(a) for a in flagged]})

    @router.get("/metrics/anomalies/hourly")
    async def anomalies_hourly() -> dict[str, Any]:
        """4.79's intra-day detector.

        `status` is about the EXAMINATION, not about the channels:
        `unavailable` means `v_channel_anomaly_hourly` could not be read (most
        likely `ensure_views` has not run since the flag was turned on), and in
        that case `hours` is empty because nothing was examined -- not because
        nothing was wrong.
        """
        if not settings.anomaly_hourly_enabled:
            raise HTTPException(status_code=404, detail="Hourly anomaly detection is not enabled")
        rows, ok = await port.fetch_hourly_anomalies()
        evaluated = evaluate_hourly_anomalies(
            rows,
            settings.anomaly_hourly_zscore_k,
            settings.anomaly_hourly_min_baseline,
        )
        return _stamp(
            {
                "status": "ok" if ok else "unavailable",
                "hours": [asdict(h) for h in evaluated],
                "anomalies": [asdict(h) for h in evaluated if h.status == HOURLY_STATUS_FLAGGED],
                # The configuration in force for THIS answer, so the page renders
                # the operator's floor and threshold rather than a hardcoded pair.
                "zscore_k": settings.anomaly_hourly_zscore_k,
                "min_baseline": settings.anomaly_hourly_min_baseline,
            }
        )

    # P9 task 5. `GET /metrics/freshness` is mounted through this already-wired
    # router rather than directly in `main.py`, so the freshness statement is
    # reachable over HTTP the moment it exists instead of waiting on a wiring
    # change. It is a plain factory and `main.py` may include it directly later.
    router.include_router(build_metrics_freshness_router(settings))

    return router
