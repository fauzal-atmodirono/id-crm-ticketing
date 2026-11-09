"""GET /metrics/export?format=xlsx|pdf — downloadable metrics report.

GET /metrics/weekly-report/export?format=pdf|xlsx is the Weekly Report bundle
(FIX-14). It IS gated by x-api-key, unlike /metrics/export. The window is an
explicit `from`/`to` (what the Weekly Report page sends, since its window can
start on any day), else `week_of` (the Monday-Sunday week containing it), else
the last full week in REPORTING_TIMEZONE -- the same calendar the emailed
report uses.

/metrics/export (dashboard xlsx/pdf) is intentionally unauthenticated, matching
the ungated dashboard it exports. The five per-view CSV routes below expose
the same PIC/dealer/departments-level aggregates as their x-api-key-gated JSON
equivalents in insights_router.py, so they're gated the same way here."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from datetime import date
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response

from chatbot.features.authz.data_scope import (
    build_report_scope_dependency,
    build_unscoped_only_dependency,
)
from chatbot.features.metrics.export import render_csv, render_pdf, render_xlsx
from chatbot.features.metrics.period import PeriodRange
from chatbot.features.metrics.weekly_report import (
    fetch_weekly_report,
    last_full_week,
    local_today,
    render_weekly_pdf,
    render_weekly_xlsx,
    week_containing,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings

_MAX_WINDOW_DAYS = 31
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_metrics_export_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    # Wave 2 Task 28: every route declares a data-scope stance, the same one
    # as its JSON twin in insights_router.py. `unscoped_only`: no scope
    # dimension on every block, so a data-scoped caller is refused
    # (data_scope.py rule 6). `scoped` (final review M3): the dealer-
    # escalation and case-aging CSVs read the same scope-aware views as their
    # scoped JSON routes, so they serve a scoped caller filtered rows instead
    # of a 403. Flag off: both are no-ops that never read the session.
    router = APIRouter(tags=["metrics"])
    unscoped_only = [Depends(build_unscoped_only_dependency(settings, "The report export"))]
    scoped = [Depends(build_report_scope_dependency(settings, port))]

    def _require_key(x_api_key: str | None) -> None:
        key = settings.metrics_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode(), key.encode())
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @router.get("/metrics/export", dependencies=unscoped_only)
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

    @router.get("/metrics/weekly-report/export", dependencies=unscoped_only)
    async def export_weekly(
        format: str = "pdf",
        week_of: str | None = None,
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> Response:
        # Gated like the per-view exports: the bundle carries PIC/dealer/department data.
        _require_key(x_api_key)
        if format not in ("pdf", "xlsx"):
            raise HTTPException(status_code=400, detail="format must be pdf or xlsx")
        try:
            if from_ or to:
                if not (from_ and to):
                    raise ValueError("from and to go together")
                start, end = date.fromisoformat(from_), date.fromisoformat(to)
                if start > end or (end - start).days > _MAX_WINDOW_DAYS:
                    raise ValueError("bad window")
                period = PeriodRange(start, end, "week")
            elif week_of:
                period = week_containing(date.fromisoformat(week_of))
            else:
                period = last_full_week(
                    local_today(getattr(settings, "reporting_timezone", "UTC"))
                )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="from/to or week_of must be valid YYYY-MM-DD dates (from <= to, max 31 days)",
            ) from None
        report = await fetch_weekly_report(port, period)
        stem = f"weekly-report-{period.start.isoformat()}_{period.end.isoformat()}"
        if format == "pdf":
            return Response(
                content=render_weekly_pdf(report),
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename={stem}.pdf"},
            )
        return Response(
            content=render_weekly_xlsx(report),
            media_type=_XLSX,
            headers={"Content-Disposition": f"attachment; filename={stem}.xlsx"},
        )

    def _csv_route(
        path: str, filename: str, fetch: Callable[[], Awaitable[Any]], dependencies: list[Any]
    ) -> None:
        @router.get(path, name=f"export_csv_{filename}", dependencies=dependencies)
        async def _export_csv(x_api_key: str | None = Header(default=None)) -> Response:
            _require_key(x_api_key)
            metrics = await fetch()
            return Response(
                content=render_csv(metrics),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
            )

    _csv_route(
        "/metrics/dealer-escalation/export", "dealer-escalation", port.fetch_dealer_escalation, scoped
    )
    _csv_route("/metrics/sla-buckets/export", "sla-buckets", port.fetch_sla_buckets, unscoped_only)
    _csv_route("/metrics/case-aging/export", "case-aging", port.fetch_case_aging, scoped)
    _csv_route(
        "/metrics/volume-by-type/export",
        "volume-by-type",
        port.fetch_volume_by_type_division,
        unscoped_only,
    )
    _csv_route("/metrics/departments/export", "departments", port.fetch_departments, unscoped_only)

    return router
