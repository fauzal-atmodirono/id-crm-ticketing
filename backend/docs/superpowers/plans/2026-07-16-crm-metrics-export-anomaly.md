# CRM Metrics Export + Anomaly Implementation Plan (Short-Term Item 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PDF/Excel export of the metrics dashboard, scheduled email delivery of that report to management, and a channel-volume anomaly signal (view + endpoint + alert email) — closing the remaining BRD block-F items.

**Architecture:** Pure render functions turn the existing `DashboardMetrics` DTO into xlsx/pdf bytes; a `GET /metrics/export` endpoint streams them. An `EmailReportPort` (SMTP adapter + mock) sends attachments; the existing APScheduler runs a report job on an interval. Anomaly detection is a `v_channel_anomaly` view returning per-channel current-vs-baseline volume, plus a pure `flag_anomalies()` that applies a configurable z-score threshold, exposed at `GET /metrics/anomalies` and used by a scheduled alert. All new I/O sits behind ports with mocks; all math/rendering is pure and unit-tested.

**Tech Stack:** Python 3.12, `openpyxl` (Excel), `reportlab` (PDF), stdlib `smtplib`/`email` (SMTP), `apscheduler` (already present), `google-cloud-bigquery`, `fastapi`, `pytest`. Run backend commands from `apps/backend/`.

## Global Constraints

- Run from `apps/backend/`; venv `.venv/`.
- Gates before each commit: `.venv/bin/ruff format .` · `.venv/bin/ruff check . --fix` · `.venv/bin/mypy src/ --strict` · `.venv/bin/pytest src/`.
- Baselines (ZERO new): full suite currently **366** passing; mypy --strict **3** pre-existing (firestore_session_service.py:12, service.py:179, test_service.py:10); ruff **1** pre-existing (PLC0415 vertex_search.py).
- New endpoints are additive and unauthenticated (POC, consistent with existing `/metrics/dashboard`); channel-level aggregates only, no PII.
- New config fields have safe defaults; the report scheduler and SMTP are DISABLED by default (`report_enabled=False`) so nothing emails unless configured.
- All new I/O behind ports (`EmailReportPort`) with a mock; render/anomaly math is pure.
- Conventional commits `feat(metrics): …`. Branch `feature/crm-metrics-export-anomaly`. Do not push.

## File structure

- Create `src/chatbot/features/metrics/export.py` — pure `render_xlsx` / `render_pdf`.
- Create `src/chatbot/features/metrics/export_router.py` — `GET /metrics/export`.
- Create `src/chatbot/features/metrics/email_port.py` — `EmailReportPort` Protocol, `SmtpEmailReport` adapter, `MockEmailReport`, `build_email_report_port`.
- Create `src/chatbot/features/metrics/anomaly.py` — `AnomalyRow`, `Anomaly`, pure `flag_anomalies`.
- Create `src/chatbot/features/metrics/anomaly_router.py` — `GET /metrics/anomalies`.
- Modify `bigquery_schema.py` (add `v_channel_anomaly` view), `query_port.py` (+`fetch_anomalies`, mock), `query_adapter.py` (+`fetch_anomalies`), `scheduler.py` (report + alert jobs), `platform/config.py` (+fields), `.env.example`, `main.py` (wiring), `pyproject.toml` (+deps).
- Co-located `test_*.py` for each new module.

---

### Task 1: Dependencies + config fields

**Files:**
- Modify: `pyproject.toml` (dependencies), `uv.lock` (regenerated)
- Modify: `src/chatbot/platform/config.py` (Settings), `.env.example`
- Test: `src/chatbot/features/metrics/test_config_report.py` (new)

**Interfaces:**
- Produces on `Settings`: `smtp_host: str=""`, `smtp_port: int=587`, `smtp_user: str=""`, `smtp_password: str=""`, `smtp_from: str=""`, `report_recipients: str=""` (comma-separated), `report_enabled: bool=False`, `report_interval_hours: int=24`, `anomaly_zscore_k: float=3.0`, `anomaly_min_baseline: int=20`. Helper `report_recipient_list() -> list[str]` splitting/stripping `report_recipients`.

