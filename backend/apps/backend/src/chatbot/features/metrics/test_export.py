import asyncio
import csv
import io
from dataclasses import fields

from openpyxl import load_workbook  # type: ignore[import-untyped]

from chatbot.features.metrics.export import render_csv, render_pdf, render_xlsx
from chatbot.features.metrics.query_port import (
    DashboardMetrics,
    DealerEscalationMetrics,
    DealerEscalationRow,
    DealerSlowCaseRow,
    MockMetricsQuery,
)


async def _metrics() -> DashboardMetrics:
    return await MockMetricsQuery().fetch_dashboard()


def _sync_metrics() -> DashboardMetrics:
    return asyncio.run(_metrics())


def test_render_xlsx_is_a_zip_workbook() -> None:
    data = render_xlsx(_sync_metrics())
    assert data[:2] == b"PK"  # xlsx is a zip container
    assert len(data) > 500


def test_render_xlsx_has_a_sheet_per_block() -> None:
    wb = load_workbook(io.BytesIO(render_xlsx(_sync_metrics())))
    # Excludes "scopes" (Task 2 / Package E): a dict[str, BlockScope], not a
    # row list, so render_xlsx's _blocks() helper skips it -- there's no
    # sheet for it, by design.
    row_list_fields = [f.name for f in fields(DashboardMetrics) if f.name != "scopes"]
    for block in row_list_fields:
        assert block in wb.sheetnames
    assert "scopes" not in wb.sheetnames


def test_render_pdf_has_pdf_header() -> None:
    data = render_pdf(_sync_metrics())
    assert data[:5] == b"%PDF-"
    assert len(data) > 500


def test_render_csv_produces_one_section_per_block():
    metrics = DealerEscalationMetrics(
        by_dealer=[DealerEscalationRow("Dealer KL", 12, 3.5, 3.0, 6.0)],
        slowest_cases=[DealerSlowCaseRow("CONV042", "Dealer KL", 12.0)],
    )
    content = render_csv(metrics).decode("utf-8")
    reader = list(csv.reader(io.StringIO(content)))
    assert ["by_dealer"] in reader
    assert ["dealer", "cases_escalated", "avg_turnaround_days", "p50_turnaround_days", "p90_turnaround_days"] in reader
    assert ["Dealer KL", "12", "3.5", "3.0", "6.0"] in reader
    assert ["slowest_cases"] in reader


def test_render_csv_empty_block_has_no_data_marker():
    metrics = DealerEscalationMetrics(by_dealer=[], slowest_cases=[])
    content = render_csv(metrics).decode("utf-8")
    assert "(no data)" in content
