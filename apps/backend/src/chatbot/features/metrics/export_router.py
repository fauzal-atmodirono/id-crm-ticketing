"""GET /metrics/export?format=xlsx|pdf — downloadable metrics report."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from chatbot.features.metrics.export import render_pdf, render_xlsx

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_metrics_export_router(port: MetricsQueryPort) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/export")
    async def export(format: str = "xlsx") -> Response:
        metrics = await port.fetch_dashboard()
        if format == "xlsx":
            return Response(
                content=render_xlsx(metrics),
                media_type=_XLSX,
                headers={"Content-Disposition": "attachment; filename=bot-metrics.xlsx"},
            )
        if format == "pdf":
            return Response(
                content=render_pdf(metrics),
                media_type="application/pdf",
                headers={"Content-Disposition": "attachment; filename=bot-metrics.pdf"},
            )
        raise HTTPException(status_code=400, detail="format must be xlsx or pdf")

    return router
