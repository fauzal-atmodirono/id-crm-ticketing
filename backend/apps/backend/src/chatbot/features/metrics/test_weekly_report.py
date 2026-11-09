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


# ---- review fixes -----------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402

from chatbot.features.metrics import scheduler as sch_mod  # noqa: E402
from chatbot.features.metrics import weekly_report as wr  # noqa: E402
from chatbot.features.metrics.query_port import (  # noqa: E402
    BlockScope,
    DashboardMetrics,
    VolumeRow,
)


def _freeze(monkeypatch: pytest.MonkeyPatch, utc: datetime) -> None:
    monkeypatch.setattr(wr, "_utcnow", lambda: utc)


def test_local_today_uses_reporting_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mon 9 Nov 00:30 in Kuala Lumpur == Sun 8 Nov 16:30 UTC
    _freeze(monkeypatch, datetime(2026, 11, 8, 16, 30, tzinfo=UTC))
    assert wr.local_today("Asia/Kuala_Lumpur") == date(2026, 11, 9)
    assert wr.local_today("UTC") == date(2026, 11, 8)
    assert wr.local_today("Not/AZone") == date(2026, 11, 8)  # bad tz falls back to UTC


@pytest.mark.parametrize(
    "utc",
    [
        datetime(2026, 11, 8, 16, 30, tzinfo=UTC),  # Mon 00:30 KL
        datetime(2026, 11, 9, 0, 0, tzinfo=UTC),  # Mon 08:00 KL
        datetime(2026, 11, 8, 15, 59, tzinfo=UTC),  # Sun 23:59 KL -> previous week
    ],
)
def test_job_and_export_route_pick_the_same_week_in_kl(
    monkeypatch: pytest.MonkeyPatch, utc: datetime
) -> None:
    _freeze(monkeypatch, utc)
    s = Settings(
        reporting_timezone="Asia/Kuala_Lumpur",
        weekly_report_email_recipients="a@x.com",
    )
    email = MockEmailReport()
    run_weekly_report_job(s, MockMetricsQuery(), email)
    subject = email.sent[0]["subject"]

    class S:
        metrics_api_key = "k"
        reporting_timezone = "Asia/Kuala_Lumpur"

    app = FastAPI()
    app.include_router(build_metrics_export_router(MockMetricsQuery(), S()))  # type: ignore[arg-type]
    r = TestClient(app).get(
        "/metrics/weekly-report/export?format=pdf", headers={"x-api-key": "k"}
    )
    fname = r.headers["content-disposition"]
    start = subject.split()[2]
    assert f"weekly-report-{start}_" in fname


def test_job_week_after_kl_monday_midnight_differs_from_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, datetime(2026, 11, 8, 16, 30, tzinfo=UTC))
    email = MockEmailReport()
    run_weekly_report_job(
        Settings(reporting_timezone="Asia/Kuala_Lumpur", weekly_report_email_recipients="a@x.com"),
        MockMetricsQuery(),
        email,
    )
    assert "2026-11-02 to 2026-11-08" in email.sent[0]["subject"]


