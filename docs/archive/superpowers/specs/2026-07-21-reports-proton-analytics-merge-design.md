# Design: Proton Reporting merged into Chatwoot Reports

**Date:** 2026-07-21
**Status:** Approved (design), pending implementation plan
**Related:** Proton BR "CRM System Enhancement" (Reporting page); Phase 3 reporting/BI
(`docs/superpowers/plans/2026-07-18-phase3-reporting-bi.md`); Knowledge native-view
patches 0010–0019 (the pattern this reuses).

## Problem / Goal

Proton's Business Requirement ("CRM System Enhancement", Reporting section) asks for a
rich reporting surface: channel-source analysis, department/PIC analysis, Call-Centre
KPIs, NPS-for-agent, case-lifecycle tracking, an anomaly-warning dashboard, and
PDF/Excel export. Two reporting surfaces exist today and answer different questions:

- **Chatwoot's native Reports menu** — 9 tabs (Overview, Conversation, Agents, Labels,
  Inbox, Team, CSAT, SLA, Bot) backed by the `reporting_events` Postgres table.
- **The standalone Proton frontend dashboard** (`proton-conversational-ai/apps/frontend`,
  Vue 3 + ApexCharts) — a `/dashboard` view rendering KPI cards + 8 charts from a single
  `GET /metrics/dashboard` call, backed by the BigQuery metrics pipeline.

Managers should not hop between two products. **Goal: rebuild the Proton analytics as
native Chatwoot report tabs, living inside the existing Reports menu**, so all reporting
lives in one place and reuses Chatwoot's nav/tab shell, filters, and design tokens.

The native tabs (`reporting_events`-backed) stay as-is; the new Proton tabs read from the
proton backend `/metrics/*` (BigQuery-backed). The two data worlds coexist by design —
they answer different questions.

## Non-goals

- Not replacing or re-pointing Chatwoot's 9 native report tabs.
- Not Customer 360 / DMS+TSP integration (parked pending Proton clarification).
- Not building new BigQuery views — Phase 3 already created them; this exposes them.
- Not the scheduled auto-send-to-management wiring (backend `scheduler.py` exists; the
  cron/config is ops work — a follow-up, not this spec).

## Chosen approach

**Native rebuild on chart.js** (chosen over: ApexCharts port, or iframe embed).

Rebuild the dashboards using Chatwoot's existing `chart.js` / `vue-chartjs` (already
dependencies) so the new tabs are visually consistent with native reports and add **no
new frontend dependency**. Wire data from the proton backend over HTTP using the
runtime-config pattern the Knowledge patches (0010–0019) already proved. Delivered as new
Chatwoot fork patches (`0020+`), the same delivery mechanism as every prior first-party
Chatwoot customization in this repo.

Rejected:
- **ApexCharts port** — fastest (reuses the existing Proton chart components verbatim) but
  adds a second charting library alongside chart.js, grows the bundle, and needs theme
  reconciliation. Not worth a permanent second chart lib.
- **Iframe / dashboard-app embed** — near-zero re-port, but iframe theme/auth friction, and
  the team explicitly retired iframes in favour of native views for Knowledge (patches
  0010/0011). Does not truly live in the Reports nav.

## The tabs (5, BR-aligned)

Each is a native Vue view under
`app/javascript/dashboard/routes/dashboard/settings/reports/`, registered in
`reports.routes.js` and added as a child of the Reports group in `Sidebar.vue`
(lines ~593–623). All gated by `FEATURE_FLAGS.REPORTS` + `['administrator','report_manage']`.

