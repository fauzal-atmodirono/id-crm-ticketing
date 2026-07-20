# In-App Vue Metrics Dashboard

The embedded `/dashboard` route provides real-time channel-level Bot-Metrics aggregates directly in the web UI, rendered via 8 metric tiles and KPI cards.

## Route & Endpoint

- **Frontend route:** `/dashboard` (Vue SPA)
- **Backend endpoint:** `GET /metrics/dashboard`

## Configuration

The dashboard reads from BigQuery views when `metrics_provider=bigquery` is set in the backend environment, and returns mock data when `metrics_provider=noop` (default).

**Supported values:**
- `metrics_provider=noop` — Returns `MockMetricsQuery` with representative sample data; no BigQuery access required. Default.
- `metrics_provider=bigquery` — Queries the 8 Bot-Metrics BigQuery views (see below). Requires `BIGQUERY_PROJECT_ID`, `BIGQUERY_DATASET`, and associated credentials (ADC or explicit key).

## Data

The endpoint returns a `DashboardMetrics` payload with 8 metric arrays, one per channel:

1. **Volume** — conversation counts by month and channel
2. **Resolution** — bot-closed vs. transferred conversations per channel
3. **CSAT** — Customer Satisfaction (avg score, satisfaction rate) per channel
4. **NPS** — Net Promoter Score (promoters, passives, detractors, NPS metric) per channel
5. **Speed** — conversation latency (first-turn vs. follow-up, p99 and average) per channel
6. **Fallback** — fallback-request rate per channel
7. **Bounce** — session bounce rate per channel
8. **Quality** — QA accuracy and quality scores (requires `POST /qa/label` entry) per channel

Each metric is aggregated by `channel` (e.g., "web", "whatsapp"); volume is further split by month (YYYY-MM).

## Authentication & Scope

**The endpoint is unauthenticated by design (POC).** Only channel-level aggregates are exposed; no PII, message content, or customer-specific data is included. Restrict network access or deploy behind an auth proxy if desired.

## Frontend Components

The dashboard renders 8 tiles in a responsive grid:

- **VolumeChart** — monthly conversation volume by channel
- **ResolutionChart** — bot-closed vs. transferred split
- **CsatGauge** — average CSAT score
- **NpsTile** — NPS metric and buckets
- **SpeedChart** — p99 latency (first-turn vs. follow-up)
- **RateChart** (Fallback) — fallback rate per channel
- **RateChart** (Bounce) — bounce rate per channel
- **QualityChart** — accuracy and quality averages

All tiles support a **client-side channel filter** (via `useDashboardStore`) to drill into a single channel or view aggregate.

## Deferred Items

- **Date-range picker** — Currently fixed to month-level volume; custom date range filtering is not yet implemented.
- **Auth gate** — The endpoint is open for POC; production deployment should add X-API-Key or OAuth2.
- **Drill-down** — Clicking a metric card does not yet link to detailed historical views or per-conversation details.
