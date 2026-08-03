"""GET /metrics/export?format=xlsx|pdf — downloadable metrics report.

/metrics/export (dashboard xlsx/pdf) is intentionally unauthenticated, matching
the ungated dashboard it exports. The five per-view CSV routes below expose
the same PIC/dealer/departments-level aggregates as their x-api-key-gated JSON
equivalents in insights_router.py, so they're gated the same way here."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from chatbot.features.metrics.export import render_csv, render_pdf, render_xlsx

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_metrics_export_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    def _require_key(x_api_key: str | None) -> None:
        key = settings.metrics_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode(), key.encode())
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

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

    def _csv_route(path: str, filename: str, fetch: Callable[[], Awaitable[Any]]) -> None:
        @router.get(path, name=f"export_csv_{filename}")
        async def _export_csv(x_api_key: str | None = Header(default=None)) -> Response:
            _require_key(x_api_key)
            metrics = await fetch()
            return Response(
                content=render_csv(metrics),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
            )

    _csv_route("/metrics/dealer-escalation/export", "dealer-escalation", port.fetch_dealer_escalation)
    _csv_route("/metrics/sla-buckets/export", "sla-buckets", port.fetch_sla_buckets)
    _csv_route("/metrics/case-aging/export", "case-aging", port.fetch_case_aging)
    _csv_route("/metrics/volume-by-type/export", "volume-by-type", port.fetch_volume_by_type_division)
    _csv_route("/metrics/departments/export", "departments", port.fetch_departments)

    return router