- [ ] **Step 1: Add deps** — in `pyproject.toml` `dependencies`, add `"openpyxl>=3.1.5"` and `"reportlab>=4.2.5"`. Then run `.venv/bin/uv sync` (updates `uv.lock` and installs).

Run: `.venv/bin/python -c "import openpyxl, reportlab; print('ok')"`
Expected: `ok`.

- [ ] **Step 2: Write failing config test** — `test_config_report.py`:

```python
from chatbot.platform.config import Settings


def test_report_defaults_are_safe() -> None:
    s = Settings()
    assert s.report_enabled is False
    assert s.smtp_port == 587
    assert s.anomaly_zscore_k == 3.0
    assert s.anomaly_min_baseline == 20


def test_report_recipient_list_splits_and_strips() -> None:
    s = Settings(report_recipients="a@x.com, b@y.com ,")
    assert s.report_recipient_list() == ["a@x.com", "b@y.com"]
```

- [ ] **Step 3: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_config_report.py -q`
Expected: FAIL (`AttributeError: report_enabled`).

- [ ] **Step 4: Implement config** — add the fields to `Settings` (near the other `metrics_`/`bigquery_` fields, ~line 79-88) with the defaults above, and add:

```python
    def report_recipient_list(self) -> list[str]:
        return [r.strip() for r in self.report_recipients.split(",") if r.strip()]
```

Add matching commented entries to `.env.example` (SMTP_HOST, SMTP_PORT, …, REPORT_ENABLED, ANOMALY_ZSCORE_K, ANOMALY_MIN_BASELINE).

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_config_report.py -q`
Expected: PASS.

- [ ] **Step 6: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add pyproject.toml uv.lock src/chatbot/platform/config.py .env.example src/chatbot/features/metrics/test_config_report.py
git commit -m "feat(metrics): add export/report/anomaly deps and config fields"
```

---

### Task 2: Export render module (pure)

**Files:**
- Create: `src/chatbot/features/metrics/export.py`
- Test: `src/chatbot/features/metrics/test_export.py`

**Interfaces:**
- Consumes: `DashboardMetrics` (from `query_port.py`).
- Produces: `render_xlsx(metrics: DashboardMetrics) -> bytes` (one worksheet per metric block, header row = the block's dataclass field names). `render_pdf(metrics: DashboardMetrics) -> bytes` (title + one table per block).

- [ ] **Step 1: Write failing tests** — `test_export.py`:

```python
from dataclasses import fields

from chatbot.features.metrics.export import render_pdf, render_xlsx
from chatbot.features.metrics.query_port import MockMetricsQuery, DashboardMetrics


async def _metrics() -> DashboardMetrics:
    return await MockMetricsQuery().fetch_dashboard()


def _sync_metrics() -> DashboardMetrics:
    import asyncio
    return asyncio.run(_metrics())


def test_render_xlsx_is_a_zip_workbook() -> None:
    data = render_xlsx(_sync_metrics())
    assert data[:2] == b"PK"  # xlsx is a zip container
    assert len(data) > 500


def test_render_xlsx_has_a_sheet_per_block() -> None:
    import io
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(render_xlsx(_sync_metrics())))
    for block in [f.name for f in fields(DashboardMetrics)]:
        assert block in wb.sheetnames


def test_render_pdf_has_pdf_header() -> None:
    data = render_pdf(_sync_metrics())
    assert data[:5] == b"%PDF-"
    assert len(data) > 500
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_export.py -q`
Expected: FAIL (`ModuleNotFoundError: export`).

- [ ] **Step 3: Implement `export.py`**

```python
"""Pure renderers: DashboardMetrics -> xlsx/pdf bytes (no I/O, no network)."""

