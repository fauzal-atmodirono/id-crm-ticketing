"""FIX-14 (RFP 4.74/4.78/4.82): weekly management report as PDF + XLSX, emailed on a schedule."""

from __future__ import annotations

import asyncio
import io
from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pypdf import PdfReader

from chatbot.features.metrics.email_port import MockEmailReport
from chatbot.features.metrics.export_router import build_metrics_export_router
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.features.metrics.scheduler import (
    run_weekly_report_job,
    start_weekly_report_scheduler,
)
from chatbot.features.metrics.weekly_report import (
    SECTION_TITLES,
    fetch_weekly_report,
    last_full_week,
    render_weekly_pdf,
    render_weekly_xlsx,
)
from chatbot.platform.config import Settings


def _report() -> Any:
    return asyncio.run(fetch_weekly_report(MockMetricsQuery(), last_full_week(date(2026, 11, 11))))


def _pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def test_last_full_week_is_previous_monday_to_sunday() -> None:
    p = last_full_week(date(2026, 11, 11))  # a Wednesday
    assert (p.start, p.end, p.granularity) == (date(2026, 11, 2), date(2026, 11, 8), "week")


def test_weekly_report_pdf_has_all_sections() -> None:
    report = _report()
    data = render_weekly_pdf(report)
    assert data[:5] == b"%PDF-"
    text = _pdf_text(data)
    assert "2026-11-02" in text and "2026-11-08" in text
    for title in SECTION_TITLES.values():
        assert title in text, title


def test_weekly_report_xlsx_has_a_sheet_per_section_block() -> None:
    wb = load_workbook(io.BytesIO(render_weekly_xlsx(_report())))
    assert wb.sheetnames[0] == "summary"
    assert len(wb.sheetnames) > len(SECTION_TITLES)


def test_failed_section_is_marked_unavailable_not_dropped() -> None:
    class Broken(MockMetricsQuery):
        async def fetch_sla_buckets(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("bq down")

    report = asyncio.run(fetch_weekly_report(Broken(), last_full_week(date(2026, 11, 11))))
    text = _pdf_text(render_weekly_pdf(report))
    assert SECTION_TITLES["sla_buckets"] in text
    assert "unavailable" in text.lower()


def test_scheduled_email_sends_pdf_and_xlsx_to_configured_list() -> None:
    email = MockEmailReport()
    s = Settings(
        weekly_report_email_enabled=True,
        weekly_report_email_recipients="mgmt@x.com, ops@x.com",
    )
    run_weekly_report_job(s, MockMetricsQuery(), email, today=date(2026, 11, 9))
    assert len(email.sent) == 1
    msg = email.sent[0]
    assert msg["recipients"] == ["mgmt@x.com", "ops@x.com"]
    names = {a[0] for a in msg["attachments"]}
    assert names == {
        "weekly-report-2026-11-02_2026-11-08.pdf",
        "weekly-report-2026-11-02_2026-11-08.xlsx",
    }
    assert "2026-11-02" in msg["subject"]


def test_no_recipients_sends_nothing() -> None:
    email = MockEmailReport()
    run_weekly_report_job(
        Settings(weekly_report_email_enabled=True), MockMetricsQuery(), email, today=date(2026, 11, 9)
    )
    assert email.sent == []


def test_job_never_raises() -> None:
    class Boom:
        def send_report(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("smtp")

    run_weekly_report_job(
        Settings(weekly_report_email_recipients="a@x.com"), MockMetricsQuery(), Boom()  # type: ignore[arg-type]
    )


def test_schedule_off_by_default() -> None:
    s = Settings()
    assert s.weekly_report_email_enabled is False
    assert (
        start_weekly_report_scheduler(s, MockMetricsQuery(), MockEmailReport()) is None
    )


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def add_job(self, func: Any, **kw: Any) -> None:
        self.jobs.append(kw)

    def start(self) -> None: ...


def test_schedule_on_registers_weekly_cron() -> None:
    s = Settings(
        weekly_report_email_enabled=True,
        weekly_report_email_day="mon",
        weekly_report_email_hour=8,
    )
    fake = _FakeScheduler()
    assert start_weekly_report_scheduler(s, MockMetricsQuery(), MockEmailReport(), scheduler=fake) is fake
    job = fake.jobs[0]
    assert job["trigger"] == "cron"
    assert job["day_of_week"] == "mon" and job["hour"] == 8
    assert job["id"] == "weekly_report_email"


def test_weekly_export_route_pdf_and_xlsx_require_api_key() -> None:

    class S:
        metrics_api_key = "secret"

    app = FastAPI()
    app.include_router(build_metrics_export_router(MockMetricsQuery(), S()))  # type: ignore[arg-type]
    c = TestClient(app)
    assert c.get("/metrics/weekly-report/export?format=pdf").status_code == 401
    r = c.get(
        "/metrics/weekly-report/export?format=pdf&week_of=2026-11-04", headers={"x-api-key": "secret"}
    )
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    r = c.get("/metrics/weekly-report/export?format=xlsx", headers={"x-api-key": "secret"})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert c.get("/metrics/weekly-report/export?format=csv", headers={"x-api-key": "secret"}).status_code == 400
