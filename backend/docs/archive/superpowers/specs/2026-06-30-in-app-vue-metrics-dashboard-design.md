# In-App Vue Metrics Dashboard — Design

**Date:** 2026-06-30
**Status:** Approved (brainstorming)
**Related:** Bot-Metrics dashboard phases 1–4 (BigQuery views), ADR [0002](../../decisions/0002-monorepo-vue-frontend-gemini-audio.md)

## Goal

Surface the existing Bot-Metrics in an **embedded Vue dashboard inside the current
frontend** — a new `/dashboard` route in the same SPA, not Looker Studio and not a
separate app. Reuse the 8 BigQuery views already built and populated in
`lv-playground-genai.demo_proton`; add only a thin backend read-side and a frontend
feature slice.

## Non-Goals (YAGNI)

- No Looker Studio work (this replaces it for the in-app use case; the Looker guides remain valid for external/embedded use).
- No new metric logic — the SQL views remain the single source of truth.
- No write paths, no auth-gated mutation, no real-time streaming/websockets (a manual refresh button suffices).
- No date-range picker for v1 (views are pre-aggregated by month/channel). Client-side channel filter only.
- No mobile-specific layout beyond responsive grid reflow.

## Architecture & Data Flow

```
BigQuery (8 existing views)
   ↓  SELECT * FROM v_*
Backend — features/metrics read-side (NEW, additive)
   MetricsQueryPort                (Protocol)
   ├─ BigQueryMetricsQuery         (live; SELECTs the 8 views, asyncio.to_thread)
   └─ MockMetricsQuery             (tests + local dev; returns demo-shaped data)
   build_metrics_query_port(settings)  → picks by METRICS_PROVIDER (reuses existing flag)
   ↓  GET /metrics/dashboard → JSON (all 8 blocks, one round-trip)
Frontend — src/features/dashboard/ (NEW)
   api/dashboard.ts → store (Pinia) → components (ApexCharts tiles)
   route: /dashboard   (vue-router, NEW)
```

This mirrors the existing `features/chat` and `features/metrics` patterns: I/O behind a
port, a mock for tests, a thin router wired through the existing
`_wire_metrics_features(app, settings)` helper in `main.py`. Follows the
testability-first architecture rule (I/O behind interfaces, pure logic inward).

## Backend — Read-Side (additive)

### New files (under `apps/backend/src/chatbot/features/metrics/`)

- **`query_port.py`** — `MetricsQueryPort` Protocol with one async method
  `async def fetch_dashboard(self) -> DashboardMetrics`. Plus frozen dataclasses, one per
  view, matching the exact view columns (below).
- **`query_adapter.py`**
  - `BigQueryMetricsQuery` — runs `SELECT * FROM \`{project}.{dataset}.v_*\`` for each
    view, each wrapped in `asyncio.to_thread` so it never blocks the event loop; maps rows
    into the dataclasses. Reuses the same `bigquery.Client` init pattern as the existing
    metrics adapters; falls back to mock on init failure.
  - `MockMetricsQuery` — returns a representative `DashboardMetrics` (the demo shape) so
    tests and local dev never touch BigQuery.
  - `build_metrics_query_port(settings) -> MetricsQueryPort` — returns `BigQueryMetricsQuery`
    when `METRICS_PROVIDER == "bigquery"`, else `MockMetricsQuery`.
- **`dashboard_router.py`** — `GET /metrics/dashboard` → returns `DashboardMetrics` as JSON
  (Pydantic response model or dataclass→dict). One endpoint returning all 8 blocks:
  `{volume, resolution, csat, nps, speed, fallback, bounce, quality}`.

### Wiring

Add the port build + router include inside the existing
`_wire_metrics_features(app, settings)` in `main.py`. No new config keys — reuses the
existing `METRICS_PROVIDER` (`noop`/mock vs `bigquery`) and `BIGQUERY_*` settings.

### Result dataclasses (exact columns from the live views)

Each block is a `list` of per-channel rows except volume (per month×channel):

- **volume** (`v_volume_by_month_channel`): `month: str`, `channel: str`, `volume: int`
- **resolution** (`v_resolution_split`): `channel`, `closed_by_bot: int`, `transfer_to_agent: int`, `total: int`, `closed_by_bot_pct: float|None`, `transfer_to_agent_pct: float|None`
- **csat** (`v_csat`): `channel`, `respondents: int`, `avg_score: float|None`, `satisfied_rate: float|None`
- **nps** (`v_nps`): `channel`, `respondents: int`, `promoters: int`, `passives: int`, `detractors: int`, `nps: float|None`
- **speed** (`v_speed_of_response`): `channel`, `is_first_turn: bool`, `p99_latency_ms: int|None`, `avg_latency_ms: float|None`, `turns: int`
- **fallback** (`v_fallback_rate`): `channel`, `fallback_rate: float|None`, `turns: int`
- **bounce** (`v_bounce_rate`): `channel`, `bounced: int`, `total_sessions: int`, `bounce_rate: float|None`
- **quality** (`v_quality`): `channel`, `labels: int`, `avg_accuracy: float|None`, `avg_quality: float|None`

