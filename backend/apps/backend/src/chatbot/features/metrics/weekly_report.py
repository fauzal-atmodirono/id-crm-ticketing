"""Weekly management report (FIX-14): one bundle, rendered as PDF and XLSX.

RFP 4.74 / 4.78 / 4.82 ask for reports that export to PDF, Excel and CSV and
are sent to management on a regular basis. The pieces already existed
(`render_pdf`/`render_xlsx` for the dashboard bundle only, an interval email
job) but the emailed PDF covered just the bot-metrics dashboard, not the
sections of the Weekly Report page. This module gathers every section that
page shows -- case volume, status trend, dealer escalation, SLA, aging,
type/division volume, departments, call centre -- for one Monday-Sunday week
and renders them with the same `_blocks()` reflection the other exports use,
so a new metric block needs no per-view code here.

Failure semantics: each section is fetched independently. A section whose
query raises is kept in the report with an explicit "unavailable" marker and
the error class -- never dropped. A management reader must be able to tell
"nothing happened" from "we could not measure it"; silently omitting the
section would present the second as the first. Rendering itself is pure (no
I/O); only `fetch_weekly_report` touches the query port.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import structlog
from openpyxl import Workbook  # type: ignore[import-untyped]
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4, landscape  # type: ignore[import-untyped]
from reportlab.lib.styles import getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from chatbot.features.metrics.export import _blocks, _exportable_field_names, spreadsheet_safe
from chatbot.features.metrics.period import PeriodRange

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort

_log = structlog.get_logger(__name__)

# key -> heading, in page order. The PDF test asserts every heading is present.
SECTION_TITLES: dict[str, str] = {
    "dashboard": "Case Volume and Bot Quality",
    "lifecycle": "Case Status Trend",
    "dealer_escalation": "Dealer Escalation",
    "sla_buckets": "SLA Buckets",
    "case_aging": "Case Aging",
    "volume_by_type": "Volume by Type and Division",
    "departments": "Departments",
    "call_centre": "Call Centre",
}


@dataclass
class WeeklySection:
    key: str
    title: str
    data: Any | None  # a dataclass of list[<dataclass>] blocks, or None when unavailable
    error: str | None = None


@dataclass
class WeeklyReport:
    period: PeriodRange
    sections: list[WeeklySection] = field(default_factory=list)


SCOPE_THIS_WEEK = "this week"
SCOPE_ALL_TIME = "all time (not period-scoped)"
SCOPE_UNSUPPORTED = "not available for a weekly window"
SCOPE_UNAVAILABLE = "unavailable"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def local_today(tz_name: str) -> date:
    """Today's date in the reporting calendar (REPORTING_TIMEZONE).

    "Last full week" must be computed on the same calendar the scheduler fires
    on: at Mon 00:30 Kuala Lumpur it is still Sunday in UTC, so a UTC date
    would send the week before the one management expects. Unknown zone names
    fall back to UTC rather than raising inside a scheduled job.
    """
    try:
        tz: Any = ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    return _utcnow().astimezone(tz).date()


def block_scope_labels(data: Any) -> dict[str, str]:
    """Per block: is it really this week's figure, or all-time data under a weekly heading?

    Only a block whose BlockScope status is "ok" had the period applied. A block
    with no scope reported is labelled all-time: claiming "weekly" without
    evidence is the misreading this label exists to prevent.
    """
    scopes = getattr(data, "scopes", None) or {}
    out: dict[str, str] = {}
    for name, _rows in _blocks(data):
        status = getattr(scopes.get(name), "status", None)
        out[name] = {
            "ok": SCOPE_THIS_WEEK,
            "unsupported_granularity": SCOPE_UNSUPPORTED,
            "unavailable": SCOPE_UNAVAILABLE,
        }.get(status or "", SCOPE_ALL_TIME)
    return out


def last_full_week(today: date) -> PeriodRange:
    """The most recent complete Monday-Sunday week before `today`'s week."""
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(days=7)
    return PeriodRange(start, start + timedelta(days=6), "week")


