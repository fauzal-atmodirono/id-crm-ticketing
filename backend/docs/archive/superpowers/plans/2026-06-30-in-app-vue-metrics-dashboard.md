# In-App Vue Metrics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an embedded `/dashboard` page to the existing Vue SPA that renders the 8 existing Bot-Metrics BigQuery views via a thin backend read-side and ApexCharts tiles.

**Architecture:** Backend gains a read-side in `features/metrics` — a `MetricsQueryPort` with a BigQuery adapter (SELECTs the 8 views) and a mock, exposed as one `GET /metrics/dashboard` endpoint. Frontend gains a `features/dashboard` slice, vue-router, and ApexCharts tiles. No metric logic is duplicated; the SQL views remain the source of truth.

**Tech Stack:** Python 3 / FastAPI / google-cloud-bigquery / pytest (backend); Vue 3 / Vite / Pinia / TypeScript / vue-router / vue3-apexcharts (frontend).

## Global Constraints

- Backend run dir: `apps/backend/`. Frontend run dir: `apps/frontend/`.
- Backend: `ruff format .`, `ruff check . --fix`, `mypy src/ --strict`, `pytest src/` must pass. Do NOT regress baselines (mypy: 3 pre-existing, ruff: 1 pre-existing).
- Frontend: `npm run type-check` (vue-tsc strict) and `npm run build` must pass. No test runner is installed — the type-check + build are the gates; manual dev-server check against the mock backend is the render verification.
- No new backend config keys. Reuse `metrics_provider: Literal["noop","bigquery"]`, `bigquery_project_id="lv-playground-genai"`, `bigquery_dataset="demo_proton"`, and the per-feature table-name settings already in `platform/config.py`.
- Tests must NOT touch live BigQuery. Backend adapter tests use a fake `bigquery.Client`; the default `METRICS_PROVIDER=noop` selects the mock everywhere.
- Read endpoint `GET /metrics/dashboard` is intentionally unauthenticated (channel-level aggregates only — POC decision per the spec).
- Commit format: `<type>(<scope>): <desc>`. Frequent commits.
- Co-located tests: `apps/backend/src/chatbot/features/metrics/test_*.py`.
- Frontend feature-slice convention: `api/`, `components/`, `store/`, `types/`, `index.ts` (see `src/features/chat/`). Path alias `@/* → ./src/*`. Reuse CSS tokens from `@/styles/tokens.css` (`--space-*`, `--surface`, `--border`, `--text`, `--text-muted`, `--danger`, `--radius-*`).

---

## File Structure

**Backend (new):**
- `apps/backend/src/chatbot/features/metrics/query_port.py` — result dataclasses + `MetricsQueryPort` Protocol + `MockMetricsQuery`.
- `apps/backend/src/chatbot/features/metrics/query_adapter.py` — `BigQueryMetricsQuery` + `build_metrics_query_port`.
- `apps/backend/src/chatbot/features/metrics/dashboard_router.py` — `GET /metrics/dashboard`.
- Tests: `test_query_port.py`, `test_query_adapter.py`, `test_dashboard_router.py`.
- Modify: `apps/backend/src/chatbot/main.py` (wire into `_wire_metrics_features`).

**Frontend (new):**
- `apps/frontend/src/router/index.ts` — routes `/` and `/dashboard`.
- `apps/frontend/src/features/dashboard/{types/index.ts, api/dashboard.api.ts, store/dashboard.store.ts, index.ts}`.
- `apps/frontend/src/features/dashboard/components/*.vue` — tiles + `MetricCard.vue` + `ChannelFilter.vue` + `BaseChart.vue`.
- `apps/frontend/src/views/DashboardView.vue`.
- Modify: `main.ts` (router + apexcharts), `App.vue` (`<router-view>`), `MainLayout.vue` + `HomeView.vue` (width), `AppHeader.vue` (nav links).

---

## Task 1: Backend — result dataclasses, port, and mock

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/query_port.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_query_port.py`

**Interfaces:**
- Produces: frozen dataclasses `VolumeRow, ResolutionRow, CsatRow, NpsRow, SpeedRow, FallbackRow, BounceRow, QualityRow`, the container `DashboardMetrics`, the `MetricsQueryPort` Protocol (`async def fetch_dashboard(self) -> DashboardMetrics`), and `MockMetricsQuery`.

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/src/chatbot/features/metrics/test_query_port.py
from __future__ import annotations

import pytest

from chatbot.features.metrics.query_port import (
    DashboardMetrics,
    MockMetricsQuery,
)


@pytest.mark.asyncio
async def test_mock_returns_populated_dashboard() -> None:
    metrics = await MockMetricsQuery().fetch_dashboard()
    assert isinstance(metrics, DashboardMetrics)
    # every block is present and non-empty so the UI renders something
    assert metrics.volume and metrics.resolution and metrics.csat
    assert metrics.nps and metrics.speed and metrics.fallback
    assert metrics.bounce and metrics.quality
    # spot-check shapes
    assert metrics.volume[0].month and metrics.volume[0].channel
    assert isinstance(metrics.volume[0].volume, int)
    assert any(s.is_first_turn for s in metrics.speed)
    assert metrics.nps[0].promoters >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_query_port.py -v`
Expected: FAIL — `ModuleNotFoundError: chatbot.features.metrics.query_port`.

- [ ] **Step 3: Write minimal implementation**

