# Bot-Metrics Dashboard — Phase 1 (Zendesk → BigQuery → Looker) — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorm complete)
**Scope:** Phase 1 of the cross-channel Bot-Metrics dashboard (the XLSMART-style board).
Ships the **derivable** metrics — Volume, Transfer-to-Agent vs Closed-by-Bot (+ composition),
and CSAT — by syncing existing Zendesk tickets into BigQuery and visualizing in Looker
Studio. Later phases add the instrumented signals (NPS, fallback, bounce, speed) and the
manual QA metrics (accuracy, quality).

## Context & North Star

The POC now serves five channels (web text, voice, WhatsApp, email, phone) converging on
Zendesk, with unified CSAT across channels. The north star is a cross-channel **Bot-Metrics
dashboard** (modeled on the XLSMART board: monthly volume, transfer-vs-closed, session
composition, CSAT/NPS trends, fallback/bounce/speed, accuracy/quality).

**Key realization:** every Phase-1 metric already lives in Zendesk — a conversation is a
ticket (channel from its `external_id` prefix), `open` vs `solved` is transfer-vs-closed, and
the `csat_N` tags (unified across all channels via `record_csat_on_ticket`) are CSAT. So
Phase 1 needs **no new app instrumentation** — just a **Zendesk → BigQuery sync** plus Looker
Studio on top. The per-event `MetricsPort` pipeline is deferred to Phase 2, where it earns its
keep capturing the signals Zendesk can't give us.

### Decisions locked during brainstorming

- **Architecture:** instrument-free for Phase 1 → **Zendesk → BigQuery sync** → **Looker Studio**.
- **"Live" = periodic sync:** run once = backfill (all history); re-run/schedule = keeps the
  dashboard current. Per-event streaming is not Phase 1 (dashboards are batch-refreshed).
- **Dataset:** `lv-playground-genai.demo_proton` (GCP project `lv-playground-genai`, same as
  Vertex/Firestore — ADC auth already works). No tables exist yet.
- **Looker deliverable boundary:** Looker Studio is interactive GCP config we cannot put in
  the repo. Our deliverable is the **BigQuery table + views + a written setup guide**; the
  user builds the Looker dashboard from the guide. "Done" for this spec = the sync populates
  the table + views and the guide exists.

## Architecture & Data Flow

```
Zendesk tickets (every conversation = a ticket; external_id = <channel>-<id>; status; csat_N tag)
   │  scripts/sync_zendesk_metrics.py
   │   1. pull tickets via the Incremental Export API (/api/v2/incremental/tickets, cursor-based)
   │   2. map each ticket → a conversation row  (PURE, unit-tested)
   │   3. load rows into BigQuery (WRITE_TRUNCATE full reload — ticket volume is small)
   ▼
BigQuery: lv-playground-genai.demo_proton.conversations   (one row per conversation)
   │  + views: v_volume_by_month_channel, v_resolution_split, v_csat
   ▼
Looker Studio dashboard  (built by the user from the setup guide; reads the views)
```

## Components

### 1. BigQuery table `demo_proton.conversations`
One row per ticket/conversation. Schema:

| column | type | source |
|---|---|---|
| `conversation_id` | STRING | ticket id |
| `channel` | STRING | from `external_id` prefix (see mapping) |
| `created_at` | TIMESTAMP | ticket `created_at` |
| `updated_at` | TIMESTAMP | ticket `updated_at` |
| `status` | STRING | ticket status (new/open/pending/hold/solved/closed) |
| `resolved_by` | STRING | `agent` if status in (new/open/pending/hold), else `bot` |
| `csat_score` | INT64 (nullable) | parsed from a `csat_<N>` tag, else NULL |
| `synced_at` | TIMESTAMP | sync run time |

### 2. Pure mapping function (the testable core)
`map_ticket_to_row(ticket: dict) -> ConversationRow | None`:
- **channel** — `external_id` prefix before the first `-`: `whatsapp`→`WhatsApp`,
  `email`→`Email`, `phone`→`Phone`, `sim`→`Web`, `zendesk`/`chatwoot`→`Web`. Unknown/missing
  external_id → `Other`.
- **resolved_by** — `agent` if status ∈ {new, open, pending, hold} (escalated / in a human
  queue), else `bot` (deflected/closed). *Limitation:* a handoff later solved by a human reads
  as `bot`; acceptable for the POC (most handoff tickets stay open in the sandbox). Noted; an
  accurate `handoff` tag is a later enhancement.