`SAFE_DIVIDE`/`AVG` columns can be `NULL` → typed `Optional` and rendered as “—” in the UI.

### Auth (POC decision)

The read endpoint is **open** for the POC. The payload is channel-level **aggregates only**
— no PII, no message content. Requiring `X-API-Key` would force embedding a key in the
browser, which is worse. Locking it later is a config flip (can reuse the `QA_API_KEY`
gate pattern). This is recorded as a deliberate POC choice, not an oversight.

## Frontend — `src/features/dashboard/` (additive)

New dependencies: **`vue-router`**, **`vue3-apexcharts`** (+ `apexcharts`).

### Routing

- Add `vue-router` with two routes: `/` (existing channels/`HomeView`) and `/dashboard`
  (new `DashboardView`). `App.vue` renders `<router-view>`.
- Add a nav link/toggle in `AppHeader` to switch between Channels and Dashboard.

### Feature slice (matches existing `chat`/`voice`/`phone` convention)

- **`types/`** — TS interfaces mirroring the backend `DashboardMetrics` blocks.
- **`api/dashboard.ts`** — `fetchDashboard(): Promise<DashboardMetrics>` using the existing
  `plugins/api.ts` `API_BASE_URL` + fetch pattern.
- **`store/`** — Pinia store: `metrics`, `loading`, `error`, `selectedChannel`, `load()`,
  and getters that filter blocks by `selectedChannel` client-side.
- **`components/`** — one component per tile (below) + `MetricCard.vue` (KPI cards) +
  `ChannelFilter.vue`. ApexCharts registered globally or per-component.
- **`views/DashboardView.vue`** (or under `layouts/`) — composes the grid layout, calls
  `store.load()` on mount, shows loading/error/empty states, and a manual Refresh button.

### Tile layout (XLSMART-style, responsive grid)

```
┌───────────────────────────────────────────────────────────────┐
│ KPI CARDS:  Total Convos │ Bot-Resolved % │ CSAT avg │ NPS      │
├───────────────────────────────────────────────────────────────┤
│ Monthly Volume by Channel             (stacked bar, full width) │
├──────────────────────────────┬────────────────────────────────┤
│ Resolution Split              │ CSAT (radial gauge) +           │
│ (donut: bot vs agent)         │ NPS (gauge + promoter/passive/  │
│                               │ detractor breakdown)            │
├──────────────────────────────┴────────────────────────────────┤
│ Speed p99 │ Fallback Rate │ Bounce Rate     (operational row)   │
├───────────────────────────────────────────────────────────────┤
│ Accuracy & Quality by Channel              (grouped bar)        │
└───────────────────────────────────────────────────────────────┘
```

All 8 views represented; none dropped. The **channel filter** (All / Web / WhatsApp /
Email / Phone) toggles displayed series client-side — no refetch, since the data is
already per-channel. Speed tile shows p99 split by `is_first_turn`.

## Error / Edge Handling

- Backend adapter init failure → falls back to `MockMetricsQuery` (never 500s the dashboard).
- Empty views (no data yet) → endpoint returns empty blocks; UI shows an empty state per tile.
- `NULL` aggregate values → rendered as “—”, not `0` (avoids implying a real zero metric).
- Network/HTTP error in the store → `error` state with a Retry action.

## Testing

- **Backend:** pytest with `MockMetricsQuery` — assert `/metrics/dashboard` JSON shape and
  status; unit-test the row→dataclass mapping in `BigQueryMetricsQuery` against fake
  `RowIterator` data. No live BigQuery in CI (matches all prior phases). Keep mypy strict
  and ruff clean (existing baselines: mypy 3, ruff 1 — do not regress).
- **Frontend:** `vue-tsc` strict type-check must pass; tile components render against a
  fixture `DashboardMetrics` payload (smoke render, no live backend).

## Rollout / Live Enablement

1. Local/CI default (`METRICS_PROVIDER=noop`) → dashboard renders mock data; everything testable offline.
2. Live: backend already supports `METRICS_PROVIDER=bigquery`; the new read adapter then
   serves the real 8 views (already populated with 25 real + demo rows).
3. Frontend `VITE_API_BASE_URL` already points at the backend; no new frontend config.

## Open Items Deferred (follow-ups)

- Date-range / month picker.
- Auth gate on the read endpoint (if the dashboard becomes externally reachable).
- Per-tile drill-down / CSV export.
- Auto-refresh / live updates.
