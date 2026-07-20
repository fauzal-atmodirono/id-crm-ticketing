# Looker Studio — Bot Metrics Dashboard (Phase 2)

> **Goal:** build the Phase 2 bot-metrics dashboard in Looker Studio backed by the
> three BigQuery views that accrue from live per-turn instrumentation in
> `lv-playground-genai.demo_proton`.
>
> **What is in Phase 2:** Speed of Response (initial and subsequent), Fallback Rate, 
> and Bounce Rate tiles, populated by the new `turn_events` table and three aggregation 
> views.
>
> **What is NOT in Phase 2:** NPS, Accuracy, and Quality are later phases.

---

## 0. Prerequisites

- [ ] The backend is deployed (or running locally) with `METRICS_PROVIDER=bigquery` so
      the `turn_events` table accrues one row per chat turn.
  - **Note:** `turn_events` is empty until live traffic arrives; there is NO historical 
    backfill. It populates only from turns processed after instrumentation is active.
- [ ] You have BigQuery Data Viewer (or higher) on the `lv-playground-genai` project,
      and access to [Looker Studio](https://lookerstudio.google.com) under the same
      Google account.
- [ ] (Optional) `METRICS_SYNC_ENABLED=true` and `METRICS_SYNC_INTERVAL_HOURS=6` 
      (or your preferred interval) keep the Phase-1 `conversations` table fresh 
      automatically while the backend runs. If `false` (default), the sync runs only 
      when you manually invoke `scripts/sync_zendesk_metrics.py`.

---

## 1. Deploy with per-turn instrumentation

From `apps/backend/`:

```bash
cd apps/backend
# Edit .env to set:
# METRICS_PROVIDER=bigquery
# BIGQUERY_TURN_EVENTS_TABLE=turn_events
# Then start the backend:
.venv/bin/uvicorn chatbot.main:app --reload
```

Once the backend is running, every chat turn across any channel (web, WhatsApp, email, 
phone) will stream one row into `lv-playground-genai.demo_proton.turn_events`.

---

## 2. Verify the views in BigQuery (recommended)

1. [ ] Open the [BigQuery console](https://console.cloud.google.com/bigquery) and
       navigate to `lv-playground-genai` → `demo_proton`.
2. [ ] Confirm the three views appear:
       - `v_speed_of_response`
       - `v_fallback_rate`
       - `v_bounce_rate`
3. [ ] (Optional) Run a quick preview on each view (click the view → **Preview**) 
       to confirm rows are returned. If `turn_events` is still empty, skip this — 
       proceed to the live smoke test below.

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
| `Proton — Speed of Response` | `v_speed_of_response` |
| `Proton — Fallback Rate` | `v_fallback_rate` |
| `Proton — Bounce Rate` | `v_bounce_rate` |

---

## 4. Build the Phase-2 tiles

### 4.1 Speed of Response (Initial)

**Tile type:** Scorecard

Initial response speed is the latency from customer message to AI's first reply on a 
new session.

1. [ ] Insert a **Scorecard** tile.
2. [ ] Set the data source to `Proton — Speed of Response`.
3. [ ] Add a filter: `is_first_turn = true`.
4. [ ] Configure the scorecard:

   | Field | Setting |
   |---|---|
   | Metric | `p99_latency_ms` (AVG, or MAX for the worst-case 99th percentile) |
   | Dimension (optional) | `channel` (to see per-channel breakdown; or omit for aggregate) |

5. [ ] Set the chart title to **Initial Response Speed (p99, ms)**.
6. [ ] Set number format to **Number** with 0 decimals and add a suffix ` ms`.

---

### 4.2 Speed of Response (Subsequent)

**Tile type:** Scorecard

Subsequent response speed is the latency from customer message to AI's reply on a turn 
that is NOT the first turn.

1. [ ] Duplicate the scorecard from §4.1.
2. [ ] Add a filter: `is_first_turn = false`.
3. [ ] Set the chart title to **Subsequent Response Speed (p99, ms)**.

---

### 4.3 Fallback Rate

**Tile type:** Scorecard or Bar chart (by channel)

1. [ ] Insert a **Scorecard** tile (or a **Bar chart** if you prefer per-channel breakdown).
2. [ ] Set the data source to `Proton — Fallback Rate`.
3. [ ] Configure:

   | Field | Setting |
   |---|---|
   | Metric | `fallback_rate` (AVG) |
   | Dimension (optional) | `channel` (for breakdown) |

4. [ ] Set the chart title to **Fallback Rate**.
5. [ ] If using a scorecard, set number format to **Percent** with 1 decimal.
   If using a bar chart, add `channel` as the dimension and `fallback_rate` as the metric.

---

### 4.4 Bounce Rate

**Tile type:** Scorecard or Bar chart (by channel)

1. [ ] Insert a **Scorecard** tile (or a **Bar chart** for per-channel).
2. [ ] Set the data source to `Proton — Bounce Rate`.
3. [ ] Configure:

   | Field | Setting |
   |---|---|
   | Metric | `bounce_rate` (AVG) |
   | Dimension (optional) | `channel` (for breakdown) |

4. [ ] Set the chart title to **Bounce Rate**.
5. [ ] If using a scorecard, set number format to **Percent** with 1 decimal.
   If using a bar chart, add `channel` as the dimension and `bounce_rate` as the metric.

---

## 5. Live smoke test

Before finalizing the dashboard, confirm the data pipeline is working:

1. [ ] Deploy the backend with `METRICS_PROVIDER=bigquery`.
2. [ ] Run a few test chat turns across different channels:
       - Web chat (simulator, e.g. `sim-test-001`)
       - WhatsApp (if configured)
       - Email (if configured)
       - Phone (if configured)
3. [ ] In the BigQuery console, run:
   ```sql
   SELECT COUNT(*) as row_count FROM `lv-playground-genai.demo_proton.turn_events`;
   ```
   Confirm rows > 0 (one per turn you sent).
4. [ ] Run a quick query on each view to confirm data:
   ```sql
   SELECT * FROM `lv-playground-genai.demo_proton.v_speed_of_response` LIMIT 5;
   SELECT * FROM `lv-playground-genai.demo_proton.v_fallback_rate` LIMIT 5;
   SELECT * FROM `lv-playground-genai.demo_proton.v_bounce_rate` LIMIT 5;
   ```
   Each should return rows with the channel, aggregates, and turn counts.
5. [ ] In Looker Studio, click **Refresh data** (circular arrow) to pull fresh BigQuery 
       results. The tiles should populate with non-zero values.

---

## 6. Sync scheduler (Phase 1 automation)

**Optional:** To keep the Phase-1 `conversations` table (volume, resolution, CSAT) fresh 
without manual runs:

1. [ ] Edit `apps/backend/.env`:
   ```bash
   METRICS_SYNC_ENABLED=true
   METRICS_SYNC_INTERVAL_HOURS=6
   ```
2. [ ] Restart the backend. The scheduler will refresh the `conversations` table and 
       its views (`v_volume_by_month_channel`, `v_resolution_split`, `v_csat`) every 
       6 hours.
3. [ ] To trigger a manual sync at any time, run:
   ```bash
   cd apps/backend
   .venv/bin/python scripts/sync_zendesk_metrics.py
   ```

---

## 7. Refresh the dashboard data

Looker Studio caches BigQuery results. Two ways to get fresh data:

1. **Process new turns** — run more chat sessions through the app; new rows arrive in 
   `turn_events` immediately.
2. **Force a cache refresh** in Looker Studio — click the **Refresh data** button 
   (circular arrow) in the top toolbar. This bypasses the cache and re-queries BigQuery 
   immediately.

To reduce stale-data risk, set a shorter cache TTL:

- In the data source editor → **Data freshness** → change from the default 12 h to
  **1 hour** (or **None** for live queries, at higher BigQuery cost).

---

## Quick reference — field glossary

| View | Field | Type | Meaning |
|---|---|---|---|
| `v_speed_of_response` | `channel` | Text | `web`, `whatsapp`, `email`, `phone` |
| `v_speed_of_response` | `is_first_turn` | Boolean | `true` = initial response; `false` = subsequent |
| `v_speed_of_response` | `p99_latency_ms` | Integer | 99th percentile latency (milliseconds) |
| `v_speed_of_response` | `avg_latency_ms` | Float | Average latency (milliseconds) |
| `v_speed_of_response` | `turns` | Integer | Count of turns in the aggregation |
| `v_fallback_rate` | `channel` | Text | `web`, `whatsapp`, `email`, `phone` |
| `v_fallback_rate` | `fallback_rate` | Float [0–1] | Fraction of turns that triggered fallback |
| `v_fallback_rate` | `turns` | Integer | Total turns sampled |
| `v_bounce_rate` | `channel` | Text | `web`, `whatsapp`, `email`, `phone` |
| `v_bounce_rate` | `bounced` | Integer | Sessions with only one turn (no follow-up) |
| `v_bounce_rate` | `total_sessions` | Integer | Total sessions |
| `v_bounce_rate` | `bounce_rate` | Float [0–1] | Fraction of single-turn sessions |

---

*Backend per-turn instrumentation: `apps/backend/src/chatbot/features/metrics/`.
BigQuery schema + view DDL: `apps/backend/src/chatbot/features/metrics/turn_schema.py`.
Phase 1 sync script: `apps/backend/scripts/sync_zendesk_metrics.py`.*
