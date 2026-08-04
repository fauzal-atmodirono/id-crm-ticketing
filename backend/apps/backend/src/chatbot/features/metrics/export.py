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


def _exportable_field_names(rows: list[Any]) -> list[str]:
    """Field names for `rows`, excluding any declared `period_only` in its
    dataclass field metadata -- e.g. `VolumeRow.bucket` (Task 2 / Package
    E): it's only ever populated by a period-scoped query, and no current
    export path (`scheduler.py`'s weekly xlsx/pdf/csv, or the
    `/metrics/*/export` routes) ever supplies a period, so it can never be
    populated here.

    Deliberately a property of the *field*, checked once per dataclass,
    not of the row *values* (Task 2 review, round 3): an earlier version
    of this function dropped any column that was `None` on every row in
    a given export, which also caught legitimately-nullable business
    columns that merely happen to be null in a particular week's data --
    e.g. CsatRow.avg_score/satisfied_rate, ResolutionRow.*_pct, NpsRow.nps,
    every other SAFE_DIVIDE-derived column, all of which are null only
    when a denominator was zero, not because they can't ever be populated.
    That made the exported header shift week to week based on which
    metric happened to be all-null, which is worse for anything parsing
    the report by header or column position than a single column that's
    always blank for a structural reason. Checking field metadata instead
    of row values keeps every export's column set deterministic and only
    ever drops a column that could never be anything but blank here."""
    return [f.name for f in fields(rows[0]) if not f.metadata.get("period_only")]


def render_xlsx(metrics: DashboardMetrics) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)  # drop the default sheet
    for name, rows in _blocks(metrics):
        ws = wb.create_sheet(title=name[:31])  # Excel sheet-name limit
        if rows:
            field_names = _exportable_field_names(rows)
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
            field_names = _exportable_field_names(rows)
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
            field_names = _exportable_field_names(rows)
            writer.writerow(field_names)
            for row in rows:
                writer.writerow([getattr(cast(Any, row), n) for n in field_names])
        else:
            writer.writerow(["(no data)"])
        writer.writerow([])
    return buf.getvalue().encode("utf-8")