```python
# apps/backend/src/chatbot/features/metrics/query_port.py
"""Read-side for the in-app metrics dashboard: result shapes, port, and mock.

One dataclass per BigQuery view (columns match the view SELECT exactly).
SAFE_DIVIDE / AVG columns are Optional because BigQuery returns NULL when the
denominator is zero or no rows match."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VolumeRow:
    month: str
    channel: str
    volume: int


@dataclass(frozen=True)
class ResolutionRow:
    channel: str
    closed_by_bot: int
    transfer_to_agent: int
    total: int
    closed_by_bot_pct: float | None
    transfer_to_agent_pct: float | None


@dataclass(frozen=True)
class CsatRow:
    channel: str
    respondents: int
    avg_score: float | None
    satisfied_rate: float | None


@dataclass(frozen=True)
class NpsRow:
    channel: str
    respondents: int
    promoters: int
    passives: int
    detractors: int
    nps: float | None


@dataclass(frozen=True)
class SpeedRow:
    channel: str
    is_first_turn: bool
    p99_latency_ms: int | None
    avg_latency_ms: float | None
    turns: int


@dataclass(frozen=True)
class FallbackRow:
    channel: str
    fallback_rate: float | None
    turns: int


@dataclass(frozen=True)
class BounceRow:
    channel: str
    bounced: int
    total_sessions: int
    bounce_rate: float | None


@dataclass(frozen=True)
class QualityRow:
    channel: str
    labels: int
    avg_accuracy: float | None
    avg_quality: float | None


@dataclass(frozen=True)
class DashboardMetrics:
    volume: list[VolumeRow]
    resolution: list[ResolutionRow]
    csat: list[CsatRow]
    nps: list[NpsRow]
    speed: list[SpeedRow]
    fallback: list[FallbackRow]
    bounce: list[BounceRow]
    quality: list[QualityRow]


class MetricsQueryPort(Protocol):
    async def fetch_dashboard(self) -> DashboardMetrics: ...


class MockMetricsQuery:
    """Returns a representative payload so dev/tests never touch BigQuery."""

    async def fetch_dashboard(self) -> DashboardMetrics:
        return DashboardMetrics(
            volume=[
                VolumeRow(month="2026-05", channel="web", volume=120),
                VolumeRow(month="2026-05", channel="whatsapp", volume=80),
                VolumeRow(month="2026-06", channel="web", volume=140),
                VolumeRow(month="2026-06", channel="whatsapp", volume=95),
            ],
            resolution=[
                ResolutionRow("web", 90, 30, 120, 0.75, 0.25),
                ResolutionRow("whatsapp", 60, 20, 80, 0.75, 0.25),
            ],
            csat=[
                CsatRow("web", 40, 4.3, 0.85),
                CsatRow("whatsapp", 25, 4.1, 0.80),
            ],
            nps=[
                NpsRow("web", 35, 20, 10, 5, 42.86),
                NpsRow("whatsapp", 22, 11, 7, 4, 31.82),
            ],
            speed=[
                SpeedRow("web", True, 1800, 950.0, 130),
                SpeedRow("web", False, 1200, 700.0, 410),
                SpeedRow("whatsapp", True, 2100, 1100.0, 90),
                SpeedRow("whatsapp", False, 1500, 820.0, 260),
            ],
            fallback=[
                FallbackRow("web", 0.08, 540),
                FallbackRow("whatsapp", 0.12, 350),
            ],
            bounce=[
                BounceRow("web", 18, 120, 0.15),
                BounceRow("whatsapp", 16, 80, 0.20),
            ],
            quality=[
                QualityRow("web", 20, 88.5, 91.0),
                QualityRow("whatsapp", 15, 84.0, 87.5),
            ],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_query_port.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `cd apps/backend && .venv/bin/ruff format src/chatbot/features/metrics/query_port.py src/chatbot/features/metrics/test_query_port.py && .venv/bin/ruff check src/chatbot/features/metrics/ && .venv/bin/mypy src/ --strict`
Expected: ruff clean (no new issues); mypy at baseline 3 (no new errors).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/features/metrics/query_port.py apps/backend/src/chatbot/features/metrics/test_query_port.py
git commit -m "feat(metrics): dashboard read-side dataclasses + MetricsQueryPort + mock"
```

---

## Task 2: Backend — BigQuery read adapter + port factory

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/query_adapter.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_query_adapter.py`

**Interfaces:**
- Consumes: `DashboardMetrics` and all row dataclasses + `MockMetricsQuery` from Task 1; `Settings` from `chatbot.platform.config`.
- Produces: `BigQueryMetricsQuery` (constructor `(settings, *, client=None)`, async `fetch_dashboard`), and `build_metrics_query_port(settings) -> MetricsQueryPort`.

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/src/chatbot/features/metrics/test_query_adapter.py
from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.metrics.query_adapter import (
    BigQueryMetricsQuery,
    build_metrics_query_port,
)
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.platform.config import Settings


class _FakeJob:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeClient:
    """Returns canned rows based on the view name in the SQL."""

    def __init__(self, by_view: dict[str, list[dict[str, Any]]]) -> None:
        self._by_view = by_view
        self.queries: list[str] = []

    def query(self, sql: str) -> _FakeJob:
        self.queries.append(sql)
        for view, rows in self._by_view.items():
            if view in sql:
                return _FakeJob(rows)
        return _FakeJob([])


@pytest.mark.asyncio
async def test_adapter_maps_rows_into_dataclasses() -> None:
    client = _FakeClient(
        {
            "v_volume_by_month_channel": [
                {"month": "2026-06", "channel": "web", "volume": 140}
            ],
            "v_resolution_split": [
                {
                    "channel": "web",
                    "closed_by_bot": 90,
                    "transfer_to_agent": 30,
                    "total": 120,
                    "closed_by_bot_pct": 0.75,
                    "transfer_to_agent_pct": 0.25,
                }
            ],
            "v_csat": [
                {"channel": "web", "respondents": 40, "avg_score": 4.3, "satisfied_rate": 0.85}
            ],
            "v_nps": [
                {
                    "channel": "web",
                    "respondents": 35,
                    "promoters": 20,
                    "passives": 10,
                    "detractors": 5,
                    "nps": 42.86,
                }
            ],
            "v_speed_of_response": [
                {
                    "channel": "web",
                    "is_first_turn": True,
                    "p99_latency_ms": 1800,
                    "avg_latency_ms": 950.0,
                    "turns": 130,
                }
            ],
            "v_fallback_rate": [{"channel": "web", "fallback_rate": 0.08, "turns": 540}],
            "v_bounce_rate": [
                {"channel": "web", "bounced": 18, "total_sessions": 120, "bounce_rate": 0.15}
            ],
            "v_quality": [
                {"channel": "web", "labels": 20, "avg_accuracy": 88.5, "avg_quality": 91.0}
            ],
        }
    )
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds")
    adapter = BigQueryMetricsQuery(settings, client=client)

    metrics = await adapter.fetch_dashboard()

    assert metrics.volume[0].volume == 140
    assert metrics.resolution[0].closed_by_bot_pct == 0.75
    assert metrics.csat[0].avg_score == 4.3
    assert metrics.nps[0].promoters == 20
    assert metrics.speed[0].is_first_turn is True
    assert metrics.fallback[0].fallback_rate == 0.08
    assert metrics.bounce[0].bounce_rate == 0.15
    assert metrics.quality[0].avg_accuracy == 88.5
    # one query per view (8 total)
    assert len(client.queries) == 8


@pytest.mark.asyncio
async def test_adapter_handles_null_aggregates() -> None:
    client = _FakeClient(
        {"v_csat": [{"channel": "web", "respondents": 0, "avg_score": None, "satisfied_rate": None}]}
    )
    adapter = BigQueryMetricsQuery(Settings(), client=client)
    metrics = await adapter.fetch_dashboard()
    assert metrics.csat[0].avg_score is None
    assert metrics.volume == []  # absent view -> empty block, no crash


def test_build_factory_returns_mock_for_noop() -> None:
    port = build_metrics_query_port(Settings(metrics_provider="noop"))
    assert isinstance(port, MockMetricsQuery)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_query_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: chatbot.features.metrics.query_adapter`.

