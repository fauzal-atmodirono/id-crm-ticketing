# Rebuilding the Power BI artefact

> **Status (2026-08-09):** the `.pbix` itself does **not** exist in this repo and
> cannot be produced here — it is a proprietary binary only Power BI Desktop can
> author. What ships instead is `proton-crm.pbids` (the connection, in
> version-controllable JSON) plus the page-by-page spec below, which is the part
> that can be reviewed and diffed.
>
> §4.55 was raised by the client at the 2026-07-28 demo (feedback item 5).
> **A claim that it is done needs evidence attached**: the file opened, every
> page rendering against a real dataset, refresh succeeding under the service
> account, and a screenshot of each page pasted into this runbook. Until those
> screenshots are here, report it as in progress.

## Quick start

Open `proton-crm.pbids` in Power BI Desktop after replacing its two
`REPLACE_WITH_` placeholders. It opens the Navigator already pointed at the
tenant's dataset in **DirectQuery** mode.

**Use DirectQuery, not Import.** Import copies data into the `.pbix`, so the
report serves whatever was true at the last refresh — which is the staleness the
client raised in the first place.

## The five cuts §4.55 names, and the views behind each

| Page | Views | Notes |
|---|---|---|
| **By channel** | `v_volume_by_month_channel`, `v_resolution_split`, `v_first_response_by_channel` | |
| **By division** | `v_volume_by_division`, `v_volume_by_type_division`, `v_concern_pivot` | `v_concern_pivot` uses ROLLUP — filter to the grain you want or subtotal rows double-count. |
| **Trend** | `v_volume_daily`, `v_volume_weekly`, `v_case_state_trend`, `v_volume_after_hours` | `v_case_state_trend` is the real case state; `v_state_trend` is Chatwoot's `status`. They are different questions. |
| **By PIC** | `v_dept_pic_performance`, `v_tasks_per_agent`, `v_nps_by_agent` | |
| **CRR / dealer** | `v_dealer_escalation`, `v_first_response_by_dealer`, `v_dealer_escalation_slowest_cases` | **`v_dealer_escalation` keys on `dealer_escalated_at`, not `created_at`** — its monthly total deliberately does not sum to that month's case count. |
| **SLA** | `v_sla_achievement`, `v_resolution_sla_buckets`, `v_case_aging` | |

### Three things that will otherwise be read wrong

1. **`v_volume_by_tag` double-counts by construction.** A case with three
   labels is in three buckets, and a case with no labels is absent. Do not put
   a total on that page.
2. **Every `day` column is bucketed in `REPORTING_TIMEZONE`** (UTC by default).
   If the deck is compiled in MYT, see the timezone section of the main README
   before assuming a discrepancy is a bug.
3. **Blank columns mean "not yet captured", not "zero"** — most P3 case fields
   are agent-entered. `/metrics/*` responses carry a coverage note; put it on
   the slide.

---

# Appendix: the original connection runbook

**Purpose:** Connect Power BI Desktop to the existing BigQuery `conversations` dataset/views,
build a report, publish it to Power BI Service, and wire the embed URL into the Reports nav item.

**Prerequisites:**
- Power BI Desktop installed (free, Windows/macOS via Parallels or use Power BI web for macOS)
- Power BI Pro or Premium Per User license (for publishing + embed)
- Google Cloud service account JSON key with `roles/bigquery.dataViewer` on the dataset
- BigQuery project id + dataset name (from `settings.bigquery_project_id` / `settings.bigquery_dataset`)
- Phase-0 nav menu deployed (the Reports item at `PROTON_BACKEND_URL/apps/reports` must exist)

---

## Part 1 — Connect Power BI Desktop to BigQuery

### Step 1: Install the BigQuery connector

1. Open Power BI Desktop → **Get Data** (Home ribbon) → **More…**
2. Search for **Google BigQuery** → select it → **Connect**.
3. On first use, you may be prompted to install the connector. Accept and restart Power BI Desktop.

### Step 2: Authenticate with a service account

1. In the BigQuery connector dialog, select **Sign in**.
2. Choose **Service Account** authentication.
3. Upload the JSON key file for the service account that has `roles/bigquery.dataViewer` on
   the dataset. The service account must NOT have write permissions (least privilege).
4. Click **Connect**.

### Step 3: Navigate to the dataset

1. In the Navigator, expand: `<bigquery_project_id>` → `<bigquery_dataset>`.
2. Select the following views (tick the checkboxes):

   | View | Purpose |
   |---|---|
   | `v_volume_by_month_channel` | Monthly volume by channel |
   | `v_volume_daily` | Daily trend |
   | `v_volume_weekly` | Weekly trend |
   | `v_volume_by_division` | Division breakdown |
   | `v_resolution_split` | Bot vs agent closure |
   | `v_resolution_time` | Resolution time p50/p90 |
   | `v_dept_pic_performance` | Dept / PIC case counts + response |
   | `v_sla_achievement` | SLA achievement rate |
   | `v_reopen_rate` | CRR by dealer / dept / PIC |
   | `v_nps` | NPS by channel |
   | `v_nps_by_agent` | NPS by agent |
   | `v_csat` | CSAT by channel |
   | `v_channel_anomaly` | Volume anomaly baseline |
   | `v_peak_hours` | Peak complaint hours heatmap |
   | `v_complaint_type_ranking` | Complaint-type ranking |
   | `v_tasks_per_agent` | Tasks per agent + avg response |
   | `v_first_response_by_channel` | First-response time by channel |
   | `v_case_lifecycle` | Case lifecycle rows |
   | `v_state_trend` | Status trend by month |