| # | Tab | Content (BR coverage) | Data source | Tier |
|---|-----|------|------|------|
| 1 | **Analytics** | KPI cards (total conversations, bot-resolved %, CSAT, NPS) + charts: volume-by-channel, resolution split, CSAT, NPS (promoters/passives/detractors), speed p99 by channel, fallback rate, bounce rate, accuracy/quality. Covers: channel-source analysis, CSAT, channel-level NPS. | `GET /metrics/dashboard` — **exists** | **1** |
| 2 | **Departments & PIC** | Case distribution & resolution by dealer/department/PIC; first-response-rate / resolution-rate / resolution-time ranking; reopen rate (CRR) by dealer/dept/PIC. | **new** `GET /metrics/departments` | 2 |
| 3 | **Call-Centre KPI** | SLA achievement rate; tasks-per-agent + avg response; first-response & closure time by channel; popular-complaint-type ranking; peak complaint hours; **NPS-for-agent** (customer-rates-agent, Call & WA). | **new** `GET /metrics/callcenter` | 2 |
| 4 | **Case Lifecycle** | creation→closing time distribution ("map"); state-trend of special states (Higher-escalation / WIP / Temp-Closed / Closed). | **new** `GET /metrics/lifecycle` | 2 |
| 5 | **Anomaly** | real-time channel-volume anomaly warnings (e.g. "explosion of a channel"). | `GET /metrics/anomalies` — **exists** | **1** |

Design choice: **NPS-for-agent is folded into the Call-Centre KPI tab** (both are
agent-performance views). It can be split into its own tab later without rework — it is a
separate chart component either way.

### Cross-cutting

- **Export** — each tab surfaces a PDF/Excel download. `GET /metrics/export?format=xlsx|pdf`
  exists for the Analytics dashboard; Tier-2 tabs extend it (add `report=` param or
  per-endpoint export) in Phase B.
- **Filters** — date range + channel selector, reusing Chatwoot's `ReportFilters` /
  `OverviewReportFilters` pattern so the look matches native tabs.
- **"Run report by request tag keyword"** (BR) → a tag filter; deferred to an optional
  Phase C.

## Architecture

### Data-source tiers

The existing FE-consumable `/metrics/*` surface (verified in
`backend/apps/backend/src/chatbot/features/metrics/`):

- `GET /metrics/dashboard` — channel-level aggregates (volume/resolution/csat/nps/speed/
  fallback/bounce/quality). Unauthenticated by design (POC; channel aggregates, no PII).
- `GET /metrics/anomalies` — flagged channel-volume anomalies.
- `GET /metrics/export?format=xlsx|pdf` — downloadable report of the dashboard.

The Phase-3 BigQuery **views** (`v_peak_hours`, `v_complaint_type_ranking`,
`v_tasks_per_agent`, `v_first_response_by_channel`, `v_case_lifecycle`, `v_state_trend`,
plus the `dealer` dimension and live `reopen_count`) exist in `bigquery_schema.py` but are
**not exposed over HTTP** — they were built for Power BI. Tiers:

- **Tier 1 (endpoints exist, FE-only work):** Analytics, Anomaly, export button.
- **Tier 2 (need new backend endpoints + FE):** Departments & PIC, Call-Centre KPI,
  Case Lifecycle. These wrap already-existing views — no new data pipeline.

### Backend (Tier 2) — 3 new read endpoints

In the metrics feature, add three read endpoints that expose the existing views as JSON
through the existing `MetricsQueryPort` / `query_adapter` (the same port `/metrics/dashboard`
uses). Each returns typed row blocks mirroring the view columns:

- `GET /metrics/departments` → dealer/dept/PIC distribution, resolution ranking, reopen/CRR
  (wraps `v_complaint_type_ranking`, dealer dim, `reopen_count`).
- `GET /metrics/callcenter` → SLA achievement, `v_tasks_per_agent`,
  `v_first_response_by_channel`, `v_peak_hours`, `v_complaint_type_ranking`, agent-NPS.
- `GET /metrics/lifecycle` → `v_case_lifecycle`, `v_state_trend`.

**Auth:** gate the three new endpoints with `x-api-key` (reusing the `qa_api_key` /
`TASKS_API_KEY` pattern in `qa_router.py`, `faq_router.py`, `/tasks/mine`). Unlike the
channel-only `/metrics/dashboard`, these carry agent/PIC-level aggregates and should not be
anonymous. New config: a metrics-read API key in `deploy/tenants/example.env` +
`platform/config.py` (follow the existing `qa_api_key` shape).

### Frontend (fork patches)

- `api/protonMetrics.js` — a small API client that calls the proton backend `/metrics/*`
  via the runtime-config (`useProtonConfig`) accessor, exactly like `api/protonKnowledge.js`.
  Sends the metrics-read API key header for Tier-2 endpoints.
