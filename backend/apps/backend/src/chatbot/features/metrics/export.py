"""Pure renderers: DashboardMetrics -> xlsx/pdf bytes (no I/O, no network)."""

from __future__ import annotations

import csv
import io
from dataclasses import fields
from typing import TYPE_CHECKING, Any, cast

from openpyxl import Workbook  # type: ignore[import-untyped]
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import DashboardMetrics


def _blocks(metrics: Any) -> list[tuple[str, list[Any]]]:
    """Every list[<dataclass>] field on `metrics`, as (name, rows)."""
    return [(f.name, getattr(metrics, f.name)) for f in fields(metrics)]


def _visible_field_names(rows: list[Any]) -> list[str]:
    """Field names for `rows`, excluding any that are `None` on *every*
    row in this block -- e.g. `VolumeRow.bucket` outside a period-scoped
    query (Task 2 / Package E): only the adapter's period-aware volume
    path ever populates it, so a scheduled all-time export
    (`scheduler.py` calls `fetch_dashboard()` with no period) would
    otherwise render a column that is blank on every single row. That's
    presentational noise, not data, so it's dropped rather than shown
    empty."""
    names = [f.name for f in fields(rows[0])]
    return [n for n in names if any(getattr(r, n) is not None for r in rows)]


def render_xlsx(metrics: DashboardMetrics) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)  # drop the default sheet
    for name, rows in _blocks(metrics):
        ws = wb.create_sheet(title=name[:31])  # Excel sheet-name limit
        if rows:
            field_names = _visible_field_names(rows)
            ws.append(field_names)
            for row in rows:
                ws.append([getattr(cast(Any, row), n) for n in field_names])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render_pdf(metrics: DashboardMetrics) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    story: list[Any] = [Paragraph("Bot Metrics Report", styles["Title"]), Spacer(1, 12)]
    for name, rows in _blocks(metrics):
        story.append(Paragraph(name, styles["Heading2"]))
        if rows:
            field_names = _visible_field_names(rows)
            data = [field_names] + [
                [str(getattr(cast(Any, r), n)) for n in field_names] for r in rows
            ]
            table = Table(data)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.append(table)
        else:
            story.append(Paragraph("(no data)", styles["Normal"]))
        story.append(Spacer(1, 12))
    doc.build(story)
    return buf.getvalue()


def render_csv(metrics: Any) -> bytes:
    """CSV export for any dataclass whose fields are list[<dataclass>] — same
    _blocks() reflection render_xlsx/render_pdf already use, so this
    works for DashboardMetrics AND every new report bundle (DealerEscalation-
    Metrics, SlaBucketMetrics, CaseAgingMetrics, VolumeByTypeDivisionMetrics,
    DepartmentsMetrics, ...) with no per-view code."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for name, rows in _blocks(metrics):
        writer.writerow([name])
        if rows:
            field_names = _visible_field_names(rows)
            writer.writerow(field_names)
            for row in rows:
                writer.writerow([getattr(cast(Any, row), n) for n in field_names])
        else:
            writer.writerow(["(no data)"])
        writer.writerow([])
    return buf.getvalue().encode("utf-8")