from __future__ import annotations

import io
from dataclasses import astuple, fields
from typing import TYPE_CHECKING

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import DashboardMetrics


def _blocks(metrics: DashboardMetrics) -> list[tuple[str, list[object]]]:
    return [(f.name, getattr(metrics, f.name)) for f in fields(metrics)]


def render_xlsx(metrics: DashboardMetrics) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)  # drop the default sheet
    for name, rows in _blocks(metrics):
        ws = wb.create_sheet(title=name[:31])  # Excel sheet-name limit
        if rows:
            ws.append([f.name for f in fields(rows[0])])
            for row in rows:
                ws.append(list(astuple(row)))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render_pdf(metrics: DashboardMetrics) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    story: list[object] = [Paragraph("Bot Metrics Report", styles["Title"]), Spacer(1, 12)]
    for name, rows in _blocks(metrics):
        story.append(Paragraph(name, styles["Heading2"]))
        if rows:
            header = [f.name for f in fields(rows[0])]
            data = [header] + [[str(v) for v in astuple(r)] for r in rows]
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_export.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/export.py src/chatbot/features/metrics/test_export.py
git commit -m "feat(metrics): render dashboard metrics to xlsx/pdf bytes"
```

---

### Task 3: Export endpoint

**Files:**
- Create: `src/chatbot/features/metrics/export_router.py`
- Test: `src/chatbot/features/metrics/test_export_router.py`

**Interfaces:**
- Consumes: `MetricsQueryPort`, `render_xlsx`, `render_pdf`.
- Produces: `build_metrics_export_router(port: MetricsQueryPort) -> APIRouter` with `GET /metrics/export?format=xlsx|pdf` returning a streaming file (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` / `application/pdf`), `Content-Disposition: attachment`. Invalid format → HTTP 400.

- [ ] **Step 1: Write failing test** — `test_export_router.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.export_router import build_metrics_export_router
from chatbot.features.metrics.query_port import MockMetricsQuery


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_metrics_export_router(MockMetricsQuery()))
    return TestClient(app)


def test_export_xlsx() -> None:
    r = _client().get("/metrics/export?format=xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]


def test_export_pdf() -> None:
    r = _client().get("/metrics/export?format=pdf")
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


def test_export_bad_format_is_400() -> None:
    assert _client().get("/metrics/export?format=csv").status_code == 400
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_export_router.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `export_router.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_export_router.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/export_router.py src/chatbot/features/metrics/test_export_router.py
git commit -m "feat(metrics): add GET /metrics/export xlsx/pdf endpoint"
```

---

### Task 4: EmailReportPort (SMTP adapter + mock)

**Files:**
- Create: `src/chatbot/features/metrics/email_port.py`
- Test: `src/chatbot/features/metrics/test_email_port.py`

**Interfaces:**
- Produces: `Attachment = tuple[str, bytes, str]` (filename, content, mimetype). `EmailReportPort` Protocol: `send_report(self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]) -> None`. `MockEmailReport` (records calls in `.sent`). `SmtpEmailReport(settings, *, smtp_factory=None)` building a `EmailMessage` and sending via `smtplib.SMTP`. `build_email_report_port(settings) -> EmailReportPort` (returns `SmtpEmailReport` if `report_enabled and smtp_host` else `MockEmailReport`).

- [ ] **Step 1: Write failing tests** — `test_email_port.py`:

```python
from chatbot.features.metrics.email_port import MockEmailReport, SmtpEmailReport
from chatbot.platform.config import Settings


def test_mock_records_send() -> None:
    m = MockEmailReport()
    m.send_report(["a@x.com"], "subj", "body", [("f.xlsx", b"PK", "application/octet-stream")])
    assert m.sent[0]["recipients"] == ["a@x.com"]
    assert m.sent[0]["attachments"][0][0] == "f.xlsx"