- [ ] **Step 3: Write minimal implementation**

```python
# apps/backend/src/chatbot/features/metrics/query_adapter.py
"""Read-side adapter: SELECTs the 8 Bot-Metrics views into DashboardMetrics."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.query_port import (
    BounceRow,
    CsatRow,
    DashboardMetrics,
    FallbackRow,
    MockMetricsQuery,
    NpsRow,
    QualityRow,
    ResolutionRow,
    SpeedRow,
    VolumeRow,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class BigQueryMetricsQuery:
    """Reads the 8 dashboard views. Each SELECT is wrapped in asyncio.to_thread
    so it never blocks the event loop. A missing/empty view yields an empty
    block rather than raising — the dashboard degrades gracefully."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._prefix = f"{settings.bigquery_project_id}.{settings.bigquery_dataset}"
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    def _rows(self, view: str) -> list[dict[str, Any]]:
        try:
            job = self._client.query(f"SELECT * FROM `{self._prefix}.{view}`")
            return [dict(r) for r in job.result()]
        except Exception as e:  # a single bad view must not blank the whole page
            _log.error("metrics_view_query_failed", view=view, error=str(e))
            return []

    def _fetch_sync(self) -> DashboardMetrics:
        return DashboardMetrics(
            volume=[VolumeRow(**r) for r in self._rows("v_volume_by_month_channel")],
            resolution=[ResolutionRow(**r) for r in self._rows("v_resolution_split")],
            csat=[CsatRow(**r) for r in self._rows("v_csat")],
            nps=[NpsRow(**r) for r in self._rows("v_nps")],
            speed=[SpeedRow(**r) for r in self._rows("v_speed_of_response")],
            fallback=[FallbackRow(**r) for r in self._rows("v_fallback_rate")],
            bounce=[BounceRow(**r) for r in self._rows("v_bounce_rate")],
            quality=[QualityRow(**r) for r in self._rows("v_quality")],
        )

    async def fetch_dashboard(self) -> DashboardMetrics:
        return await asyncio.to_thread(self._fetch_sync)


def build_metrics_query_port(settings: Settings) -> MetricsQueryPort:
    """Pick the read-side implementation from settings (reuses metrics_provider)."""
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryMetricsQuery(settings)
        except Exception as e:  # never let init crash the app
            _log.error("metrics_query_init_failed_falling_back_to_mock", error=str(e))
            return MockMetricsQuery()
    return MockMetricsQuery()
```

Note: `dict(r)` works for both `bigquery.Row` (supports mapping protocol) and the test's plain dicts. `**r` into the dataclass requires the SELECT column names to match field names exactly — they do (verified against the view DDLs). Extra/missing columns would raise `TypeError`; that is desirable (fail loud on schema drift), and is caught per-view by `_rows`'s caller only at the `query` stage, not the mapping — so a genuine schema mismatch surfaces in tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_query_adapter.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + type-check**

Run: `cd apps/backend && .venv/bin/ruff format src/chatbot/features/metrics/query_adapter.py src/chatbot/features/metrics/test_query_adapter.py && .venv/bin/ruff check src/chatbot/features/metrics/ && .venv/bin/mypy src/ --strict`
Expected: ruff clean; mypy baseline 3.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/features/metrics/query_adapter.py apps/backend/src/chatbot/features/metrics/test_query_adapter.py
git commit -m "feat(metrics): BigQueryMetricsQuery read adapter + build_metrics_query_port"
```

---

## Task 3: Backend — GET /metrics/dashboard router

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/dashboard_router.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_dashboard_router.py`

**Interfaces:**
- Consumes: `MetricsQueryPort` (Task 1), `MockMetricsQuery` (Task 1), `create_app` from `chatbot.platform.server`.
- Produces: `build_metrics_query_router(port: MetricsQueryPort) -> APIRouter` mounting `GET /metrics/dashboard`.

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/src/chatbot/features/metrics/test_dashboard_router.py
from __future__ import annotations

from fastapi.testclient import TestClient

from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


def _client() -> TestClient:
    app = create_app(Settings())
    app.include_router(build_metrics_query_router(MockMetricsQuery()))
    return TestClient(app)


def test_dashboard_returns_all_eight_blocks() -> None:
    res = _client().get("/metrics/dashboard")
    assert res.status_code == 200
    body = res.json()
    for block in (
        "volume",
        "resolution",
        "csat",
        "nps",
        "speed",
        "fallback",
        "bounce",
        "quality",
    ):
        assert block in body
        assert isinstance(body[block], list)
    assert body["volume"][0]["channel"]
    assert body["nps"][0]["promoters"] >= 0