def test_export_route_accepts_explicit_window() -> None:
    class S:
        metrics_api_key = "k"

    app = FastAPI()
    app.include_router(build_metrics_export_router(MockMetricsQuery(), S()))  # type: ignore[arg-type]
    c = TestClient(app)
    r = c.get(
        "/metrics/weekly-report/export?format=pdf&from=2026-07-17&to=2026-07-23",
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    assert "weekly-report-2026-07-17_2026-07-23.pdf" in r.headers["content-disposition"]
    bad = c.get(
        "/metrics/weekly-report/export?format=pdf&from=2026-07-23&to=2026-07-17",
        headers={"x-api-key": "k"},
    )
    assert bad.status_code == 400


def test_invalid_day_and_hour_fall_back_to_defaults() -> None:
    s = Settings(weekly_report_email_day="funday", weekly_report_email_hour=99)
    assert (s.weekly_report_email_day, s.weekly_report_email_hour) == ("mon", 8)
    s = Settings(weekly_report_email_day=" FRI ", weekly_report_email_hour=17)
    assert (s.weekly_report_email_day, s.weekly_report_email_hour) == ("fri", 17)


def test_start_scheduler_survives_add_job_failure() -> None:
    class Bad:
        def add_job(self, *a: Any, **k: Any) -> None:
            raise ValueError("bad cron")

        def start(self) -> None: ...

    s = Settings(weekly_report_email_enabled=True)
    assert start_weekly_report_scheduler(s, MockMetricsQuery(), MockEmailReport(), scheduler=Bad()) is None


def test_sections_are_labelled_with_their_scope() -> None:
    report = _report()
    labels = {(s.key, b): lbl for s in report.sections if s.data is not None for b, lbl in wr.block_scope_labels(s.data).items()}
    assert wr.SCOPE_ALL_TIME in labels.values()
    text = _pdf_text(render_weekly_pdf(report))
    assert wr.SCOPE_ALL_TIME in text


def test_scope_ok_reads_this_week_and_unfiltered_reads_all_time() -> None:
    m = DashboardMetrics(
        volume=[VolumeRow(month="2026-11", channel="web", volume=1)],
        resolution=[], csat=[], nps=[], speed=[], fallback=[], bounce=[], quality=[],
    )
    m.attach_scopes(
        {
            "volume": BlockScope(status="ok", period=None, supported_granularity=None),
            "csat": BlockScope(status="unfiltered", period=None, supported_granularity=None),
        }
    )
    labels = wr.block_scope_labels(m)
    assert labels["volume"] == wr.SCOPE_THIS_WEEK
    assert labels["csat"] == wr.SCOPE_ALL_TIME
    assert labels["nps"] == wr.SCOPE_ALL_TIME  # no scope reported -> never claim weekly


def test_xlsx_carries_scope_label() -> None:
    wb = load_workbook(io.BytesIO(render_weekly_xlsx(_report())))
    cells = [c.value for row in wb["summary"].iter_rows() for c in row]
    assert wr.SCOPE_ALL_TIME in cells


def test_pdf_escapes_markup_in_values() -> None:
    m = DashboardMetrics(
        volume=[VolumeRow(month="2026-11", channel="<b>x</b> & <i", volume=1)],
        resolution=[], csat=[], nps=[], speed=[], fallback=[], bounce=[], quality=[],
    )
    report = wr.WeeklyReport(
        period=last_full_week(date(2026, 11, 11)),
        sections=[wr.WeeklySection("dashboard", "Case <Volume>", m)],
    )
    text = _pdf_text(render_weekly_pdf(report))
    assert "<b>x</b> & <i" in text
    assert "Case <Volume>" in text


def test_oversize_attachment_is_skipped_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(sch_mod, "render_weekly_xlsx", lambda _r: b"x" * (21 * 1024 * 1024))
    email = MockEmailReport()
    run_weekly_report_job(
        Settings(weekly_report_email_recipients="a@x.com"), MockMetricsQuery(), email, today=date(2026, 11, 9)
    )
    msg = email.sent[0]
    assert [a[0].rsplit(".", 1)[1] for a in msg["attachments"]] == ["pdf"]
    assert "xlsx" in msg["body"] and "20 MB" in msg["body"]


def test_weekly_xlsx_neutralises_formula_cells() -> None:
    """Final review M1: a dealer/PIC name starting with = + - @ must not run
    as a formula in the downloaded workbook."""
    from chatbot.features.metrics.query_port import DealerEscalationMetrics, DealerEscalationRow

    class Evil(MockMetricsQuery):
        async def fetch_dealer_escalation(self, *a: Any, **k: Any) -> Any:
            return DealerEscalationMetrics(
                by_dealer=[DealerEscalationRow("=cmd|' /C calc'!A0", 1, None, None, None)],
                slowest_cases=[],
            )

    report = asyncio.run(fetch_weekly_report(Evil(), last_full_week(date(2026, 11, 11))))
    wb = load_workbook(io.BytesIO(render_weekly_xlsx(report)))
    cells = [
        c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row
        if isinstance(c.value, str) and "calc" in c.value
    ]
    assert cells == ["'=cmd|' /C calc'!A0"]
