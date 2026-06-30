"""GET /metrics/dashboard — read-only aggregated metrics for the in-app dashboard.

Unauthenticated by design (POC): the payload is channel-level aggregates only,
no PII or message content."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort


def build_metrics_query_router(port: MetricsQueryPort) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/dashboard")
    async def dashboard() -> dict[str, Any]:
        return asdict(await port.fetch_dashboard())

    return router