def test_smtp_builds_and_sends_message() -> None:
    captured: dict[str, object] = {}

    class _SMTP:
        def __init__(self, host: str, port: int) -> None:
            captured["host"] = host
        def __enter__(self) -> "_SMTP":
            return self
        def __exit__(self, *a: object) -> None: ...
        def starttls(self) -> None: captured["tls"] = True
        def login(self, u: str, p: str) -> None: captured["user"] = u
        def send_message(self, msg: object) -> None: captured["msg"] = msg

    s = Settings(smtp_host="smtp.test", smtp_user="u", smtp_password="p", smtp_from="from@x.com")
    SmtpEmailReport(s, smtp_factory=_SMTP).send_report(
        ["a@x.com"], "subj", "body", [("f.xlsx", b"PK", "application/octet-stream")]
    )
    assert captured["host"] == "smtp.test"
    assert captured["user"] == "u"
    msg = captured["msg"]
    assert msg["To"] == "a@x.com" and msg["Subject"] == "subj"
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_email_port.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `email_port.py`**

```python
"""EmailReportPort: send the metrics report as an email with attachments."""

from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from smtplib import SMTP
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

Attachment = tuple[str, bytes, str]  # (filename, content, mimetype)


class EmailReportPort(Protocol):
    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None: ...


class MockEmailReport:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None:
        self.sent.append(
            {"recipients": recipients, "subject": subject, "body": body, "attachments": attachments}
        )


class SmtpEmailReport:
    def __init__(
        self, settings: Settings, *, smtp_factory: Callable[[str, int], Any] | None = None
    ) -> None:
        self._s = settings
        self._smtp = smtp_factory or SMTP

    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None:
        if not recipients:
            return
        msg = EmailMessage()
        msg["From"] = self._s.smtp_from
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body)
        for filename, content, mimetype in attachments:
            maintype, _, subtype = mimetype.partition("/")
            msg.add_attachment(
                content, maintype=maintype or "application", subtype=subtype or "octet-stream",
                filename=filename,
            )
        with self._smtp(self._s.smtp_host, self._s.smtp_port) as smtp:
            smtp.starttls()
            if self._s.smtp_user:
                smtp.login(self._s.smtp_user, self._s.smtp_password)
            smtp.send_message(msg)
        _log.info("metrics_report_email_sent", recipients=len(recipients))


def build_email_report_port(settings: Settings) -> EmailReportPort:
    if settings.report_enabled and settings.smtp_host:
        return SmtpEmailReport(settings)
    return MockEmailReport()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_email_port.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/email_port.py src/chatbot/features/metrics/test_email_port.py
git commit -m "feat(metrics): add EmailReportPort with SMTP adapter and mock"
```

---

### Task 5: Anomaly view + pure flagging + fetch

**Files:**
- Create: `src/chatbot/features/metrics/anomaly.py`
- Modify: `bigquery_schema.py` (add `v_channel_anomaly`), `query_port.py` (+`AnomalyRow`, `fetch_anomalies` on Protocol + Mock), `query_adapter.py` (+`fetch_anomalies`)
- Test: `src/chatbot/features/metrics/test_anomaly.py`, extend `test_bigquery_schema.py`

**Interfaces:**
- Produces in `query_port.py`: `@dataclass(frozen=True) AnomalyRow{channel:str, current_volume:int, baseline_mean:float|None, baseline_stddev:float|None}`; `MetricsQueryPort` gains `async fetch_anomalies() -> list[AnomalyRow]`; `MockMetricsQuery.fetch_anomalies` returns a representative list (one normal, one spike). In `anomaly.py`: `@dataclass(frozen=True) Anomaly{channel:str, current_volume:int, baseline_mean:float, z_score:float}`; pure `flag_anomalies(rows: list[AnomalyRow], k: float, min_baseline: int) -> list[Anomaly]` — flags a row when `baseline_stddev` is set and `> 0`, `baseline_mean >= min_baseline`, and `current_volume > baseline_mean + k*baseline_stddev`. `v_channel_anomaly` view key added to `view_ddls`.

