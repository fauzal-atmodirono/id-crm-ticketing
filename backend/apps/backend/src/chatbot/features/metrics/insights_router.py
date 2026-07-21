"""GET /metrics/{departments,callcenter,lifecycle} — gated report reads.

Agent/PIC-level aggregates, so x-api-key gated (unlike /metrics/dashboard)."""
from __future__ import annotations

import hmac
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException

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
    async def departments(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_departments())

    @router.get("/metrics/callcenter")
    async def callcenter(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_callcenter())

    @router.get("/metrics/lifecycle")
    async def lifecycle(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_lifecycle())

    return router
