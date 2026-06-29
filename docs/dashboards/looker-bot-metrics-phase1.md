# Looker Studio — Bot Metrics Dashboard (Phase 1)

> **Goal:** build the Phase 1 bot-metrics dashboard in Looker Studio backed by the
> three BigQuery views that the Zendesk sync populates in `lv-playground-genai.demo_proton`.
> The five tiles map directly to the XLSMART board scope for Phase 1.
>
> **What is NOT in Phase 1:** NPS, Fallback Rate, Bounce Rate, Speed of Response,
> Accuracy, and Quality tiles are intentionally greyed out — they require Phase 2/3/4
> instrumentation and will be wired in subsequent releases.

---

## 0. Prerequisites

- [ ] The Zendesk sync has been run at least once so the table and views exist in
      BigQuery project `lv-playground-genai`, dataset `demo_proton`:
  - `conversations` (base table)
  - `v_volume_by_month_channel`
  - `v_resolution_split`
  - `v_csat`
- [ ] You have **BigQuery Data Viewer** (or higher) on the `lv-playground-genai` project,
      and access to [Looker Studio](https://lookerstudio.google.com) under the same
      Google account.
- [ ] The backend `.env` has `BIGQUERY_PROJECT_ID=lv-playground-genai` and
      `BIGQUERY_DATASET=demo_proton` set correctly.

---

## 1. Run (or refresh) the sync

From `apps/backend/`:

```bash
cd apps/backend
.venv/bin/python scripts/sync_zendesk_metrics.py
```

What it does: performs a **full reload (`WRITE_TRUNCATE`)** of **all** tickets (open
and solved, all channels) into `conversations`, then materialises the three views via
`CREATE OR REPLACE VIEW`. The sync pulls ALL history (`start_time=0`); there are no
flags. Re-run this command any time you want fresher data.

Expected terminal output (no errors):

```
synced: <N> tickets -> <M> rows into lv-playground-genai.demo_proton.conversations; views refreshed
```

---

## 2. Verify the views in BigQuery (optional but recommended)

1. [ ] Open the [BigQuery console](https://console.cloud.google.com/bigquery) and
       navigate to `lv-playground-genai` → `demo_proton`.
2. [ ] Confirm the three views appear alongside the `conversations` table.
3. [ ] Run a quick preview on each view (click the view → **Preview**) and confirm rows
       are returned.

---

## 3. Create BigQuery data sources in Looker Studio

You need one data source per view. Repeat these steps three times.

1. [ ] Go to [Looker Studio](https://lookerstudio.google.com) → **Create** → **Report**.
2. [ ] In the "Add data to report" panel select **BigQuery**.
3. [ ] Authenticate with the Google account that has access to `lv-playground-genai`.
4. [ ] Choose **My Projects** → `lv-playground-genai` → `demo_proton` → select the
       view (see table below) → click **Add**.
5. [ ] Repeat for the other two views by clicking **Add data** in the report toolbar.

| Data source name (suggested) | BigQuery view |
|---|---|
| `Proton — Volume by Month/Channel` | `v_volume_by_month_channel` |
| `Proton — Resolution Split` | `v_resolution_split` |
| `Proton — CSAT` | `v_csat` |

> Looker Studio will auto-detect field types. Verify that `month` is recognised as
> **Text** (not Date) since the view stores it as `YYYY-MM` string. If Looker treats
> it as a date, go to the data source editor and set its type to **Text**.

---

## 4. Build the Phase-1 tiles

### 4.1 Monthly Volume Trend

**Tile type:** Time series chart or Grouped bar chart

1. [ ] Insert a **Time series** chart (or **Bar chart** for a cleaner grouped view).
2. [ ] Set the data source to `Proton — Volume by Month/Channel`.
3. [ ] Configure the chart:

   | Field | Setting |
   |---|---|
   | Dimension | `month` |
   | Breakdown dimension | `channel` |
   | Metric | `volume` (SUM — already aggregated in the view) |

4. [ ] Set the chart title to **Monthly Volume Trend**.
5. [ ] In **Style**, enable **Show data labels** if you want per-bar numbers.

---

### 4.2 Session Composition

**Tile type:** Donut chart + two Scorecards

#### 4.2a — Donut chart

1. [ ] Insert a **Donut chart**.
2. [ ] Set the data source to `Proton — Resolution Split`.
3. [ ] Configure the chart:

   | Field | Setting |
   |---|---|
   | Dimension | `channel` *(or remove dimension to see totals across all channels)* |
   | Metric 1 | `closed_by_bot` (SUM) |
   | Metric 2 | `transfer_to_agent` (SUM) |

4. [ ] In **Style** → **Pie labels**, show both slice label and value.
5. [ ] Set the chart title to **Session Composition**.

> To show the donut collapsed across all channels (single ring), remove the Dimension
> field and add `closed_by_bot` and `transfer_to_agent` as two separate metrics.
> Looker will render them as two donut slices.

#### 4.2b — Scorecards (percentage)

1. [ ] Insert a **Scorecard** tile.
2. [ ] Data source: `Proton — Resolution Split`.
3. [ ] Metric: `closed_by_bot_pct` — set number format to **Percent** with 1 decimal.
4. [ ] Label: **Bot Resolution Rate**.
5. [ ] Duplicate the scorecard, change Metric to `transfer_to_agent_pct`,
       label to **Agent Transfer Rate**.

---

### 4.3 CSAT

**Tile type:** Two Scorecards (+ optional bar chart by channel)

#### 4.3a — Satisfaction rate scorecard

1. [ ] Insert a **Scorecard** tile.
2. [ ] Data source: `Proton — CSAT`.
3. [ ] Metric: `satisfied_rate` (average across rows, or SUM if only one row).
       Set number format to **Percent** with 1 decimal.
4. [ ] Label: **CSAT Satisfied Rate (≥4/5)**.

#### 4.3b — Respondents scorecard

1. [ ] Insert another **Scorecard**.
2. [ ] Data source: `Proton — CSAT`.
3. [ ] Metric: `respondents` (SUM).
4. [ ] Label: **CSAT Respondents**.

#### 4.3c — Optional breakdown by channel

1. [ ] Insert a **Bar chart** or **Table**.
2. [ ] Data source: `Proton — CSAT`.
3. [ ] Dimension: `channel`; Metrics: `satisfied_rate`, `respondents`, `avg_score`.
4. [ ] Title: **CSAT by Channel**.

---

## 5. Tiles NOT in Phase 1 (greyed out)

The following tiles are placeholders on the XLSMART board. **Do not build them yet** —
the underlying instrumentation arrives in later phases:

| Tile | Phase |
|---|---|
| NPS (Net Promoter Score) | Phase 3 — requires NPS survey flow |
| Fallback Rate | Phase 2 — requires intent-classification logging |
| Bounce Rate | Phase 2 — requires session-start / no-response logging |
| Speed of Response | Phase 2 — requires `first_response_at` timestamp on conversations |
| Accuracy | Phase 4 — requires human-review annotation pipeline |
| Quality | Phase 4 — requires QA scoring model |

Add these as **greyed-out text tiles** in Looker Studio with the label
`"Coming in Phase N"` so the board communicates the roadmap without creating broken
charts.

---

## 6. Refresh the dashboard data

Looker Studio caches BigQuery results. Two ways to get fresh data:

1. **Re-run the sync** (see §1) — this writes new rows to BigQuery. Looker will pick
   them up on the next cache expiry.
2. **Force a cache refresh** in Looker Studio — click the **Refresh data** button
   (circular arrow) in the top toolbar. This bypasses the cache and re-queries BigQuery
   immediately.

To reduce stale-data risk set a shorter cache TTL:

- In the data source editor → **Data freshness** → change from the default 12 h to
  **1 hour** (or **None** for live queries, at higher BigQuery cost).

---

## Quick reference — field glossary

| View | Field | Type | Meaning |
|---|---|---|---|
| `v_volume_by_month_channel` | `month` | Text (`YYYY-MM`) | Calendar month |
| `v_volume_by_month_channel` | `channel` | Text | `WhatsApp`, `Web`, `Email`, `Phone` |
| `v_volume_by_month_channel` | `volume` | Integer | Conversation count |
| `v_resolution_split` | `closed_by_bot` | Integer | Conversations auto-resolved by bot |
| `v_resolution_split` | `transfer_to_agent` | Integer | Conversations handed off to a human |
| `v_resolution_split` | `total` | Integer | All resolved conversations |
| `v_resolution_split` | `closed_by_bot_pct` | Float [0–1] | Bot resolution rate |
| `v_resolution_split` | `transfer_to_agent_pct` | Float [0–1] | Agent transfer rate |
| `v_csat` | `respondents` | Integer | Conversations with a CSAT score |
| `v_csat` | `avg_score` | Float | Mean score (1–5) |
| `v_csat` | `satisfied_rate` | Float [0–1] | Fraction with score ≥ 4 |

---

*Backend sync script: `apps/backend/scripts/sync_zendesk_metrics.py`.
BigQuery schema + view DDL: `apps/backend/src/chatbot/features/metrics/bigquery_schema.py`.*