- [ ] **Step 1: Write failing tests** — `test_anomaly.py`:

```python
from chatbot.features.metrics.anomaly import Anomaly, flag_anomalies
from chatbot.features.metrics.query_port import AnomalyRow


def test_flags_spike_above_threshold() -> None:
    rows = [AnomalyRow("web", current_volume=200, baseline_mean=100.0, baseline_stddev=20.0)]
    out = flag_anomalies(rows, k=3.0, min_baseline=20)
    assert len(out) == 1 and out[0].channel == "web"
    assert out[0].z_score == 5.0


def test_ignores_within_threshold() -> None:
    rows = [AnomalyRow("web", current_volume=140, baseline_mean=100.0, baseline_stddev=20.0)]
    assert flag_anomalies(rows, k=3.0, min_baseline=20) == []


def test_ignores_low_baseline() -> None:
    rows = [AnomalyRow("web", current_volume=999, baseline_mean=5.0, baseline_stddev=1.0)]
    assert flag_anomalies(rows, k=3.0, min_baseline=20) == []


def test_ignores_zero_or_missing_stddev() -> None:
    rows = [
        AnomalyRow("web", 200, 100.0, 0.0),
        AnomalyRow("wa", 200, 100.0, None),
    ]
    assert flag_anomalies(rows, k=3.0, min_baseline=20) == []
```

Extend `test_bigquery_schema.py`:

```python
def test_view_ddls_include_anomaly_view() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert "v_channel_anomaly" in ddls
    assert "v_channel_anomaly" in ddls["v_channel_anomaly"]
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_anomaly.py src/chatbot/features/metrics/test_bigquery_schema.py -k "anomaly" -q`
Expected: FAIL (`ModuleNotFoundError` / missing view).

- [ ] **Step 3: Implement `AnomalyRow` + mock** — in `query_port.py`, add the dataclass (after `QualityRow`), add `fetch_anomalies` to the `MetricsQueryPort` Protocol, and implement it on `MockMetricsQuery`:

```python
@dataclass(frozen=True)
class AnomalyRow:
    channel: str
    current_volume: int
    baseline_mean: float | None
    baseline_stddev: float | None
```

```python
    async def fetch_anomalies(self) -> list[AnomalyRow]:
        return [
            AnomalyRow("web", current_volume=130, baseline_mean=125.0, baseline_stddev=10.0),
            AnomalyRow("whatsapp", current_volume=260, baseline_mean=90.0, baseline_stddev=15.0),
        ]
```

Add to the Protocol: `async def fetch_anomalies(self) -> list[AnomalyRow]: ...`

- [ ] **Step 4: Implement `anomaly.py`**

```python
"""Pure channel-volume anomaly flagging over baseline stats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import AnomalyRow


@dataclass(frozen=True)
class Anomaly:
    channel: str
    current_volume: int
    baseline_mean: float
    z_score: float


def flag_anomalies(rows: list[AnomalyRow], k: float, min_baseline: int) -> list[Anomaly]:
    out: list[Anomaly] = []
    for r in rows:
        if r.baseline_stddev is None or r.baseline_stddev <= 0 or r.baseline_mean is None:
            continue
        if r.baseline_mean < min_baseline:
            continue
        z = (r.current_volume - r.baseline_mean) / r.baseline_stddev
        if z > k:
            out.append(Anomaly(r.channel, r.current_volume, r.baseline_mean, z))
    return out
```

