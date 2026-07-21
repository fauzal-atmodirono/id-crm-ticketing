# Merge Proton Reports into Native Chatwoot Reports — Plan

> Follow-up to `2026-07-21-reports-proton-analytics-merge.md`. Backend `/metrics/*`
> endpoints are UNCHANGED (already deployed + working). This is a frontend re-org:
> native reports become the home; Proton/BigQuery data is surfaced as a
> self-contained SECTION appended inside the matching native report view.

**Decision (user, 2026-07-21):** Full merge now. Augment native SLA / CSAT / Bot /
Agents tabs with Proton sections; keep Anomaly / Case Lifecycle / Departments & PIC
as the only remaining standalone Proton tabs; retire the standalone Analytics +
Call-Centre KPI tabs.

## Technique
Each Proton addition is a **self-contained `<script setup>` section component** that
fetches its own `/metrics/*` data (via `dashboard/api/protonMetrics.js`), guards
loading/error/empty, and renders with `ReportMetricCard` + `BarChart`/`DoughnutChart`.
The native view change is minimal: `import` it + drop one `<ProtonXSection />` tag in
the template (after the native content). Bare English strings + `--no-verify` + eslint
`--fix` per the established fork-view convention. Sections live in
`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/`.

## Mapping (all 15 backend data blocks placed)

| Native view (file) | Proton section | Data source (block) |
|---|---|---|
| SLA (`SLAReports.vue`) | `ProtonSlaSection.vue` | `getCallCentre().sla` |
| CSAT (`CsatResponses.vue`) | `ProtonCsatSection.vue` | `getDashboard().csat,nps` + `getCallCentre().nps_by_agent` |
| Bot (`BotReports.vue`) | `ProtonBotSection.vue` | `getDashboard().{volume,resolution,speed,fallback,bounce,quality}` + `getCallCentre().{first_response,resolution_time,peak_hours}` |
| Agents (`AgentReportsIndex.vue`) | `ProtonAgentsSection.vue` | `getCallCentre().tasks_per_agent` |
| Departments & PIC (kept Proton tab, `ProtonDepartments.vue`) | + complaint-type ranking | `getCallCentre().complaint_types` |

Kept standalone Proton tabs (no native equivalent): **Anomaly**, **Case Lifecycle**,
**Departments & PIC**.

Retired standalone tabs: **Analytics** (`ProtonAnalytics.vue`), **Call-Centre KPI**
(`ProtonCallCentre.vue`) — delete the files, remove their routes in `reports.routes.js`
and their sidebar entries in `Sidebar.vue`. (Their data-shaping/chart logic is lifted
into the section components.)

## Tasks (SDD, clone branch `proton-reports-dev`)
- M1 `ProtonSlaSection.vue` + patch `SLAReports.vue`.
- M2 `ProtonCsatSection.vue` + patch `CsatResponses.vue`.
- M3 `ProtonBotSection.vue` + patch `BotReports.vue`.
- M4 `ProtonAgentsSection.vue` + patch `AgentReportsIndex.vue`.
- M5 Add complaint-type ranking section to `ProtonDepartments.vue`.
- M6 Retire Analytics + Call-Centre KPI tabs (delete files + routes + sidebar entries).
- M7 Re-export frontend patches, rebuild image, redeploy, verify in-browser.

Per-view flow: author → `npx eslint --fix` → `pnpm exec vite build` (✓) →
`git commit --no-verify`. Each section reuses computeds from the existing
`ProtonAnalytics.vue`/`ProtonCallCentre.vue` (which M6 deletes — lift before deleting).

## Verification
Build clean per task; final rebuild of `proton-chatwoot:v4.15.1-custom` + recreate
rails; in-browser: native SLA/CSAT/Bot/Agents each show their Proton section; the
Analytics/Call-Centre tabs are gone; Anomaly/Case Lifecycle/Departments remain.
Backend untouched — endpoints already smoke-verified.