- **csat_score** — first tag matching `^csat_([1-5])$` → int, else NULL.
- Returns None to skip tickets that aren't conversations (no external_id and no csat tag),
  logged as skipped.

### 3. Sync script `apps/backend/scripts/sync_zendesk_metrics.py`
- Reads Zendesk via the **Incremental Ticket Export API** (`/api/v2/incremental/tickets.json?
  start_time=0`), paging on `after_cursor`/`end_of_stream`; the ticket objects include
  `external_id`, `status`, `tags`, `created_at`, `updated_at`.
- Maps each via `map_ticket_to_row`.
- Loads into BigQuery with a `WRITE_TRUNCATE` load job (atomic full reload; idempotent — run
  once for backfill, re-run any time to refresh). Creates the dataset/table if missing.
- Best-effort + structured logging; never prints secrets. Reads creds + BQ settings from
  `Settings`. Run manually: `PYTHONPATH=... .venv/bin/python scripts/sync_zendesk_metrics.py`.
  (Automation/scheduling — cron/Cloud Scheduler — is out of scope; noted for the user.)

### 4. BigQuery views (for Looker)
- `v_volume_by_month_channel` — `FORMAT_DATE('%Y-%m', created_at) AS month, channel, COUNT(*) AS volume`.
- `v_resolution_split` — per channel (+ overall): `COUNTIF(resolved_by='bot')` closed-by-bot,
  `COUNTIF(resolved_by='agent')` transfer-to-agent, and the two percentages (the donut +
  the transfer/closed lines).
- `v_csat` — per channel: `COUNTIF(csat_score IS NOT NULL)` respondents, `AVG(csat_score)`,
  and `SAFE_DIVIDE(COUNTIF(csat_score>=4), COUNTIF(csat_score IS NOT NULL))` as the
  satisfied-rate (CSAT% with 4–5 = satisfied).

### 5. Looker Studio setup guide `docs/dashboards/looker-bot-metrics-phase1.md`
Step-by-step: create the BQ data sources from the three views, then build the Phase-1 tiles —
**Monthly Volume Trend** (line/bar by month+channel), **Session Composition donut** +
**Transfer-vs-Closed %**, and **CSAT** (respondents + satisfied-rate). Mirrors the relevant
XLSMART tiles; the un-instrumented tiles (NPS, fallback, bounce, speed, accuracy/quality) are
called out as later phases.

## Configuration
| Setting | Default | Purpose |
|---|---|---|
| `bigquery_project_id` | `lv-playground-genai` | BQ project (ADC auth) |
| `bigquery_dataset` | `demo_proton` | the dataset |
| `bigquery_conversations_table` | `conversations` | the fact table |
| (reused) `zendesk_subdomain` / `zendesk_email` / `zendesk_api_token` | — | Incremental Export auth |

Add dependency `google-cloud-bigquery` (`uv add google-cloud-bigquery`).

## Testing
- **Unit (pytest):** `map_ticket_to_row` — channel from each prefix (`whatsapp-/email-/phone-/
  sim-/zendesk-conv-/chatwoot-conv-`/unknown); `resolved_by` from each status; `csat_score`
  from a `csat_4` tag / no tag / non-csat tags; skip when not a conversation. Plus the BQ
  view SQL strings are well-formed (a light assertion that the view DDL contains the expected
  aggregates) if the script generates them.
- **Live smoke (manual, documented):** run the sync against the real Zendesk + BQ; confirm the
  table populates and the three views return rows; then build the Looker tiles from the guide.
- Quality gates: `ruff format`, `ruff check`, `mypy --strict`, `pytest`.

## Out of Scope (later phases)
- The per-event `MetricsPort` + BigQuery streaming adapter; NPS survey, fallback rate, bounce
  rate, and speed-of-response instrumentation (Phase 2/3).
- Accuracy / Quality (manual QA entry path) — Phase 4.
- An accurate durable `handoff` tag for transfer attribution on already-solved tickets.
- Scheduling/automation of the sync (cron/Cloud Scheduler).
- Building the Looker dashboard itself (interactive GCP config — delivered as a guide).
- Browser-voice metrics if voice turns don't create Zendesk tickets.

## Risks / Unknowns
- **Incremental Export fields:** confirm the ticket objects include `external_id` + `tags`
  (they do in the standard ticket export); if `tags` needs sideloading, add `?include=`.
- **BQ dataset existence / permissions:** the script creates the table; the dataset
  `demo_proton` must exist (or the script creates it) and ADC must have BQ write on the project.
- **resolved_by approximation** (status-based) — documented; accurate only while handoff
  tickets remain unsolved.