- [ ] **Step 5: Implement `v_channel_anomaly` view** — add to the `view_ddls` dict (compares yesterday's per-channel volume against the trailing 7-day daily mean/stddev):

```python
        "v_channel_anomaly": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_channel_anomaly` AS "
            f"WITH daily AS (SELECT channel, DATE(created_at) AS d, COUNT(*) AS v "
            f"FROM {fq} WHERE created_at IS NOT NULL GROUP BY channel, d), "
            f"cur AS (SELECT channel, v AS current_volume FROM daily "
            f"WHERE d = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)), "
            f"base AS (SELECT channel, AVG(v) AS baseline_mean, STDDEV(v) AS baseline_stddev "
            f"FROM daily WHERE d BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 8 DAY) "
            f"AND DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) GROUP BY channel) "
            f"SELECT b.channel, COALESCE(c.current_volume, 0) AS current_volume, "
            f"b.baseline_mean, b.baseline_stddev "
            f"FROM base b LEFT JOIN cur c USING (channel)"
        ),
```

- [ ] **Step 6: Implement adapter `fetch_anomalies`** — in `query_adapter.py`, import `AnomalyRow`, and add:

```python
    def _fetch_anomalies_sync(self) -> list["AnomalyRow"]:
        from chatbot.features.metrics.query_port import AnomalyRow
        return self._block("v_channel_anomaly", AnomalyRow)

    async def fetch_anomalies(self) -> list["AnomalyRow"]:
        return await asyncio.to_thread(self._fetch_anomalies_sync)
```

(Reuses the existing `_block` degrade-to-empty helper. Prefer a top-of-file `AnomalyRow` import over the inline one if it keeps mypy happy.)

- [ ] **Step 7: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_anomaly.py src/chatbot/features/metrics/test_bigquery_schema.py src/chatbot/features/metrics/test_query_adapter.py -q`
Expected: PASS.

- [ ] **Step 8: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/anomaly.py src/chatbot/features/metrics/query_port.py src/chatbot/features/metrics/query_adapter.py src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/test_anomaly.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): add channel-volume anomaly view, flagging, and fetch"
```

---

### Task 6: Anomaly endpoint

**Files:**
- Create: `src/chatbot/features/metrics/anomaly_router.py`
- Test: `src/chatbot/features/metrics/test_anomaly_router.py`

**Interfaces:**
- Consumes: `MetricsQueryPort.fetch_anomalies`, `flag_anomalies`, `Settings` (for `anomaly_zscore_k`, `anomaly_min_baseline`).
- Produces: `build_metrics_anomaly_router(port, settings) -> APIRouter` with `GET /metrics/anomalies` → `{"anomalies": [asdict(Anomaly), ...]}`.

- [ ] **Step 1: Write failing test** — `test_anomaly_router.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.anomaly_router import build_metrics_anomaly_router
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.platform.config import Settings


def test_anomalies_endpoint_flags_the_spike() -> None:
    app = FastAPI()
    app.include_router(build_metrics_anomaly_router(MockMetricsQuery(), Settings()))
    r = TestClient(app).get("/metrics/anomalies")
    assert r.status_code == 200
    chans = [a["channel"] for a in r.json()["anomalies"]]
    assert "whatsapp" in chans and "web" not in chans
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_anomaly_router.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `anomaly_router.py`**

```python
"""GET /metrics/anomalies — currently-flagged channel-volume anomalies."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from chatbot.features.metrics.anomaly import flag_anomalies

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


def build_metrics_anomaly_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/anomalies")
    async def anomalies() -> dict[str, Any]:
        rows = await port.fetch_anomalies()
        flagged = flag_anomalies(rows, settings.anomaly_zscore_k, settings.anomaly_min_baseline)
        return {"anomalies": [asdict(a) for a in flagged]}

    return router
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_anomaly_router.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/anomaly_router.py src/chatbot/features/metrics/test_anomaly_router.py
git commit -m "feat(metrics): add GET /metrics/anomalies endpoint"
```

---

### Task 7: Scheduled report + anomaly alert jobs

**Files:**
- Modify: `src/chatbot/features/metrics/scheduler.py`
- Test: `src/chatbot/features/metrics/test_scheduler.py` (extend)