- 5 views: `ProtonAnalytics.vue`, `ProtonDepartments.vue`, `ProtonCallCentre.vue`,
  `ProtonCaseLifecycle.vue`, `ProtonAnomaly.vue`.
- Reuse `components/ReportMetricCard.vue` for KPI cards and
  `shared/components/charts/BarChart.vue` for bars; add thin `LineChart.vue` /
  `DoughnutChart.vue` wrappers over the existing `vue-chartjs` where a bar won't do
  (donut for resolution/NPS split, line for trends, gauge-style for CSAT).
- Register routes in `reports.routes.js`; add children under the Reports group in
  `Sidebar.vue`. Style with `n-*` design tokens (matches Knowledge views).

### Data flow

```
Reports tab (native Vue)
  → api/protonMetrics.js  (useProtonConfig → backend URL + key)
    → HTTP GET /metrics/{dashboard,anomalies,departments,callcenter,lifecycle}
      → proton backend metrics feature
        → MetricsQueryPort / query_adapter
          → BigQuery (Phase-3 views)   |  Tier-1 dashboard aggregate
  ← typed JSON row blocks
  → chart.js (vue-chartjs) + ReportMetricCard render
```

Fail-open: if the proton backend is unreachable, the tab shows an empty/error state
(as Knowledge views do) — it never breaks the rest of Reports.

## Implementation phasing

- **Phase A (Tier 1 — quick win):** `ProtonAnalytics.vue` (from `/metrics/dashboard`) +
  `ProtonAnomaly.vue` (from `/metrics/anomalies`) + export button (`/metrics/export`).
  Route + sidebar wiring. New chart wrapper components. FE-only; endpoints already exist.
- **Phase B (Tier 2):** the 3 new backend read endpoints (with `x-api-key`, config, tests),
  then `ProtonDepartments.vue`, `ProtonCallCentre.vue`, `ProtonCaseLifecycle.vue`.
- **Phase C (optional/deferred):** tag-keyword report filter; scheduled auto-send wiring.

## Testing

- **Backend:** pytest per new endpoint using the offline query-port stub (mirrors
  `test_phase3_smoke.py` / `test_dashboard_router.py`). Assert the JSON shape matches the
  view columns and that `x-api-key` gates access (401 without key).
- **Frontend:** vite build verification (0 errors) + live smoke against a running stack —
  the fork's established verification flow (as used for the Knowledge patches). Confirm each
  tab renders with real backend data and empty-states when the backend is down.

## Open items to validate during planning

1. Confirm each Phase-3 view carries the exact dimensions its tab needs. dealer/dept/PIC
   and tasks-per-agent are present; **agent-level NPS may need agent attribution added to
   the NPS collection path** (`features/chat/nps.py` / the `nps` block in `/metrics/dashboard`
   is by channel, not agent) — verify before building the Call-Centre KPI NPS chart.
2. Confirm `useProtonConfig` (runtime-config) exposes the backend URL + a place for the
   metrics-read API key to report views the same way it does for Knowledge views.
3. Confirm `/metrics/export` can be parameterised per report, or decide on per-endpoint
   export for Tier-2 tabs.

## Files touched (anticipated)

Backend (`backend/apps/backend/src/chatbot/`):
- `features/metrics/departments_router.py`, `callcenter_router.py`, `lifecycle_router.py` (new)
- `features/metrics/query_port.py` / `query_adapter.py` (add view-reading methods)
- `platform/config.py` (+ metrics-read API key), `main.py` (mount routers)
- co-located `test_*.py`

Frontend (Chatwoot fork, delivered as patches `0020+` in
`deploy/chatwoot-fork/patches/`):
- `dashboard/api/protonMetrics.js` (new)
- `dashboard/routes/dashboard/settings/reports/Proton{Analytics,Departments,CallCentre,CaseLifecycle,Anomaly}.vue` (new)
- `shared/components/charts/{LineChart,DoughnutChart}.vue` (new thin wrappers, if absent)
- `dashboard/routes/dashboard/settings/reports/reports.routes.js` (register)
- `dashboard/components-next/sidebar/Sidebar.vue` (nav children)

Config:
- `deploy/tenants/example.env` (metrics-read API key)
