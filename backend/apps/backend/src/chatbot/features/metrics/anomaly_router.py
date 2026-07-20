"""GET /metrics/anomalies — currently-flagged channel-volume anomalies."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from chatbot.features.metrics.anomaly import flag_anomalies

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


def build_metrics_anomaly_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/anomalies")
    async def anomalies() -> dict[str, Any]:
        rows = await port.fetch_anomalies()
        flagged = flag_anomalies(rows, settings.anomaly_zscore_k, settings.anomaly_min_baseline)
        return {"anomalies": [asdict(a) for a in flagged]}

    return router