**Interfaces:**
- Consumes: `MetricsQueryPort`, `EmailReportPort`, `render_xlsx`/`render_pdf`, `flag_anomalies`, `Settings`.
- Produces: `run_report_job(settings, query_port, email_port)` — best-effort (never raises); renders xlsx+pdf, emails to `settings.report_recipient_list()` when non-empty; and evaluates anomalies, emailing an alert when any are flagged. `start_report_scheduler(settings, query_port, email_port, *, scheduler=None, job=None)` — adds an interval job (`report_interval_hours`) only when `settings.report_enabled`; else returns None.

- [ ] **Step 1: Write failing tests** — extend `test_scheduler.py`:

```python
import asyncio

from chatbot.features.metrics.email_port import MockEmailReport
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.features.metrics.scheduler import run_report_job, start_report_scheduler
from chatbot.platform.config import Settings


def test_run_report_job_emails_report_and_alert() -> None:
    email = MockEmailReport()
    s = Settings(report_recipients="mgmt@x.com")
    run_report_job(s, MockMetricsQuery(), email)
    # one report email (with 2 attachments) + one anomaly alert (mock has a spike)
    assert len(email.sent) == 2
    report = next(m for m in email.sent if len(m["attachments"]) == 2)
    assert {a[0] for a in report["attachments"]} == {"bot-metrics.xlsx", "bot-metrics.pdf"}


def test_run_report_job_no_recipients_no_report_email() -> None:
    email = MockEmailReport()
    run_report_job(Settings(report_recipients=""), MockMetricsQuery(), email)
    # no report email; anomaly alert also needs recipients → nothing sent
    assert email.sent == []


def test_start_report_scheduler_disabled_returns_none() -> None:
    assert start_report_scheduler(Settings(report_enabled=False), MockMetricsQuery(), MockEmailReport()) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_scheduler.py -k "report" -q`
Expected: FAIL (`ImportError: run_report_job`).

- [ ] **Step 3: Implement in `scheduler.py`** — add (imports at top: `asyncio`, `render_xlsx`, `render_pdf`, `flag_anomalies`, and typing for the ports):

```python
def run_report_job(
    settings: "Settings",
    query_port: "MetricsQueryPort",
    email_port: "EmailReportPort",
) -> None:
    """Render + email the metrics report and any anomaly alert. Never raises."""
    try:
        recipients = settings.report_recipient_list()
        metrics = asyncio.run(query_port.fetch_dashboard())
        if recipients:
            email_port.send_report(
                recipients,
                "Bot Metrics Report",
                "Attached: the latest bot metrics (xlsx + pdf).",
                [
                    ("bot-metrics.xlsx", render_xlsx(metrics), _XLSX_MIME),
                    ("bot-metrics.pdf", render_pdf(metrics), "application/pdf"),
                ],
            )
        rows = asyncio.run(query_port.fetch_anomalies())
        flagged = flag_anomalies(rows, settings.anomaly_zscore_k, settings.anomaly_min_baseline)
        if flagged and recipients:
            lines = "\n".join(
                f"- {a.channel}: {a.current_volume} (baseline {a.baseline_mean:.0f}, z={a.z_score:.1f})"
                for a in flagged
            )
            email_port.send_report(
                recipients, "⚠️ Channel anomaly detected", f"Anomalies:\n{lines}", []
            )
    except Exception as e:  # a scheduled report must never crash the app
        _log.error("metrics_report_job_failed", error=str(e))


def start_report_scheduler(
    settings: "Settings",
    query_port: "MetricsQueryPort",
    email_port: "EmailReportPort",
    *,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    if not settings.report_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_report_job(settings, query_port, email_port))
    sched.add_job(
        run, trigger="interval", hours=settings.report_interval_hours,
        id="metrics_report", replace_existing=True,
    )
    sched.start()
    _log.info("metrics_report_scheduler_started", interval_hours=settings.report_interval_hours)
    return sched
```