3. Click **Load** (not Transform — the views are already clean).

### Step 4: Set DirectQuery mode

In the connection dialog, select **DirectQuery** (not Import). This ensures dashboards always
reflect the latest sync without a manual refresh — the BQ sync job already runs on a schedule.

---

## Part 2 — Build the report

### Recommended page layout

| Page | Visuals | Primary view(s) |
|---|---|---|
| **Overview** | Volume line (monthly), channel donut, division bar | `v_volume_by_month_channel`, `v_volume_by_division` |
| **Agent Performance** | Table (dept/PIC), bar (avg response), bar (tasks/agent) | `v_dept_pic_performance`, `v_tasks_per_agent` |
| **SLA & Quality** | KPI (SLA rate), gauge (CSAT), NPS score | `v_sla_achievement`, `v_csat`, `v_nps` |
| **CRR** | Bar chart by dealer, by dept, by PIC | `v_reopen_rate` |
| **Complaint Types** | Bar ranking (category), heatmap (peak hours) | `v_complaint_type_ranking`, `v_peak_hours` |
| **Response Time** | Line (first-response by channel), scatter lifecycle | `v_first_response_by_channel`, `v_case_lifecycle` |
| **Trends** | Line state trend, anomaly table | `v_state_trend`, `v_channel_anomaly` |

### Slicers to add on every page

- Month slicer (from `v_volume_by_month_channel.month`)
- Channel slicer (from any view `.channel`)
- Division slicer (from `v_volume_by_division.division`)

---

## Part 3 — Publish to Power BI Service

### Step 5: Save and publish

1. **File** → **Save** → name the file `proton-crm-reporting.pbix`.
2. **Home** → **Publish** → select your Power BI workspace (e.g. `Proton CRM`).
3. Wait for upload to complete. Power BI will open a browser link to the report.

### Step 6: Configure scheduled refresh (optional for DirectQuery)

DirectQuery reports do not need a refresh schedule — queries run live against BigQuery on
each page load. Skip this step if you used DirectQuery in Step 4.

If you used Import mode instead:
1. In Power BI Service, go to the **Datasets** section → find `proton-crm-reporting`.
2. **Settings** → **Scheduled refresh** → Add credentials (service account JSON) → set
   refresh frequency to match the BQ sync interval (default: 4 hours).

### Step 7: Get the embed URL

1. In Power BI Service, open the published report.
2. **File** → **Embed report** → **Website or portal**.
3. Copy the **Secure Embed URL** (format: `https://app.powerbi.com/reportEmbed?reportId=<uuid>&autoAuth=true&ctid=<tenant>`).
4. Keep this URL — it goes into the `REPORTS_EMBED_URL` env var in the next step.

---

## Part 4 — Wire into the Reports nav item (Phase-0 iframe host)

### Step 8: Set `REPORTS_EMBED_URL` per tenant

In `id-crm-ticketing/tenants/<tenant>.env` (e.g. `proton.env`), add:

```env
# Power BI embed URL for the Reports nav item (from Power BI Service → Embed → Website)
REPORTS_EMBED_URL=https://app.powerbi.com/reportEmbed?reportId=<uuid>&autoAuth=true&ctid=<tenant>
```

The Phase-0 iframe host route at `PROTON_BACKEND_URL/apps/reports` reads this env var and
renders `<iframe src="${REPORTS_EMBED_URL}" ... />`. The Chatwoot nav item for **Reports**
already points at that route via `PROTON_BACKEND_URL`.

### Step 9: Verify the embed

1. Open the Chatwoot UI for the tenant.
2. Click **Reports** in the left nav.
3. The Power BI report iframe should load within ~3 s.
4. Confirm slicers and all 7 pages are navigable inside the iframe.

If the iframe shows a blank page or an error, check:
- `REPORTS_EMBED_URL` is set correctly in the tenant env.
- The Power BI workspace allows embedding (Admin Portal → Tenant settings → **Embed codes**).
- The Chatwoot origin is listed in the Power BI allowed domains if org policies restrict embedding.

---

## Looker Studio alternative

If Power BI licensing is not available, the same BigQuery views work with **Looker Studio**
(free, browser-based). Connect via **BigQuery** connector → same view list → build charts.
Share the Looker Studio report URL as `REPORTS_EMBED_URL`. Looker Studio reports embed
cleanly in iframes without additional licensing.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Access denied" on BQ connector | Service account lacks `bigquery.dataViewer` | Grant role in IAM console |
| View returns 0 rows | Sync job hasn't run yet | Trigger `POST /metrics/sync` or wait for scheduler |
| `v_reopen_rate` shows all `reopen_count = NULL` | No integration writing `additional_attributes.reopen_count` | Verify the write-back payload includes the field; see mapping.py `_chatwoot_reopen_count` |
| `dealer` column shows `Unknown` everywhere | No `dealer_<slug>` labels on conversations | Ensure ChatwootAdapter writes `dealer_<slug>` labels at classification time |
| iframe shows blank in Chatwoot | `REPORTS_EMBED_URL` not set or misconfigured | Check tenant `.env` and restart container |