def week_containing(d: date) -> PeriodRange:
    start = d - timedelta(days=d.weekday())
    return PeriodRange(start, start + timedelta(days=6), "week")


async def fetch_weekly_report(port: MetricsQueryPort, period: PeriodRange) -> WeeklyReport:
    fetchers: dict[str, Callable[[], Awaitable[Any]]] = {
        "dashboard": lambda: port.fetch_dashboard(period),
        "lifecycle": lambda: port.fetch_lifecycle(period),
        "dealer_escalation": lambda: port.fetch_dealer_escalation(period),
        "sla_buckets": lambda: port.fetch_sla_buckets(period),
        "case_aging": lambda: port.fetch_case_aging(period),
        "volume_by_type": lambda: port.fetch_volume_by_type_division(period),
        "departments": lambda: port.fetch_departments(period),
        "call_centre": lambda: port.fetch_callcenter(period),
    }
    report = WeeklyReport(period=period)
    for key, fetch in fetchers.items():
        title = SECTION_TITLES[key]
        try:
            report.sections.append(WeeklySection(key, title, await fetch()))
        except Exception as exc:  # one broken view must not sink the whole report
            _log.warning("weekly_report_section_failed", section=key, error=str(exc))
            report.sections.append(WeeklySection(key, title, None, type(exc).__name__))
    return report


def _period_label(p: PeriodRange) -> str:
    return f"{p.start.isoformat()} to {p.end.isoformat()}"


def render_weekly_pdf(report: WeeklyReport) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title="Weekly Report")
    styles = getSampleStyleSheet()
    story: list[Any] = [
        Paragraph("Weekly Report", styles["Title"]),
        Paragraph(f"Week {_period_label(report.period)}", styles["Normal"]),
        Paragraph(
            "Each block below states whether it covers this week or all time; "
            "all-time blocks are not weekly figures.",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    for sec in report.sections:
        story.append(Paragraph(escape(sec.title), styles["Heading1"]))
        if sec.data is None:
            story.append(
                Paragraph(f"Section unavailable ({escape(str(sec.error))}). Not measured, not zero.", styles["Normal"])
            )
            continue
        labels = block_scope_labels(sec.data)
        for name, rows in _blocks(sec.data):
            story.append(Paragraph(f"{escape(name)} - {labels[name]}", styles["Heading3"]))
            if not rows:
                story.append(Paragraph("(no data)", styles["Normal"]))
                continue
            names = _exportable_field_names(rows)
            cell = styles["BodyText"].clone("cell", fontSize=6, leading=7)
            data = [[Paragraph(escape(n), cell) for n in names]] + [
                [Paragraph(escape(str(getattr(cast(Any, r), n))), cell) for n in names] for r in rows
            ]
            table = Table(data, repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 8))
    doc.build(story)
    return buf.getvalue()


def render_weekly_xlsx(report: WeeklyReport) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = "summary"
    summary.append(["Weekly Report", _period_label(report.period)])
    summary.append(["section", "block", "scope", "status"])
    for sec in report.sections:
        if sec.data is None:
            summary.append([sec.title, "", "", f"unavailable ({sec.error})"])
            continue
        for name, scope in block_scope_labels(sec.data).items():
            summary.append([sec.title, name, scope, "ok"])
    for sec in report.sections:
        if sec.data is None:
            continue
        for name, rows in _blocks(sec.data):
            ws = wb.create_sheet(title=f"{sec.key}.{name}"[:31])  # Excel sheet-name limit
            ws.append(["scope", block_scope_labels(sec.data)[name]])
            if rows:
                names = _exportable_field_names(rows)
                ws.append(names)
                for r in rows:
                    ws.append([spreadsheet_safe(getattr(cast(Any, r), n)) for n in names])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