Add module constant `_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"` and the necessary imports (`from chatbot.features.metrics.export import render_pdf, render_xlsx`; `from chatbot.features.metrics.anomaly import flag_anomalies`; TYPE_CHECKING imports for `MetricsQueryPort`, `EmailReportPort`).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_scheduler.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/scheduler.py src/chatbot/features/metrics/test_scheduler.py
git commit -m "feat(metrics): scheduled report email + anomaly alert job"
```

---

### Task 8: Wiring + full suite + docs

**Files:**
- Modify: `src/chatbot/main.py` (register export + anomaly routers; start report scheduler)
- Create: `docs/dashboards/export-anomaly.md`
- Test: existing integration test for metrics wiring (extend if present)

**Interfaces:**
- Consumes: `build_metrics_export_router`, `build_metrics_anomaly_router`, `build_email_report_port`, `start_report_scheduler`, `build_metrics_query_port`.

- [ ] **Step 1: Wire in `main.py`** — inside the existing `_wire_metrics_features` (where `build_metrics_query_port` + `build_metrics_query_router` are already wired), add:

```python
    from chatbot.features.metrics.anomaly_router import build_metrics_anomaly_router
    from chatbot.features.metrics.email_port import build_email_report_port
    from chatbot.features.metrics.export_router import build_metrics_export_router
    from chatbot.features.metrics.scheduler import start_report_scheduler

    app.include_router(build_metrics_export_router(query_port))
    app.include_router(build_metrics_anomaly_router(query_port, settings))
    start_report_scheduler(settings, query_port, build_email_report_port(settings))
```

(Reuse the `query_port` and `settings` already in scope in that function; match its existing style.)

- [ ] **Step 2: Boot + endpoint smoke test** — extend the metrics wiring integration test (or add one) that boots the app and asserts `GET /metrics/export?format=xlsx` returns 200 with `PK` bytes and `GET /metrics/anomalies` returns 200 with an `anomalies` key.

Run: `.venv/bin/pytest src/chatbot -k "wiring or main or export or anomaly" -q`
Expected: PASS.

- [ ] **Step 3: Full suite + gates**

Run: `.venv/bin/pytest src/ -q` → all green.
Run: `.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict` → 1 ruff / 3 mypy baseline only.

- [ ] **Step 4: Docs** — `docs/dashboards/export-anomaly.md`: document `GET /metrics/export?format=xlsx|pdf`, `GET /metrics/anomalies`, the SMTP/report config keys, `report_enabled` default-off, and the anomaly view's baseline window + z-score threshold.

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/main.py docs/dashboards/export-anomaly.md src/chatbot/features/metrics/
git commit -m "feat(metrics): wire export/anomaly routers + report scheduler; docs"
```

---

## Self-review

**Spec coverage (Item 2 of the design):**
- Export module xlsx/pdf → Task 2. ✓
- `GET /metrics/export` → Task 3. ✓
- `EmailReportPort` + SMTP adapter + mock + config → Tasks 1, 4. ✓
- Scheduled delivery job → Task 7. ✓
- `v_channel_anomaly` view + baseline vs current → Task 5. ✓
- `GET /metrics/anomalies` → Task 6. ✓
- Scheduler alert email on breach → Task 7. ✓
- Wiring + docs → Task 8. ✓

**Placeholder scan:** Task 5 adapter shows inline-vs-top import (prefer top); Task 8 references "match existing style" of `_wire_metrics_features` — the concrete include-router lines are given. No TBD/TODO.

**Type consistency:** `AnomalyRow` fields (channel/current_volume/baseline_mean/baseline_stddev) are identical across query_port, the view SELECT aliases, `flag_anomalies`, and the mock. `Attachment` tuple shape `(filename, bytes, mimetype)` is consistent across email_port and the scheduler job. `render_xlsx`/`render_pdf` signatures match across export, router, and scheduler.

## Deferred to later plans
- Item 3 (SLA escalation + audit), Item 4 (Zendesk-native routing config), Item 5 (agent-assist FAQ).
