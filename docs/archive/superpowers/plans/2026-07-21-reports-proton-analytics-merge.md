# Proton Reporting → Chatwoot Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the standalone Proton analytics dashboard as native Chatwoot report tabs inside the existing Reports menu, fed by the vendored proton backend `/metrics/*`.

**Architecture:** Two data tiers. Tier 1 (Analytics, Anomaly) reuses existing backend endpoints — frontend-only. Tier 2 (Departments & PIC, Call-Centre KPI, Case Lifecycle) adds read endpoints that expose already-existing Phase-3 BigQuery views over HTTP, then builds the tabs. Frontend is delivered as Chatwoot fork patches (native Vue + chart.js), backend lives in `backend/apps/backend`.

**Tech Stack:** Backend — Python 3.12, FastAPI, pytest, google-cloud-bigquery (all in the vendored `backend/`). Frontend — Vue 3 `<script setup>`, chart.js 4 + vue-chartjs 5 (already Chatwoot deps), Tailwind `n-*` design tokens, delivered as `.patch` files applied at Docker build.

## Global Constraints

- **Backend dir:** all backend code in `backend/apps/backend/src/chatbot/` in THIS repo (the vendored proton backend). Never edit the standalone `proton-conversational-ai` repo.
- **Backend commands** (run from `backend/apps/backend/`): `.venv/bin/ruff format .` · `.venv/bin/ruff check . --fix` · `.venv/bin/pytest src/` . Pytest needs `GOOGLE_API_KEY` set at collection (upstream quirk) — export a dummy if unset: `GOOGLE_API_KEY=x .venv/bin/pytest ...`.
- **Port pattern (verbatim):** one `@dataclass(frozen=True)` per BigQuery view, fields named EXACTLY as the view's `SELECT` columns. `SAFE_DIVIDE`/`AVG` columns are `float | None`. See `features/metrics/query_port.py`.
- **Adapter pattern (verbatim):** `self._block("v_view_name", RowType)` runs `SELECT * FROM prefix.v_view_name` and maps rows via `RowType(**dict(r))`; a failing view degrades to `[]`, never 500s.
- **Auth pattern (verbatim):** Tier-2 read endpoints gate on `x-api-key == settings.metrics_api_key` using `hmac.compare_digest`, returning 401 when the key is unset or mismatched — copy the check from `features/metrics/qa_router.py`.
- **Frontend delivery:** edit the working Chatwoot clone at `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot`, then export a numbered patch into `deploy/chatwoot-fork/patches/` (next free number after `0019`). The Dockerfile globs `patches/*.patch`. Frontend "tests" = `pnpm exec vite build` succeeds (0 errors) + live smoke against a running stack (the fork's established method; there is no unit-test harness for these patches).
- **Design tokens:** use `n-*` Tailwind tokens and reuse `ReportMetricCard.vue` + `shared/components/charts/BarChart.vue`; do not introduce a new chart library.
- **Nav gating:** report routes require `meta.permissions: ['administrator', 'report_manage']` and sit under `FEATURE_FLAGS.REPORTS`.
- **Commits:** conventional format; end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Work on `dev-yuda`; never merge to main.

---

## Endpoint → view map (reference for all backend tasks)

| Endpoint | Views (exact names) | Row dataclasses |
|---|---|---|
| `GET /metrics/departments` | `v_dept_pic_performance`, `v_reopen_rate` | `DeptPicRow`, `ReopenRow` |
| `GET /metrics/callcenter` | `v_sla_achievement`, `v_tasks_per_agent`, `v_first_response_by_channel`, `v_resolution_time`, `v_complaint_type_ranking`, `v_peak_hours`, `v_nps_by_agent` | `SlaAchievementRow`, `TasksPerAgentRow`, `FirstResponseRow`, `ResolutionTimeRow`, `ComplaintTypeRow`, `PeakHourRow`, `NpsByAgentRow` |
| `GET /metrics/lifecycle` | `v_case_lifecycle`, `v_state_trend` | `CaseLifecycleRow`, `StateTrendRow` |

Column definitions are authoritative in `features/metrics/bigquery_schema.py::view_ddls()`. Each dataclass below mirrors those columns exactly.

---

# PHASE A — Tier 1 (frontend-only, existing endpoints)

Deliverable: Analytics + Anomaly tabs live in Chatwoot Reports, plus a PDF/Excel export button, all reading endpoints that already exist (`/metrics/dashboard`, `/metrics/anomalies`, `/metrics/export`).

### Task A1: Frontend scaffolding — API client + config wiring

**Files:**
- Read first (learn the pattern): `deploy/chatwoot-fork/patches/0001-runtime-config.patch` (how `useProtonConfig` / runtime backend URL is exposed), `deploy/chatwoot-fork/patches/0010-knowledge-faqs-native.patch` (the `api/protonKnowledge.js` client + `useProtonConfig` usage).
- Create (in the clone): `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot/app/javascript/dashboard/api/protonMetrics.js`

**Interfaces:**
- Consumes: `useProtonConfig()` → `{ backendBaseUrl, apiKey }` (exact shape per patch 0001/0010).
- Produces: `ProtonMetricsAPI` with `getDashboard()`, `getAnomalies()`, `exportUrl(format)`, and (Phase B) `getDepartments()`, `getCallCentre()`, `getLifecycle()`. All return the parsed JSON body.

- [ ] **Step 1: Read the two template patches** to confirm the exact `useProtonConfig` import path and how `protonKnowledge.js` builds requests (base URL join, headers, `Api-Access-Token`/`x-api-key` handling — note the Caddy underscore-header gotcha: send dash-form headers).

- [ ] **Step 2: Create `protonMetrics.js`** modeled on `protonKnowledge.js`:

```js
// app/javascript/dashboard/api/protonMetrics.js
import { useProtonConfig } from 'dashboard/composables/useProtonConfig'; // confirm exact path from patch 0010

const join = (base, path) => `${base.replace(/\/$/, '')}${path}`;

export const useProtonMetricsAPI = () => {
  const { backendBaseUrl, apiKey } = useProtonConfig();
  const headers = apiKey ? { 'x-api-key': apiKey, 'X-Api-Key': apiKey } : {};

  const getJson = async path => {
    const res = await fetch(join(backendBaseUrl, path), { headers });
    if (!res.ok) throw new Error(`metrics ${path} -> ${res.status}`);
    return res.json();
  };

  return {
    getDashboard: () => getJson('/metrics/dashboard'),
    getAnomalies: () => getJson('/metrics/anomalies'),
    exportUrl: format => join(backendBaseUrl, `/metrics/export?format=${format}`),
    // Phase B:
    getDepartments: () => getJson('/metrics/departments'),
    getCallCentre: () => getJson('/metrics/callcenter'),
    getLifecycle: () => getJson('/metrics/lifecycle'),
  };
};
```

- [ ] **Step 3: Verify build** — from the clone root: `pnpm exec vite build 2>&1 | tail -20`. Expected: build completes, 0 errors. (No component consumes it yet; this just confirms the import path resolves.)

- [ ] **Step 4: Commit (in the clone)** `git add app/javascript/dashboard/api/protonMetrics.js && git commit -m "feat(reports): proton metrics API client"`. The patch is exported in Task A6.

### Task A2: Chart wrapper components (Line + Doughnut)

**Files:**
- Read first: `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot/app/javascript/shared/components/charts/BarChart.vue` (props `collection` + `chartOptions`, registers chart.js elements, wraps `<Bar>` from vue-chartjs).
- Create: `shared/components/charts/LineChart.vue`, `shared/components/charts/DoughnutChart.vue`

**Interfaces:**
- Produces: `<LineChart :collection :chart-options />` and `<DoughnutChart :collection :chart-options />`, identical prop contract to `BarChart.vue` (a chart.js `data` object + `options`).

- [ ] **Step 1: Create `LineChart.vue`** mirroring `BarChart.vue` but for lines:

```vue
<script setup>
import { Line } from 'vue-chartjs';
import {
  Chart as ChartJS, Title, Tooltip, Legend,
  LineElement, PointElement, CategoryScale, LinearScale,
} from 'chart.js';
defineProps({
  collection: { type: Object, default: () => ({}) },
  chartOptions: { type: Object, default: () => ({}) },
});
ChartJS.register(Title, Tooltip, Legend, LineElement, PointElement, CategoryScale, LinearScale);
</script>
<template>
  <Line :data="collection" :options="chartOptions" />
</template>
```

- [ ] **Step 2: Create `DoughnutChart.vue`**:

```vue
<script setup>
import { Doughnut } from 'vue-chartjs';
import { Chart as ChartJS, Title, Tooltip, Legend, ArcElement } from 'chart.js';
defineProps({
  collection: { type: Object, default: () => ({}) },
  chartOptions: { type: Object, default: () => ({}) },
});
ChartJS.register(Title, Tooltip, Legend, ArcElement);
</script>
<template>
  <Doughnut :data="collection" :options="chartOptions" />
</template>
```

- [ ] **Step 3: Verify build** — `pnpm exec vite build 2>&1 | tail -20`. Expected: 0 errors.

- [ ] **Step 4: Commit** `git add app/javascript/shared/components/charts/{LineChart,DoughnutChart}.vue && git commit -m "feat(reports): line + doughnut chart wrappers"`.

### Task A3: Analytics tab (`ProtonAnalytics.vue`) + route + sidebar

**Files:**
- Read first: `dashboard/routes/dashboard/settings/reports/components/BotMetrics.vue` (the KPI-card + fetch pattern), `dashboard/routes/dashboard/settings/reports/reports.routes.js`, `dashboard/components-next/sidebar/Sidebar.vue` (Reports children ~lines 593–623).
- Create: `dashboard/routes/dashboard/settings/reports/ProtonAnalytics.vue`
- Modify: `reports.routes.js`, `Sidebar.vue`

**Interfaces:**
- Consumes: `useProtonMetricsAPI().getDashboard()` → `{ volume, resolution, csat, nps, speed, fallback, bounce, quality }` (arrays of channel rows — shapes per `query_port.py` dataclasses).

- [ ] **Step 1: Create `ProtonAnalytics.vue`** — KPI cards (total conversations from `volume`, bot-resolved % from `resolution`, CSAT avg from `csat`, NPS from `nps`) using `ReportMetricCard`, plus charts: volume-by-channel (`BarChart`, stacked by channel), resolution split (`DoughnutChart`), NPS split (`DoughnutChart`), speed p99 (`BarChart` grouped by `is_first_turn`), fallback/bounce (`BarChart`), quality (`BarChart`). Fetch in `onMounted`; wrap each chart in a card with an empty-state when its array is empty. Compute chart.js `collection` objects from the rows in `computed`s. Use `n-*` tokens for card frames (copy the container classes from `BotMetrics.vue`).

- [ ] **Step 2: Register the route** in `reports.routes.js` — add inside the reports children array:

```js
import ProtonAnalytics from './ProtonAnalytics.vue';
// ...
{
  path: 'proton_analytics',
  name: 'proton_analytics_reports',
  meta: { permissions: ['administrator', 'report_manage'] },
  component: ProtonAnalytics,
},
```

- [ ] **Step 3: Add the sidebar entry** in `Sidebar.vue` Reports `children` array (after the native entries): `{ label: 'Analytics', to: accountScopedRoute('proton_analytics_reports') }` (match the exact helper the file uses for sibling entries — confirm from the surrounding lines).

- [ ] **Step 4: Verify build** — `pnpm exec vite build 2>&1 | tail -20`. Expected: 0 errors, and the bundle contains a `ProtonAnalytics` chunk.

- [ ] **Step 5: Commit** `git add -A app/javascript/dashboard/routes/dashboard/settings/reports/ProtonAnalytics.vue reports.routes.js app/javascript/dashboard/components-next/sidebar/Sidebar.vue && git commit -m "feat(reports): native Analytics tab from /metrics/dashboard"`.

### Task A4: Anomaly tab (`ProtonAnomaly.vue`) + route + sidebar

**Files:**
- Create: `dashboard/routes/dashboard/settings/reports/ProtonAnomaly.vue`
- Modify: `reports.routes.js`, `Sidebar.vue`

**Interfaces:**
- Consumes: `useProtonMetricsAPI().getAnomalies()` → `{ anomalies: [{ channel, current_volume, baseline_mean, baseline_stddev }] }`.

- [ ] **Step 1: Create `ProtonAnomaly.vue`** — a table of currently-flagged channels: columns channel / current volume / baseline mean / stddev / deviation (compute `(current - mean) / stddev` as a badge, red when the backend flagged it). Empty-state "No anomalies detected" when the array is empty. Reuse card/table classes from a native report table (e.g. `overview/AgentTable.vue`).

- [ ] **Step 2: Register route** `{ path: 'proton_anomaly', name: 'proton_anomaly_reports', meta: { permissions: ['administrator','report_manage'] }, component: ProtonAnomaly }` (import at top).

- [ ] **Step 3: Sidebar entry** `{ label: 'Anomaly', to: accountScopedRoute('proton_anomaly_reports') }`.

- [ ] **Step 4: Verify build** — `pnpm exec vite build 2>&1 | tail -20`. Expected: 0 errors.

- [ ] **Step 5: Commit** `git commit -am "feat(reports): native Anomaly tab from /metrics/anomalies"`.

### Task A5: Export button on Analytics

**Files:**
- Modify: `dashboard/routes/dashboard/settings/reports/ProtonAnalytics.vue`

- [ ] **Step 1: Add an export control** to `ProtonAnalytics.vue` header — two links/buttons opening `useProtonMetricsAPI().exportUrl('xlsx')` and `exportUrl('pdf')` in a new tab (`window.open(url, '_blank')`). Label "Download Excel" / "Download PDF".

- [ ] **Step 2: Verify build** — `pnpm exec vite build 2>&1 | tail -20`. Expected: 0 errors.

- [ ] **Step 3: Commit** `git commit -am "feat(reports): PDF/Excel export on Analytics tab"`.

### Task A6: Export Phase-A patch, rebuild image, live smoke

**Files:**
- Create: `deploy/chatwoot-fork/patches/0020-reports-proton-analytics.patch` (in THIS repo)

- [ ] **Step 1: Generate the patch** from the clone — diff the Phase-A commits against the pre-Phase-A HEAD:

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git format-patch <pre-A-sha>..HEAD --stdout > \
  /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0020-reports-proton-analytics.patch
```

- [ ] **Step 2: Rebuild the fork image** per `deploy/chatwoot-fork/Dockerfile` (Mac build), restart local `default-chatwoot-rails` + `-sidekiq` on the new image. Confirm the `ProtonAnalytics` chunk is in `public/vite`.

- [ ] **Step 3: Live smoke** — open `http://crm.localhost` (Chrome) → Reports → Analytics: KPI cards + charts render from `/metrics/dashboard`; Reports → Anomaly renders; export buttons download xlsx/pdf. With the backend stopped, both tabs show empty-states (fail-open), rest of Reports unaffected.

- [ ] **Step 4: Commit the patch** in the id-crm repo: `git add deploy/chatwoot-fork/patches/0020-reports-proton-analytics.patch && git commit -m "feat(reports): Phase A patch — Analytics + Anomaly tabs + export"`.

---

# PHASE B — Tier 2 (backend read endpoints + tabs)

Deliverable: three `x-api-key`-gated read endpoints exposing existing Phase-3 views, plus the Departments & PIC, Call-Centre KPI, and Case Lifecycle tabs.

### Task B1: Config — metrics-read API key

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (near `qa_api_key`, ~line 141)
- Modify: `deploy/tenants/example.env`

**Interfaces:**
- Produces: `settings.metrics_api_key: str` (default `""`).

- [ ] **Step 1: Add the field** in `config.py` after `qa_api_key`:

```python
    # Metrics read endpoints (departments / call-centre / lifecycle) — agent/PIC-level
    # aggregates, so gated unlike the channel-only /metrics/dashboard.
    metrics_api_key: str = ""
```

- [ ] **Step 2: Document it** in `deploy/tenants/example.env` (near the other `*_API_KEY` entries):

```
# Gates GET /metrics/{departments,callcenter,lifecycle} (agent/PIC-level report data)
METRICS_API_KEY=
```

- [ ] **Step 3: Commit** `git add backend/apps/backend/src/chatbot/platform/config.py deploy/tenants/example.env && git commit -m "feat(metrics): add metrics_api_key config for gated report endpoints"`.

### Task B2: Port + Mock — Departments rows

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/query_port.py`
- Test: `backend/apps/backend/src/chatbot/features/metrics/test_query_port.py`

**Interfaces:**
- Produces: `DeptPicRow`, `ReopenRow`, `DepartmentsMetrics`; `MetricsQueryPort.fetch_departments() -> DepartmentsMetrics`; `MockMetricsQuery.fetch_departments`.

- [ ] **Step 1: Write the failing test** in `test_query_port.py`:

```python
import asyncio
from chatbot.features.metrics.query_port import MockMetricsQuery, DepartmentsMetrics

def test_mock_departments_shape():
    m = asyncio.run(MockMetricsQuery().fetch_departments())
    assert isinstance(m, DepartmentsMetrics)
    assert m.dept_pic and m.dept_pic[0].department
    assert m.reopen and 0.0 <= (m.reopen[0].reopen_rate or 0) <= 1.0
```

- [ ] **Step 2: Run — expect fail** `cd backend/apps/backend && GOOGLE_API_KEY=x .venv/bin/pytest src/chatbot/features/metrics/test_query_port.py::test_mock_departments_shape -v`. Expected: FAIL (`ImportError: DepartmentsMetrics`).

- [ ] **Step 3: Implement** — add to `query_port.py`:

```python
@dataclass(frozen=True)
class DeptPicRow:
    department: str
    pic: str
    cases: int
    avg_first_response_min: float | None
    avg_resolution_min: float | None
    resolution_rate: float | None


@dataclass(frozen=True)
class ReopenRow:
    dealer: str
    department: str
    pic: str
    cases: int
    reopened: int
    reopen_rate: float | None


@dataclass(frozen=True)
class DepartmentsMetrics:
    dept_pic: list[DeptPicRow]
    reopen: list[ReopenRow]
```

Add to the `MetricsQueryPort` Protocol: `async def fetch_departments(self) -> DepartmentsMetrics: ...`. Add to `MockMetricsQuery`:

```python
    async def fetch_departments(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=[DeptPicRow("Aftersales", "Ali", 40, 12.0, 240.0, 0.9)],
            reopen=[ReopenRow("Dealer KL", "Aftersales", "Ali", 40, 4, 0.1)],
        )
```

- [ ] **Step 4: Run — expect pass** (same command). Expected: PASS.

- [ ] **Step 5: Commit** `git add src/chatbot/features/metrics/query_port.py src/chatbot/features/metrics/test_query_port.py && git commit -m "feat(metrics): departments port rows + mock"`.

### Task B3: Port + Mock — Call-Centre rows

**Files:**
- Modify: `features/metrics/query_port.py`
- Test: `features/metrics/test_query_port.py`

**Interfaces:**
- Produces: `SlaAchievementRow`, `TasksPerAgentRow`, `FirstResponseRow`, `ResolutionTimeRow`, `ComplaintTypeRow`, `PeakHourRow`, `NpsByAgentRow`, `CallCentreMetrics`; `fetch_callcenter() -> CallCentreMetrics`.

- [ ] **Step 1: Write the failing test**:

```python
from chatbot.features.metrics.query_port import CallCentreMetrics

def test_mock_callcenter_shape():
    m = asyncio.run(MockMetricsQuery().fetch_callcenter())
    assert isinstance(m, CallCentreMetrics)
    assert m.nps_by_agent and m.nps_by_agent[0].channel in ("Phone", "WhatsApp")
    assert m.peak_hours and 1 <= m.peak_hours[0].day_of_week <= 7
```

- [ ] **Step 2: Run — expect fail** (`pytest ...::test_mock_callcenter_shape`). Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** — add dataclasses mirroring the views exactly:

```python
@dataclass(frozen=True)
class SlaAchievementRow:
    channel: str
    division: str
    with_sla: int
    met: int
    sla_achievement_rate: float | None


@dataclass(frozen=True)
class TasksPerAgentRow:
    agent_id: str
    pic: str
    cases: int
    avg_first_response_min: float | None
    avg_resolution_min: float | None
    resolved_cases: int


@dataclass(frozen=True)
class FirstResponseRow:
    channel: str
    avg_first_response_min: float | None
    p50_first_response_min: int | None
    p90_first_response_min: int | None
    with_first_response: int


@dataclass(frozen=True)
class ResolutionTimeRow:
    channel: str
    division: str
    avg_min: float | None
    p50_min: int | None
    p90_min: int | None


@dataclass(frozen=True)
class ComplaintTypeRow:
    category: str
    subcategory: str
    division: str
    cases: int
    share_pct: float | None


@dataclass(frozen=True)
class PeakHourRow:
    day_of_week: int
    hour_of_day: int
    channel: str
    volume: int


@dataclass(frozen=True)
class NpsByAgentRow:
    agent_id: str
    channel: str
    respondents: int
    nps: float | None


@dataclass(frozen=True)
class CallCentreMetrics:
    sla: list[SlaAchievementRow]
    tasks_per_agent: list[TasksPerAgentRow]
    first_response: list[FirstResponseRow]
    resolution_time: list[ResolutionTimeRow]
    complaint_types: list[ComplaintTypeRow]
    peak_hours: list[PeakHourRow]
    nps_by_agent: list[NpsByAgentRow]
```

Add `async def fetch_callcenter(self) -> CallCentreMetrics: ...` to the Protocol, and a `MockMetricsQuery.fetch_callcenter` returning one representative row per list (e.g. `NpsByAgentRow("Ali", "Phone", 30, 45.0)`, `PeakHourRow(2, 14, "whatsapp", 55)`).

- [ ] **Step 4: Run — expect pass**. Expected: PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(metrics): call-centre port rows + mock"`.

### Task B4: Port + Mock — Lifecycle rows

**Files:**
- Modify: `features/metrics/query_port.py`
- Test: `features/metrics/test_query_port.py`

**Interfaces:**
- Produces: `CaseLifecycleRow`, `StateTrendRow`, `LifecycleMetrics`; `fetch_lifecycle() -> LifecycleMetrics`.

- [ ] **Step 1: Write the failing test**:

```python
from chatbot.features.metrics.query_port import LifecycleMetrics

def test_mock_lifecycle_shape():
    m = asyncio.run(MockMetricsQuery().fetch_lifecycle())
    assert isinstance(m, LifecycleMetrics)
    assert m.cases and m.cases[0].conversation_id
    assert m.state_trend and m.state_trend[0].status
```

- [ ] **Step 2: Run — expect fail**. Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** — `v_case_lifecycle` returns timestamp columns (BigQuery returns `datetime`); type them `datetime | None` and `from datetime import datetime` at the top:

```python
@dataclass(frozen=True)
class CaseLifecycleRow:
    conversation_id: str
    channel: str
    division: str
    department: str
    dealer: str
    status: str
    created_at: datetime | None
    first_response_at: datetime | None
    resolved_at: datetime | None
    first_response_minutes: int | None
    resolution_minutes: int | None
    reopen_count: int | None


@dataclass(frozen=True)
class StateTrendRow:
    month: str
    status: str
    division: str
    cases: int


@dataclass(frozen=True)
class LifecycleMetrics:
    cases: list[CaseLifecycleRow]
    state_trend: list[StateTrendRow]
```

Add `async def fetch_lifecycle(self) -> LifecycleMetrics: ...` to the Protocol and a `MockMetricsQuery.fetch_lifecycle` (pass `created_at=None` etc. in the mock row to avoid `Date.now`-style calls — the mock only needs shape).

- [ ] **Step 4: Run — expect pass**. Expected: PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(metrics): lifecycle port rows + mock"`.

### Task B5: BigQuery adapter — three new fetch methods

**Files:**
- Modify: `features/metrics/query_adapter.py`
- Test: `features/metrics/test_query_adapter.py`

**Interfaces:**
- Consumes: the row dataclasses from B2–B4.
- Produces: `BigQueryMetricsQuery.fetch_departments/fetch_callcenter/fetch_lifecycle` using `self._block(view, RowType)` and `asyncio.to_thread`.

- [ ] **Step 1: Write the failing test** using a fake BQ client (follow the existing `test_query_adapter.py` fake-client style). Assert `fetch_departments()` issues `SELECT * FROM ....v_dept_pic_performance` and `...v_reopen_rate` and returns `DepartmentsMetrics`:

```python
def test_departments_reads_expected_views(fake_client_recording_queries):
    q = BigQueryMetricsQuery(settings_stub, client=fake_client_recording_queries)
    asyncio.run(q.fetch_departments())
    assert any("v_dept_pic_performance" in sql for sql in fake_client_recording_queries.queries)
    assert any("v_reopen_rate" in sql for sql in fake_client_recording_queries.queries)
```

(Mirror the exact fake-client fixture already in `test_query_adapter.py`; add analogous assertions for `fetch_callcenter` → 7 views and `fetch_lifecycle` → 2 views.)

- [ ] **Step 2: Run — expect fail** `GOOGLE_API_KEY=x .venv/bin/pytest src/chatbot/features/metrics/test_query_adapter.py -k departments -v`. Expected: FAIL (`AttributeError: fetch_departments`).

- [ ] **Step 3: Implement** in `query_adapter.py` — import the new row types, then:

```python
    def _fetch_departments_sync(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=self._block("v_dept_pic_performance", DeptPicRow),
            reopen=self._block("v_reopen_rate", ReopenRow),
        )

    async def fetch_departments(self) -> DepartmentsMetrics:
        return await asyncio.to_thread(self._fetch_departments_sync)

    def _fetch_callcenter_sync(self) -> CallCentreMetrics:
        return CallCentreMetrics(
            sla=self._block("v_sla_achievement", SlaAchievementRow),
            tasks_per_agent=self._block("v_tasks_per_agent", TasksPerAgentRow),
            first_response=self._block("v_first_response_by_channel", FirstResponseRow),
            resolution_time=self._block("v_resolution_time", ResolutionTimeRow),
            complaint_types=self._block("v_complaint_type_ranking", ComplaintTypeRow),
            peak_hours=self._block("v_peak_hours", PeakHourRow),
            nps_by_agent=self._block("v_nps_by_agent", NpsByAgentRow),
        )

    async def fetch_callcenter(self) -> CallCentreMetrics:
        return await asyncio.to_thread(self._fetch_callcenter_sync)

    def _fetch_lifecycle_sync(self) -> LifecycleMetrics:
        return LifecycleMetrics(
            cases=self._block("v_case_lifecycle", CaseLifecycleRow),
            state_trend=self._block("v_state_trend", StateTrendRow),
        )

    async def fetch_lifecycle(self) -> LifecycleMetrics:
        return await asyncio.to_thread(self._fetch_lifecycle_sync)
```

- [ ] **Step 4: Run — expect pass** `GOOGLE_API_KEY=x .venv/bin/pytest src/chatbot/features/metrics/test_query_adapter.py -v`. Expected: PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(metrics): BigQuery adapter reads departments/callcenter/lifecycle views"`.

### Task B6: Insights router (3 gated endpoints) + mount

One router file exposing all three read endpoints behind the shared `metrics_api_key` check (DRY refinement of the spec's three separate files).

**Files:**
- Create: `features/metrics/insights_router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py` (after line 223)
- Test: `features/metrics/test_insights_router.py`

**Interfaces:**
- Consumes: `MetricsQueryPort` (from `build_metrics_query_port`) + `Settings`.
- Produces: `build_metrics_insights_router(port, settings) -> APIRouter` serving `GET /metrics/{departments,callcenter,lifecycle}`.

- [ ] **Step 1: Write the failing test** (FastAPI `TestClient`, mock port, `metrics_api_key="secret"`):

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.features.metrics.insights_router import build_metrics_insights_router

def _client(key="secret"):
    class S: metrics_api_key = key
    app = FastAPI()
    app.include_router(build_metrics_insights_router(MockMetricsQuery(), S()))
    return TestClient(app)

def test_departments_requires_key():
    assert _client().get("/metrics/departments").status_code == 401

def test_departments_ok_with_key():
    r = _client().get("/metrics/departments", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert "dept_pic" in r.json()

def test_callcenter_and_lifecycle_ok():
    c = _client()
    assert c.get("/metrics/callcenter", headers={"x-api-key": "secret"}).status_code == 200
    assert c.get("/metrics/lifecycle", headers={"x-api-key": "secret"}).status_code == 200
```

- [ ] **Step 2: Run — expect fail** `GOOGLE_API_KEY=x .venv/bin/pytest src/chatbot/features/metrics/test_insights_router.py -v`. Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `insights_router.py`** (auth copied from `qa_router.py`):

```python
"""GET /metrics/{departments,callcenter,lifecycle} — gated report reads.

Agent/PIC-level aggregates, so x-api-key gated (unlike /metrics/dashboard)."""
from __future__ import annotations

import hmac
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException

if TYPE_CHECKING:
    from chatbot.features.metrics.query_port import MetricsQueryPort
    from chatbot.platform.config import Settings


def build_metrics_insights_router(port: MetricsQueryPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    def _require_key(x_api_key: str | None) -> None:
        key = settings.metrics_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode(), key.encode())
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @router.get("/metrics/departments")
    async def departments(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_departments())

    @router.get("/metrics/callcenter")
    async def callcenter(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_callcenter())

    @router.get("/metrics/lifecycle")
    async def lifecycle(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_lifecycle())

    return router
```

- [ ] **Step 4: Mount in `main.py`** — add import near line 50 and, right after line 223 (`build_metrics_anomaly_router`):

```python
from chatbot.features.metrics.insights_router import build_metrics_insights_router
# ...
app.include_router(build_metrics_insights_router(query_port, settings))
```

- [ ] **Step 5: Run — expect pass** `GOOGLE_API_KEY=x .venv/bin/pytest src/chatbot/features/metrics/test_insights_router.py -v`. Expected: PASS (401 without key, 200 with).

- [ ] **Step 6: Full metrics suite + lint** `GOOGLE_API_KEY=x .venv/bin/pytest src/chatbot/features/metrics/ -q && .venv/bin/ruff check src/chatbot/features/metrics/`. Expected: all pass, ruff clean.

- [ ] **Step 7: Commit** `git commit -am "feat(metrics): gated /metrics/{departments,callcenter,lifecycle} endpoints"`.

### Task B7: Departments & PIC tab (`ProtonDepartments.vue`)

**Files:**
- Create (clone): `dashboard/routes/dashboard/settings/reports/ProtonDepartments.vue`
- Modify: `reports.routes.js`, `Sidebar.vue`

**Interfaces:**
- Consumes: `useProtonMetricsAPI().getDepartments()` → `{ dept_pic: [...], reopen: [...] }`.

- [ ] **Step 1: Create the view** — a `dept_pic` table (department, pic, cases, avg first-response min, avg resolution min, resolution rate) sortable by cases, plus a reopen/CRR table (dealer, department, pic, reopen_rate as %). Add a `BarChart` of top-N depts by cases. Empty-states per section. `n-*` tokens; mirror `overview/AgentTable.vue` for table styling.

- [ ] **Step 2: Register route** `proton_departments` / name `proton_departments_reports` (perms as global constraint).

- [ ] **Step 3: Sidebar entry** `{ label: 'Departments & PIC', to: accountScopedRoute('proton_departments_reports') }`.

- [ ] **Step 4: Verify build** `pnpm exec vite build 2>&1 | tail -20`. Expected: 0 errors.

- [ ] **Step 5: Commit** `git commit -am "feat(reports): Departments & PIC tab"`.

### Task B8: Call-Centre KPI tab (`ProtonCallCentre.vue`, incl. agent NPS)

**Files:**
- Create (clone): `dashboard/routes/dashboard/settings/reports/ProtonCallCentre.vue`
- Modify: `reports.routes.js`, `Sidebar.vue`

**Interfaces:**
- Consumes: `getCallCentre()` → `{ sla, tasks_per_agent, first_response, resolution_time, complaint_types, peak_hours, nps_by_agent }`.

- [ ] **Step 1: Create the view** with sections: SLA achievement (KPI card = overall met/with_sla, `BarChart` by channel); tasks-per-agent table; first-response & resolution-time by channel (`BarChart`, avg + p90); complaint-type ranking table (top by cases, share %); peak-hours heatmap (reuse `components/heatmaps/BaseHeatmap.vue` if the prop shape fits, else a `BarChart` of volume by hour); **NPS-for-agent** section — `nps_by_agent` grouped by agent with a `BarChart` (Phone vs WhatsApp series) + a KPI card for blended agent NPS. Empty-states per section.

- [ ] **Step 2: Register route** `proton_callcentre` / `proton_callcentre_reports`.

- [ ] **Step 3: Sidebar entry** `{ label: 'Call-Centre KPI', to: accountScopedRoute('proton_callcentre_reports') }`.

- [ ] **Step 4: Verify build** `pnpm exec vite build 2>&1 | tail -20`. Expected: 0 errors.

- [ ] **Step 5: Commit** `git commit -am "feat(reports): Call-Centre KPI tab incl. agent NPS"`.

### Task B9: Case Lifecycle tab (`ProtonCaseLifecycle.vue`)

**Files:**
- Create (clone): `dashboard/routes/dashboard/settings/reports/ProtonCaseLifecycle.vue`
- Modify: `reports.routes.js`, `Sidebar.vue`

**Interfaces:**
- Consumes: `getLifecycle()` → `{ cases: [...], state_trend: [...] }`.

- [ ] **Step 1: Create the view** — a resolution-time distribution histogram bucketed from `cases[].resolution_minutes` (compute buckets in a `computed`, render `BarChart`), and a state-trend `LineChart` (x = `state_trend[].month`, one series per `status`: Higher-escalation / WIP / Temp-Closed / Closed). A summary table of recent cases (conversation_id, channel, department, dealer, status, resolution_minutes). Empty-states per section.

- [ ] **Step 2: Register route** `proton_case_lifecycle` / `proton_case_lifecycle_reports`.

- [ ] **Step 3: Sidebar entry** `{ label: 'Case Lifecycle', to: accountScopedRoute('proton_case_lifecycle_reports') }`.

- [ ] **Step 4: Verify build** `pnpm exec vite build 2>&1 | tail -20`. Expected: 0 errors.

- [ ] **Step 5: Commit** `git commit -am "feat(reports): Case Lifecycle tab"`.

### Task B10: Export Phase-B patch, rebuild, live smoke

**Files:**
- Create: `deploy/chatwoot-fork/patches/0021-reports-proton-tier2.patch`

- [ ] **Step 1: Generate the patch** — `git format-patch <end-of-A-sha>..HEAD --stdout > .../deploy/chatwoot-fork/patches/0021-reports-proton-tier2.patch`.

- [ ] **Step 2: Set `METRICS_API_KEY`** in the local tenant env (`deploy/tenants/default.env`) + restart the backend container so the gated endpoints authorize; ensure the frontend `useProtonConfig` `apiKey` carries it (confirm from patch 0001 how the key reaches the SPA — same channel Knowledge uses).

- [ ] **Step 3: Rebuild the fork image**, restart rails+sidekiq, confirm the three new chunks are in the bundle.

- [ ] **Step 4: Live smoke** — Reports → Departments & PIC / Call-Centre KPI / Case Lifecycle each render from their endpoints; without the key the endpoints 401 and the tabs show empty-states; agent-NPS chart shows Phone/WhatsApp. Confirm backend `pytest` green and `curl -s -H 'x-api-key: $KEY' localhost:8080/metrics/callcenter` returns the 7 blocks.

- [ ] **Step 5: Commit the patch** `git add deploy/chatwoot-fork/patches/0021-reports-proton-tier2.patch && git commit -m "feat(reports): Phase B patch — Departments/Call-Centre/Lifecycle tabs"`.

---

## Deferred (out of scope — future plan)

- **Tag-keyword report filter** (BR "run report by request tag key work").
- **Scheduled auto-send to management** — `features/metrics/scheduler.py` exists; wiring the cron/recipients is ops config.
- **Per-tab export for Tier-2** — extend `/metrics/export` with a `report=` param (Tier-1 export ships in Phase A).

## Self-review notes

- Spec coverage: Analytics (A3), Departments & PIC (B7), Call-Centre KPI incl. agent-NPS (B8), Case Lifecycle (B9), Anomaly (A4), export (A5), gated Tier-2 endpoints (B6), config (B1). All spec tabs mapped.
- Resolved spec open-item #1: `v_nps_by_agent` already exists (Phone/WhatsApp, agent-level) — no NPS-collection change needed.
- Deviation from spec: three endpoints live in one `insights_router.py` (DRY) rather than three files; endpoint paths unchanged.
- Types consistent across tasks: row dataclass field names copied verbatim from `bigquery_schema.py` view SELECT columns; adapter `_block(view, RowType)` uses the same names.