def test_dashboard_requires_no_auth_header() -> None:
    # POC: read endpoint is open (no X-API-Key). 200 with no headers.
    assert _client().get("/metrics/dashboard").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_dashboard_router.py -v`
Expected: FAIL — `ModuleNotFoundError: ...dashboard_router`.

- [ ] **Step 3: Write minimal implementation**

```python
# apps/backend/src/chatbot/features/metrics/dashboard_router.py
"""GET /metrics/dashboard — read-only aggregated metrics for the in-app dashboard.

Unauthenticated by design (POC): the payload is channel-level aggregates only,
no PII or message content."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort


def build_metrics_query_router(port: MetricsQueryPort) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/dashboard")
    async def dashboard() -> dict[str, Any]:
        return asdict(await port.fetch_dashboard())

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_dashboard_router.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + type-check + full suite**

Run: `cd apps/backend && .venv/bin/ruff format src/chatbot/features/metrics/dashboard_router.py src/chatbot/features/metrics/test_dashboard_router.py && .venv/bin/ruff check src/chatbot/features/metrics/ && .venv/bin/mypy src/ --strict && .venv/bin/pytest src/`
Expected: ruff clean; mypy baseline 3; full suite green (prior 328 + the new tests).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/features/metrics/dashboard_router.py apps/backend/src/chatbot/features/metrics/test_dashboard_router.py
git commit -m "feat(metrics): GET /metrics/dashboard endpoint"
```

---

## Task 4: Backend — wire the router into bootstrap

**Files:**
- Modify: `apps/backend/src/chatbot/main.py` (imports + `_wire_metrics_features`)
- Test: `apps/backend/src/chatbot/features/metrics/test_dashboard_router.py` (add an integration assertion via the real app factory)

**Interfaces:**
- Consumes: `build_metrics_query_port` (Task 2), `build_metrics_query_router` (Task 3).

- [ ] **Step 1: Write the failing test**

Append to `test_dashboard_router.py`:

```python
def test_bootstrap_app_serves_dashboard() -> None:
    from chatbot.main import bootstrap_application

    client = TestClient(bootstrap_application())
    res = client.get("/metrics/dashboard")
    assert res.status_code == 200
    assert "volume" in res.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_dashboard_router.py::test_bootstrap_app_serves_dashboard -v`
Expected: FAIL — 404 (router not wired yet).

- [ ] **Step 3: Wire it in**

In `apps/backend/src/chatbot/main.py`, add imports near the existing metrics imports (after line 30, `from chatbot.features.metrics.qa_router import build_qa_router`):

```python
from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.query_adapter import build_metrics_query_port
```

Then extend `_wire_metrics_features` (currently lines 37-47) — add these two lines immediately after `app.include_router(build_qa_router(qa_port, settings))`:

```python
    query_port = build_metrics_query_port(settings)
    app.include_router(build_metrics_query_router(query_port))
```

The function body becomes:

```python
def _wire_metrics_features(app: FastAPI, settings: Settings) -> None:
    """Wire QA-labelling router, dashboard read API, and metrics-scheduler shutdown."""
    qa_port = build_qa_label_port(settings)
    app.include_router(build_qa_router(qa_port, settings))

    query_port = build_metrics_query_port(settings)
    app.include_router(build_metrics_query_router(query_port))

    metrics_scheduler = start_metrics_scheduler(settings)
    if metrics_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_metrics_scheduler() -> None:
            metrics_scheduler.shutdown(wait=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_dashboard_router.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + type-check + full suite**

Run: `cd apps/backend && .venv/bin/ruff format src/chatbot/main.py && .venv/bin/ruff check src/ && .venv/bin/mypy src/ --strict && .venv/bin/pytest src/`
Expected: ruff baseline 1 (no new); mypy baseline 3; full suite green.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/main.py apps/backend/src/chatbot/features/metrics/test_dashboard_router.py
git commit -m "feat(metrics): wire dashboard read API into bootstrap"
```

---

## Task 5: Frontend — deps, router, and layout width

**Files:**
- Modify: `apps/frontend/package.json` (deps)
- Create: `apps/frontend/src/router/index.ts`
- Modify: `apps/frontend/src/main.ts`, `apps/frontend/src/App.vue`, `apps/frontend/src/layouts/MainLayout.vue`, `apps/frontend/src/views/HomeView.vue`, `apps/frontend/src/components/layout/AppHeader.vue`

**Interfaces:**
- Produces: a working router with `/` (HomeView) and `/dashboard` (DashboardView placeholder), ApexCharts registered globally as `<apexchart>`, and header nav links.

- [ ] **Step 1: Install dependencies**

Run: `cd apps/frontend && npm install vue-router@4 vue3-apexcharts apexcharts`
Expected: three packages added to `dependencies` in `package.json`.

- [ ] **Step 2: Create a placeholder DashboardView so the route resolves**

```vue
<!-- apps/frontend/src/views/DashboardView.vue -->
<script setup lang="ts"></script>

<template>
  <section class="dashboard"><h2>Dashboard</h2></section>
</template>

<style scoped>
.dashboard { width: 100%; max-width: 1200px; margin: 0 auto; }
</style>
```

- [ ] **Step 3: Create the router**

```ts
// apps/frontend/src/router/index.ts
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import HomeView from '@/views/HomeView.vue';
import DashboardView from '@/views/DashboardView.vue';

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/dashboard', name: 'dashboard', component: DashboardView },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
```

- [ ] **Step 4: Register router + ApexCharts in main.ts**

Replace the contents of `apps/frontend/src/main.ts` with:

```ts
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import VueApexCharts from 'vue3-apexcharts';
import App from '@/App.vue';
import { router } from '@/router';
import '@/styles/tokens.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(VueApexCharts);
app.mount('#app');
```

- [ ] **Step 5: Render `<router-view>` in App.vue**

Replace `apps/frontend/src/App.vue` with:

```vue
<script setup lang="ts">
import MainLayout from '@/layouts/MainLayout.vue';
</script>

<template>
  <MainLayout>
    <router-view />
  </MainLayout>
</template>
```

- [ ] **Step 6: Move the 820px width cap off the layout onto HomeView**

The dashboard needs more width than the channels view. In `MainLayout.vue`, change the `main` rule — remove `max-width: 820px;` and the centering so the page controls its own width:

```css
main {
  flex: 1;
  width: 100%;
  padding: var(--space-lg);
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
```

Then in `HomeView.vue`, wrap the existing `<template>` content in a width-capped container. Change the template's outermost so its top node is:

```vue
<template>
  <div class="home-shell">
    <!-- ...existing HomeView template content unchanged... -->
  </div>
</template>
```

And add to HomeView's `<style scoped>`:

```css
.home-shell {
  width: 100%;
  max-width: 820px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
```

- [ ] **Step 7: Add header nav links**

In `AppHeader.vue`, add nav links after the `<h1>`. Change the `<template>` header block to:

```vue
  <header class="app-header">
    <div class="brand">
      <h1>Proton Conversational AI</h1>
      <nav class="nav">
        <router-link to="/">Channels</router-link>
        <router-link to="/dashboard">Dashboard</router-link>
      </nav>
    </div>
    <div v-if="health" class="badges">
      <span class="badge">CRM: <strong>{{ health.crm_provider }}</strong></span>
      <span class="badge">Voice: <strong>{{ health.voice_provider }}</strong></span>
      <span class="badge">Model: <strong>{{ health.model }}</strong></span>
    </div>
    <div v-else-if="error" class="badges">
      <span class="badge err">{{ error }}</span>
    </div>
    <div v-else class="badges">
      <span class="badge">Connecting…</span>
    </div>
  </header>
```

Add to AppHeader's `<style scoped>`:

```css
.brand { display: flex; align-items: center; gap: var(--space-md); }
.nav { display: flex; gap: var(--space-sm); }
.nav a {
  font-size: 0.85rem;
  color: var(--text-muted);
  text-decoration: none;
  padding: 0.25rem 0.6rem;
  border-radius: var(--radius-full);
}
.nav a.router-link-active { color: var(--text); background: var(--surface); }
```

- [ ] **Step 8: Type-check + build**

Run: `cd apps/frontend && npm run type-check && npm run build`
Expected: both pass. (If vue-tsc flags `vue3-apexcharts` missing types, add `// @ts-expect-error no bundled types` above its import in `main.ts` — the package ships its own types as of v1.5+, so this is usually unnecessary.)

- [ ] **Step 9: Commit**

```bash
git add apps/frontend/package.json apps/frontend/package-lock.json apps/frontend/src/main.ts apps/frontend/src/App.vue apps/frontend/src/router apps/frontend/src/views/DashboardView.vue apps/frontend/src/layouts/MainLayout.vue apps/frontend/src/views/HomeView.vue apps/frontend/src/components/layout/AppHeader.vue
git commit -m "feat(frontend): add vue-router, ApexCharts, and /dashboard route shell"
```

---

## Task 6: Frontend — dashboard types, api, and store

**Files:**
- Create: `apps/frontend/src/features/dashboard/types/index.ts`
- Create: `apps/frontend/src/features/dashboard/api/dashboard.api.ts`
- Create: `apps/frontend/src/features/dashboard/store/dashboard.store.ts`
- Create: `apps/frontend/src/features/dashboard/index.ts`

**Interfaces:**
- Consumes: `API_BASE_URL` from `@/plugins/api`.
- Produces: `DashboardMetrics` TS type (mirrors backend), `fetchDashboard()`, `useDashboardStore()` exposing `metrics`, `loading`, `error`, `selectedChannel`, `channels` getter, `filtered` getters, and `load()`.

- [ ] **Step 1: Create the types (mirror backend dataclasses)**

```ts
// apps/frontend/src/features/dashboard/types/index.ts
export interface VolumeRow { month: string; channel: string; volume: number; }
export interface ResolutionRow {
  channel: string;
  closed_by_bot: number;
  transfer_to_agent: number;
  total: number;
  closed_by_bot_pct: number | null;
  transfer_to_agent_pct: number | null;
}
export interface CsatRow {
  channel: string;
  respondents: number;
  avg_score: number | null;
  satisfied_rate: number | null;
}
export interface NpsRow {
  channel: string;
  respondents: number;
  promoters: number;
  passives: number;
  detractors: number;
  nps: number | null;
}
export interface SpeedRow {
  channel: string;
  is_first_turn: boolean;
  p99_latency_ms: number | null;
  avg_latency_ms: number | null;
  turns: number;
}
export interface FallbackRow { channel: string; fallback_rate: number | null; turns: number; }
export interface BounceRow {
  channel: string;
  bounced: number;
  total_sessions: number;
  bounce_rate: number | null;
}
export interface QualityRow {
  channel: string;
  labels: number;
  avg_accuracy: number | null;
  avg_quality: number | null;
}
export interface DashboardMetrics {
  volume: VolumeRow[];
  resolution: ResolutionRow[];
  csat: CsatRow[];
  nps: NpsRow[];
  speed: SpeedRow[];
  fallback: FallbackRow[];
  bounce: BounceRow[];
  quality: QualityRow[];
}
```

- [ ] **Step 2: Create the api wrapper**

```ts
// apps/frontend/src/features/dashboard/api/dashboard.api.ts
import { API_BASE_URL } from '@/plugins/api';
import type { DashboardMetrics } from '@/features/dashboard/types';

export async function fetchDashboard(): Promise<DashboardMetrics> {
  const res = await fetch(`${API_BASE_URL}/metrics/dashboard`);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`metrics/dashboard ${res.status}: ${body}`);
  }
  return (await res.json()) as DashboardMetrics;
}
```

- [ ] **Step 3: Create the store**

```ts
// apps/frontend/src/features/dashboard/store/dashboard.store.ts
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { fetchDashboard } from '@/features/dashboard/api/dashboard.api';
import type { DashboardMetrics } from '@/features/dashboard/types';

const EMPTY: DashboardMetrics = {
  volume: [], resolution: [], csat: [], nps: [],
  speed: [], fallback: [], bounce: [], quality: [],
};

export const useDashboardStore = defineStore('dashboard', () => {
  const metrics = ref<DashboardMetrics>(EMPTY);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const selectedChannel = ref<string>('all');

  // Union of channels across blocks, for the filter dropdown.
  const channels = computed<string[]>(() => {
    const set = new Set<string>();
    metrics.value.resolution.forEach((r) => set.add(r.channel));
    metrics.value.volume.forEach((r) => set.add(r.channel));
    return ['all', ...Array.from(set).sort()];
  });

  function pick<T extends { channel: string }>(rows: T[]): T[] {
    return selectedChannel.value === 'all'
      ? rows
      : rows.filter((r) => r.channel === selectedChannel.value);
  }

  const filtered = computed<DashboardMetrics>(() => ({
    volume: pick(metrics.value.volume),
    resolution: pick(metrics.value.resolution),
    csat: pick(metrics.value.csat),
    nps: pick(metrics.value.nps),
    speed: pick(metrics.value.speed),
    fallback: pick(metrics.value.fallback),
    bounce: pick(metrics.value.bounce),
    quality: pick(metrics.value.quality),
  }));

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      metrics.value = await fetchDashboard();
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load metrics';
    } finally {
      loading.value = false;
    }
  }

  return { metrics, loading, error, selectedChannel, channels, filtered, load };
});
```

- [ ] **Step 4: Create the barrel export**

```ts
// apps/frontend/src/features/dashboard/index.ts
export { useDashboardStore } from './store/dashboard.store';
export type { DashboardMetrics } from './types';
```

- [ ] **Step 5: Type-check**

Run: `cd apps/frontend && npm run type-check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/src/features/dashboard
git commit -m "feat(frontend): dashboard types, api client, and Pinia store"
```

---

## Task 7: Frontend — chart tiles + KPI cards + channel filter

**Files:**
- Create: `apps/frontend/src/features/dashboard/components/BaseChart.vue`
- Create: `apps/frontend/src/features/dashboard/components/MetricCard.vue`
- Create: `apps/frontend/src/features/dashboard/components/ChannelFilter.vue`
- Create: `apps/frontend/src/features/dashboard/components/{VolumeChart,ResolutionChart,CsatGauge,NpsTile,SpeedChart,RateChart,QualityChart}.vue`

**Interfaces:**
- Consumes: row types from Task 6.
- Produces: tile components, each taking its block as a prop and rendering an ApexCharts series.

- [ ] **Step 1: BaseChart wrapper (consistent card chrome)**

```vue
<!-- apps/frontend/src/features/dashboard/components/BaseChart.vue -->
<script setup lang="ts">
defineProps<{ title: string; empty?: boolean }>();
</script>

<template>
  <div class="card">
    <h3>{{ title }}</h3>
    <p v-if="empty" class="empty">No data yet.</p>
    <slot v-else />
  </div>
</template>

<style scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 12px);
  padding: var(--space-md);
}
.card h3 { margin: 0 0 var(--space-sm); font-size: 0.9rem; font-weight: 600; }
.empty { color: var(--text-muted); font-size: 0.85rem; margin: 0; }
</style>
```

- [ ] **Step 2: MetricCard (KPI numbers)**

```vue
<!-- apps/frontend/src/features/dashboard/components/MetricCard.vue -->
<script setup lang="ts">
defineProps<{ label: string; value: string }>();
</script>

<template>
  <div class="kpi">
    <span class="value">{{ value }}</span>
    <span class="label">{{ label }}</span>
  </div>
</template>

<style scoped>
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 12px);
  padding: var(--space-md);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.value { font-size: 1.6rem; font-weight: 700; color: var(--text); }
.label { font-size: 0.8rem; color: var(--text-muted); }
</style>
```

- [ ] **Step 3: ChannelFilter**

```vue
<!-- apps/frontend/src/features/dashboard/components/ChannelFilter.vue -->
<script setup lang="ts">
defineProps<{ modelValue: string; channels: string[] }>();
defineEmits<{ 'update:modelValue': [value: string] }>();
</script>

<template>
  <select
    class="filter"
    :value="modelValue"
    @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <option v-for="c in channels" :key="c" :value="c">
      {{ c === 'all' ? 'All channels' : c }}
    </option>
  </select>
</template>

<style scoped>
.filter {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--radius-full);
  padding: 0.3rem 0.75rem;
  font-size: 0.85rem;
}
</style>
```

- [ ] **Step 4: VolumeChart (stacked bar by month × channel)**

```vue
<!-- apps/frontend/src/features/dashboard/components/VolumeChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { VolumeRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: VolumeRow[] }>();

const months = computed(() => [...new Set(props.rows.map((r) => r.month))].sort());
const channels = computed(() => [...new Set(props.rows.map((r) => r.channel))].sort());

const series = computed(() =>
  channels.value.map((ch) => ({
    name: ch,
    data: months.value.map(
      (m) => props.rows.find((r) => r.month === m && r.channel === ch)?.volume ?? 0,
    ),
  })),
);

const options = computed(() => ({
  chart: { type: 'bar', stacked: true, toolbar: { show: false } },
  xaxis: { categories: months.value },
  legend: { position: 'top' as const },
  dataLabels: { enabled: false },
}));
</script>

<template>
  <BaseChart title="Monthly Volume by Channel" :empty="rows.length === 0">
    <apexchart type="bar" height="300" :options="options" :series="series" />
  </BaseChart>
</template>
```

- [ ] **Step 5: ResolutionChart (donut: bot vs agent, summed across rows)**

```vue
<!-- apps/frontend/src/features/dashboard/components/ResolutionChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { ResolutionRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: ResolutionRow[] }>();

const bot = computed(() => props.rows.reduce((s, r) => s + r.closed_by_bot, 0));
const agent = computed(() => props.rows.reduce((s, r) => s + r.transfer_to_agent, 0));
const series = computed(() => [bot.value, agent.value]);
const options = computed(() => ({
  chart: { type: 'donut' },
  labels: ['Closed by bot', 'Transferred to agent'],
  legend: { position: 'bottom' as const },
}));
</script>

<template>
  <BaseChart title="Resolution Split" :empty="bot + agent === 0">
    <apexchart type="donut" height="280" :options="options" :series="series" />
  </BaseChart>
</template>
```

- [ ] **Step 6: CsatGauge (radial; avg of avg_score scaled to 0-100)**

```vue
<!-- apps/frontend/src/features/dashboard/components/CsatGauge.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { CsatRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: CsatRow[] }>();

const scored = computed(() => props.rows.filter((r) => r.avg_score !== null));
// CSAT is 1-5; show as a percentage of 5 for the radial gauge.
const avg = computed(() => {
  if (scored.value.length === 0) return 0;
  const mean =
    scored.value.reduce((s, r) => s + (r.avg_score ?? 0), 0) / scored.value.length;
  return Math.round((mean / 5) * 100);
});
const series = computed(() => [avg.value]);
const options = computed(() => ({
  chart: { type: 'radialBar' },
  plotOptions: {
    radialBar: { dataLabels: { value: { formatter: () => `${avg.value}%` } } },
  },
  labels: ['CSAT'],
}));
</script>

<template>
  <BaseChart title="CSAT" :empty="scored.length === 0">
    <apexchart type="radialBar" height="260" :options="options" :series="series" />
  </BaseChart>
</template>
```

- [ ] **Step 7: NpsTile (summed promoter/passive/detractor + NPS number)**

```vue
<!-- apps/frontend/src/features/dashboard/components/NpsTile.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { NpsRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: NpsRow[] }>();

const promoters = computed(() => props.rows.reduce((s, r) => s + r.promoters, 0));
const passives = computed(() => props.rows.reduce((s, r) => s + r.passives, 0));
const detractors = computed(() => props.rows.reduce((s, r) => s + r.detractors, 0));
const respondents = computed(() => promoters.value + passives.value + detractors.value);
const nps = computed(() =>
  respondents.value === 0
    ? null
    : Math.round(((promoters.value - detractors.value) / respondents.value) * 100),
);
const series = computed(() => [promoters.value, passives.value, detractors.value]);
const options = computed(() => ({
  chart: { type: 'donut' },
  labels: ['Promoters', 'Passives', 'Detractors'],
  colors: ['#16a34a', '#eab308', '#dc2626'],
  legend: { position: 'bottom' as const },
}));
</script>

<template>
  <BaseChart title="NPS" :empty="respondents === 0">
    <p class="score">{{ nps }}</p>
    <apexchart type="donut" height="240" :options="options" :series="series" />
  </BaseChart>
</template>

<style scoped>
.score { font-size: 1.8rem; font-weight: 700; margin: 0 0 var(--space-sm); }
</style>
```

- [ ] **Step 8: SpeedChart (p99 latency bar, split by first vs follow-up turn)**

```vue
<!-- apps/frontend/src/features/dashboard/components/SpeedChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { SpeedRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: SpeedRow[] }>();

const channels = computed(() => [...new Set(props.rows.map((r) => r.channel))].sort());
const pick = (ch: string, first: boolean) =>
  props.rows.find((r) => r.channel === ch && r.is_first_turn === first)?.p99_latency_ms ?? 0;
const series = computed(() => [
  { name: 'First turn', data: channels.value.map((c) => pick(c, true)) },
  { name: 'Follow-up', data: channels.value.map((c) => pick(c, false)) },
]);
const options = computed(() => ({
  chart: { type: 'bar', toolbar: { show: false } },
  xaxis: { categories: channels.value },
  yaxis: { title: { text: 'p99 latency (ms)' } },
  legend: { position: 'top' as const },
  dataLabels: { enabled: false },
}));
</script>

<template>
  <BaseChart title="Speed of Response (p99)" :empty="rows.length === 0">
    <apexchart type="bar" height="260" :options="options" :series="series" />
  </BaseChart>
</template>
```

- [ ] **Step 9: RateChart (reusable for fallback + bounce; percentage bar by channel)**

```vue
<!-- apps/frontend/src/features/dashboard/components/RateChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';

const props = defineProps<{
  title: string;
  rows: { channel: string }[];
  rate: (row: { channel: string }) => number | null;
}>();

const series = computed(() => [
  {
    name: props.title,
    data: props.rows.map((r) => Math.round((props.rate(r) ?? 0) * 100)),
  },
]);
const options = computed(() => ({
  chart: { type: 'bar', toolbar: { show: false } },
  xaxis: { categories: props.rows.map((r) => r.channel) },
  yaxis: { max: 100, title: { text: '%' } },
  dataLabels: { enabled: true, formatter: (v: number) => `${v}%` },
}));
</script>

<template>
  <BaseChart :title="title" :empty="rows.length === 0">
    <apexchart type="bar" height="240" :options="options" :series="series" />
  </BaseChart>
</template>
```

- [ ] **Step 10: QualityChart (grouped bar: accuracy + quality by channel)**

```vue
<!-- apps/frontend/src/features/dashboard/components/QualityChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { QualityRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: QualityRow[] }>();

const options = computed(() => ({
  chart: { type: 'bar', toolbar: { show: false } },
  xaxis: { categories: props.rows.map((r) => r.channel) },
  yaxis: { max: 100 },
  legend: { position: 'top' as const },
  dataLabels: { enabled: false },
}));
const series = computed(() => [
  { name: 'Accuracy', data: props.rows.map((r) => Math.round(r.avg_accuracy ?? 0)) },
  { name: 'Quality', data: props.rows.map((r) => Math.round(r.avg_quality ?? 0)) },
]);
</script>

<template>
  <BaseChart title="Accuracy & Quality by Channel" :empty="rows.length === 0">
    <apexchart type="bar" height="260" :options="options" :series="series" />
  </BaseChart>
</template>
```

- [ ] **Step 11: Type-check + build**

Run: `cd apps/frontend && npm run type-check && npm run build`
Expected: both pass.

- [ ] **Step 12: Commit**

```bash
git add apps/frontend/src/features/dashboard/components
git commit -m "feat(frontend): dashboard chart tiles, KPI card, and channel filter"
```

---

## Task 8: Frontend — assemble DashboardView + verify end-to-end

**Files:**
- Modify: `apps/frontend/src/views/DashboardView.vue` (replace placeholder)

**Interfaces:**
- Consumes: `useDashboardStore` (Task 6), all tile components (Task 7).

- [ ] **Step 1: Replace DashboardView with the full layout**

```vue
<!-- apps/frontend/src/views/DashboardView.vue -->
<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useDashboardStore } from '@/features/dashboard';
import ChannelFilter from '@/features/dashboard/components/ChannelFilter.vue';
import MetricCard from '@/features/dashboard/components/MetricCard.vue';
import VolumeChart from '@/features/dashboard/components/VolumeChart.vue';
import ResolutionChart from '@/features/dashboard/components/ResolutionChart.vue';
import CsatGauge from '@/features/dashboard/components/CsatGauge.vue';
import NpsTile from '@/features/dashboard/components/NpsTile.vue';
import SpeedChart from '@/features/dashboard/components/SpeedChart.vue';
import RateChart from '@/features/dashboard/components/RateChart.vue';
import QualityChart from '@/features/dashboard/components/QualityChart.vue';

const store = useDashboardStore();
onMounted(() => {
  if (store.metrics.volume.length === 0) store.load();
});

const m = computed(() => store.filtered);

const totalConvos = computed(() => m.value.resolution.reduce((s, r) => s + r.total, 0));
const botPct = computed(() => {
  const bot = m.value.resolution.reduce((s, r) => s + r.closed_by_bot, 0);
  return totalConvos.value === 0 ? '—' : `${Math.round((bot / totalConvos.value) * 100)}%`;
});
const csatAvg = computed(() => {
  const scored = m.value.csat.filter((r) => r.avg_score !== null);
  if (scored.length === 0) return '—';
  const mean = scored.reduce((s, r) => s + (r.avg_score ?? 0), 0) / scored.length;
  return mean.toFixed(1);
});
const npsScore = computed(() => {
  const p = m.value.nps.reduce((s, r) => s + r.promoters, 0);
  const d = m.value.nps.reduce((s, r) => s + r.detractors, 0);
  const resp = p + m.value.nps.reduce((s, r) => s + r.passives, 0) + d;
  return resp === 0 ? '—' : String(Math.round(((p - d) / resp) * 100));
});

// fallback/bounce share { channel } shape; expose rate accessors for RateChart.
const fallbackRate = (row: { channel: string }) =>
  m.value.fallback.find((f) => f.channel === row.channel)?.fallback_rate ?? null;
const bounceRate = (row: { channel: string }) =>
  m.value.bounce.find((b) => b.channel === row.channel)?.bounce_rate ?? null;
</script>

<template>
  <section class="dashboard">
    <header class="bar">
      <h2>Bot Metrics</h2>
      <div class="actions">
        <ChannelFilter v-model="store.selectedChannel" :channels="store.channels" />
        <button :disabled="store.loading" @click="store.load()">
          {{ store.loading ? 'Loading…' : 'Refresh' }}
        </button>
      </div>
    </header>

    <p v-if="store.error" class="error">{{ store.error }}</p>

    <div class="kpis">
      <MetricCard label="Total conversations" :value="String(totalConvos)" />
      <MetricCard label="Bot-resolved" :value="botPct" />
      <MetricCard label="CSAT (avg /5)" :value="csatAvg" />
      <MetricCard label="NPS" :value="npsScore" />
    </div>

    <VolumeChart :rows="m.volume" />

    <div class="grid-2">
      <ResolutionChart :rows="m.resolution" />
      <div class="stack">
        <CsatGauge :rows="m.csat" />
        <NpsTile :rows="m.nps" />
      </div>
    </div>

    <div class="grid-3">
      <SpeedChart :rows="m.speed" />
      <RateChart title="Fallback Rate" :rows="m.fallback" :rate="fallbackRate" />
      <RateChart title="Bounce Rate" :rows="m.bounce" :rate="bounceRate" />
    </div>

    <QualityChart :rows="m.quality" />
  </section>
</template>

<style scoped>
.dashboard {
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
.bar { display: flex; justify-content: space-between; align-items: center; }
.bar h2 { margin: 0; font-size: 1.1rem; }
.actions { display: flex; gap: var(--space-sm); align-items: center; }
.actions button {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--radius-full);
  padding: 0.3rem 0.9rem;
  cursor: pointer;
}
.error { color: var(--danger); margin: 0; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-md); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-md); }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-md); }
.stack { display: flex; flex-direction: column; gap: var(--space-md); }
@media (max-width: 860px) {
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
}
</style>
```

- [ ] **Step 2: Type-check + build**

Run: `cd apps/frontend && npm run type-check && npm run build`
Expected: both pass.

- [ ] **Step 3: Manual end-to-end verification (mock data path)**

Run the backend with the default `METRICS_PROVIDER=noop` (serves `MockMetricsQuery`):
```bash
cd apps/backend && .venv/bin/uvicorn chatbot.main:app --reload
```
In a second terminal:
```bash
cd apps/frontend && npm run dev
```
Open `http://localhost:5173/dashboard`. Expected: header shows Channels/Dashboard links; 4 KPI cards populate; volume stacked bar, resolution donut, CSAT gauge, NPS donut, speed bar, two rate bars, and quality bar all render with the mock numbers; the channel filter narrows every tile; Refresh re-fetches. Confirm `curl -s localhost:8000/metrics/dashboard | head` returns JSON with all 8 blocks.

- [ ] **Step 4: Commit**

```bash
git add apps/frontend/src/views/DashboardView.vue
git commit -m "feat(frontend): assemble metrics DashboardView with all tiles + filter"
```

---

## Task 9: Docs + changelog

**Files:**
- Modify: `CHANGELOG.md` (if present at repo root or `apps/*`)
- Create: `docs/dashboards/in-app-vue-dashboard.md` (short usage note)

- [ ] **Step 1: Write the usage note**

Create `docs/dashboards/in-app-vue-dashboard.md` documenting: the `/dashboard` route, the `GET /metrics/dashboard` endpoint, that it reads the 8 BQ views via `METRICS_PROVIDER=bigquery` (and serves `MockMetricsQuery` when `noop`), the open-endpoint POC caveat, and the deferred items (date picker, auth gate, drill-down).

- [ ] **Step 2: Update CHANGELOG**

Add an entry under the appropriate heading: `feat(metrics): in-app Vue metrics dashboard (/dashboard) reading the 8 BigQuery views via GET /metrics/dashboard`.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/dashboards/in-app-vue-dashboard.md
git commit -m "docs(dashboard): document in-app Vue metrics dashboard"
```

---

## Self-Review Notes

- **Spec coverage:** data flow (Tasks 1-4, 6), `MetricsQueryPort` + Bq adapter + mock (Tasks 1-2), single `GET /metrics/dashboard` (Task 3), wiring via `_wire_metrics_features` (Task 4), all 8 result dataclasses with exact view columns (Task 1), open auth (Task 3 test), vue-router + AppHeader link (Task 5), feature slice api/store/types (Task 6), 8 tiles + KPI cards + client-side channel filter (Tasks 7-8), error/empty/NULL handling (store + BaseChart `empty` + `—`), testing approach (backend pytest with fake client; frontend type-check/build/manual), rollout (Task 8 step 3), docs (Task 9). All spec sections map to a task.
- **No new config:** reuses `metrics_provider` + `bigquery_*` (Task 2 factory) — matches the spec constraint.
- **Type consistency:** backend dataclass field names == view SELECT aliases == TS interface fields == store/component property accesses (`closed_by_bot`, `avg_score`, `is_first_turn`, `fallback_rate`, `bounce_rate`, `avg_accuracy`, `avg_quality`, `nps`, `promoters/passives/detractors`). `fetch_dashboard` / `build_metrics_query_port` / `build_metrics_query_router` names consistent across Tasks 2-4. `useDashboardStore` exposes `filtered`, `selectedChannel`, `channels`, `load` — all consumed in Task 8.
```
