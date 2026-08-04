"""Pure renderers: DashboardMetrics -> xlsx/pdf bytes (no I/O, no network)."""

from __future__ import annotations

import csv
import io
from dataclasses import astuple, fields
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
    """Every list[<dataclass>] field on `metrics`, as (name, rows).

    Only `list` fields -- as of Task 2 (Package E), DashboardMetrics,
    LifecycleMetrics and VolumeByTypeDivisionMetrics also carry a `scopes:
    dict[str, BlockScope]` field (which BlockScope per block, not a row
    list), and this reflection must skip it rather than try to render it
    as a sheet/table."""
    return [
        (f.name, getattr(metrics, f.name))
        for f in fields(metrics)
        if isinstance(getattr(metrics, f.name), list)
    ]


def render_xlsx(metrics: DashboardMetrics) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)  # drop the default sheet
    for name, rows in _blocks(metrics):
        ws = wb.create_sheet(title=name[:31])  # Excel sheet-name limit
        if rows:
            first_row = cast(Any, rows[0])
            ws.append([f.name for f in fields(first_row)])
            for row in rows:
                ws.append(list(astuple(cast(Any, row))))
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
            first_row = cast(Any, rows[0])
            header = [f.name for f in fields(first_row)]
            data = [header] + [[str(v) for v in astuple(cast(Any, r))] for r in rows]
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
            first_row = cast(Any, rows[0])
            writer.writerow([f.name for f in fields(first_row)])
            for row in rows:
                writer.writerow(list(astuple(cast(Any, row))))
        else:
            writer.writerow(["(no data)"])
        writer.writerow([])
    return buf.getvalue().encode("utf-8")
