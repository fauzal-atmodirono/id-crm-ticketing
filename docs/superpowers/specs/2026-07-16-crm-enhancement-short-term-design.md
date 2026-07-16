# CRM Enhancement — Short-Term Block Design

**Status:** Draft for review
**Date:** 2026-07-16
**Source requirement:** `docs/CRM System Enhancement 260414.pdf` (BRD — CRM System Enhancement)
**Gap analysis:** `docs/CRM-Enhancement-Capability-Comparison.md`
**Scope:** The five "short term (2–6 wks, no external dependency)" roadmap items, designed as **one combined spec**.

## Framing decisions (approved 2026-07-16)

- **Combined spec** for all 5 short-term items; implementation staged in dependency order (#1 → #2 → #3 → #4 → #5).
- **Agent surface = Zendesk-native.** No custom agent console. Each item splits into **in-repo code** (behind ports, tested) and **Zendesk admin configuration** (documented, not code).
- **SLA timers run in Zendesk** (SLA policies + time-based Automations); our backend owns the audit trail, WIP state, and optional WA-to-PIC alert via a webhook. We do **not** build a custom in-app timer/scheduler for SLA.
- **Testability-first** (per `.agents/rules/architectural-pattern.md`): all new I/O behind ports with mock adapters; pure logic unit-tested without I/O.

## In-repo code vs. Zendesk configuration

| # | Item | In-repo code | Zendesk config (documented) |
|---|---|---|---|
| 1 | Metrics dimensions | Schema, mapping, sync, backfill, new views | — |
| 2 | Export + anomaly | Export module, scheduled email, anomaly views/endpoint | Recipient list |
| 3 | SLA escalation + audit | `AuditLogPort` + store, WIP state, escalation webhook, optional WA-to-PIC | SLA policies + 8h/48h Automations |
| 4 | Routing enablement | Enrich ticket payload with structured fields/tags | Agent Workspace, omnichannel routing, views, desktop/sound notifications |
| 5 | Agent-assist FAQ | `GET /kb/suggest`, `POST /kb/feedback`, `v_faq_quality` view | Thin Zendesk sidebar app (ZAF) |

---

## Item 1 — Metrics dimensions

**Goal:** land the dimensions the BRD's block-F reports need, so division / dept-PIC / SLA / CRR / agent-NPS reporting becomes possible. The AI already produces category/priority/SLA; we persist it structurally.

### Data model — `conversations` table, new columns

Add to `features/metrics/bigquery_schema.py` (`conversations` schema, currently 8 columns):

| Column | Type | Source |
|---|---|---|
| `division` | STRING | classification tag `division_<x>` |
| `category` | STRING | classification tag `category_<x>` |
| `subcategory` | STRING | classification tag `subcat_<x>` |
| `agent_id` | STRING | Zendesk ticket `assignee_id` |
| `pic` | STRING | classification tag `pic_<x>` (or assignee) |
| `department` | STRING | classification tag `dept_<x>` |
| `sla_minutes` | INT64 | classification (`classify_ticket_tool`) |
| `sla_deadline` | TIMESTAMP | `created_at + sla_minutes` |
| `first_response_at` | TIMESTAMP | Zendesk ticket-metrics API |
| `resolved_at` | TIMESTAMP | Zendesk ticket-metrics API (`solved_at`) |
| `reopen_count` | INT64 | Zendesk ticket-metrics API (`reopens`) |

All new columns are NULLABLE (backfill-safe; older rows stay valid).

### Write side

- `features/chat/agents.py` `classify_ticket_tool` already captures category/subcategory/priority/sla_minutes into session state. **No change to the tool signature.**
- `features/chat/adapters/zendesk.py` `create_ticket`: emit structured tags `division_<x>`, `category_<x>`, `subcat_<x>`, `dept_<x>`, `pic_<x>` alongside the existing priority mapping (`zendesk.py:91`). Division derives from category via a small static map (see Open Questions → resolved: `mapping.CATEGORY_TO_DIVISION`).

### Sync side

- `features/metrics/mapping.py`: add regex parsers for the new tags (mirroring the existing `csat_[1-5]` / `nps_[0-10]` parsers). Add a `CATEGORY_TO_DIVISION` dict mapping category → {Apps, Sales, Aftersales, Charging}.
- `scripts/sync_zendesk_metrics.py`: for each ticket, read `assignee_id` and call the Zendesk **ticket-metrics API** (`/api/v2/tickets/{id}/metrics`) for `solved_at`, first-reply time, and `reopens`; populate the new columns.

### New BigQuery views (Phase-F reports)

Add to `bigquery_schema.py` (or a new `report_views.py` included by the schema bootstrap):

- `v_volume_by_division` — volume by division + month.
- `v_dept_pic_performance` — per department/PIC: case count, avg first-response, avg resolution time, resolution rate; rank-ordered.
- `v_sla_achievement` — % of cases where `resolved_at <= sla_deadline`, by channel/division.
- `v_reopen_rate` — CRR = cases with `reopen_count > 0` / total, by dealer/dept/PIC.
- `v_resolution_time` — creation→close distribution (avg/p50/p90), by channel/division.
- `v_nps_by_agent` — NPS split (promoters/passives/detractors) grouped by `agent_id` (Call & WA channels).
- `v_volume_daily` / `v_volume_weekly` — daily/weekly rollups of volume by channel (existing view is monthly only).

### Backfill

- `scripts/seed_demo_metrics.py`: generate the new fields (realistic division/agent/PIC/SLA/reopen distributions) so views render on demo data.
- One-off backfill: a `--backfill` flag on `sync_zendesk_metrics.py` that walks existing tickets and populates the new columns from tags + ticket-metrics.

### Tests

- `test_mapping.py`: tag-parse for each new dimension; `CATEGORY_TO_DIVISION` coverage; null/absent-tag handling.
- View correctness validated against seeded demo data (row-count + spot-value assertions).

---

## Item 2 — Export + anomaly

**Goal:** PDF/Excel export of the dashboard, scheduled delivery to management, and a real-time channel-spike anomaly signal. Closes the remaining block-F line items.

### Export

- New `features/metrics/export.py`: pure functions that take the dashboard aggregate DTOs and render:
  - **Excel** via `openpyxl` (one sheet per report block).
  - **PDF** via `reportlab` (title, KPI summary table, per-report tables).
- Endpoint `GET /metrics/export?format=xlsx|pdf&range=<7d|30d|mtd>` in the metrics router → returns a streaming file response.

### Scheduled delivery

- `EmailReportPort` (new) in the metrics feature: `send_report(recipients, subject, body, attachments)`.
  - Real adapter: SMTP (config in `platform/config.py`: `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `report_recipients`).
  - Mock adapter: records calls for tests.
- Extend `features/metrics/scheduler.py` with a cron job (configurable cadence, default weekly) that renders the report and calls `EmailReportPort.send_report`.

### Anomaly

- View `v_channel_anomaly`: per channel, compare current window volume to a trailing baseline (mean + stddev over prior N periods); flag when `current > mean + k·stddev` **or** `% jump > threshold` (k and threshold in config).
- Endpoint `GET /metrics/anomalies` → current flagged anomalies.
- Scheduler: on each sync, evaluate anomalies; if any breach, send an alert email via `EmailReportPort`.

### Tests

- `test_export.py`: rendering produces non-empty xlsx/pdf bytes with expected sheet/section names (pure, no I/O).
- `test_anomaly.py`: baseline math flags a synthetic spike, ignores normal variation.
- `EmailReportPort` mock asserts scheduled job composes correct recipients/attachments.

---

## Item 3 — SLA escalation + audit trail

**Goal:** structured case audit trail, explicit WIP state, and Zendesk-driven 8h/48h escalation with an optional WhatsApp alert to the PIC.

### Audit log

- **`AuditLogPort`** (new, `features/chat/ports.py`): `append(event)` and `list_for_ticket(ticket_id)`.
  - Event: `{ticket_id, session_id, actor, from_state, to_state, at, remark}`.
  - **Firestore adapter** (reuse `handoff_store.py` pattern), **mock adapter** for tests.
  - Mirrored to BQ `case_audit_events` by `sync_zendesk_metrics.py` for reporting/lifecycle views.

### Case state

- Add explicit **WIP** to the conversation state model. Transitions: `new → open → WIP → pending → solved` (WIP set when an agent begins work). Each transition writes an `AuditLogPort` record. Extend `features/chat/detection.py` / `service.py` state handling; keep the open/solved gate intact.

### Escalation webhook

- New route `POST /webhooks/zendesk-sla-escalation` in `features/chat/router.py`:
  - Payload identifies the ticket + escalation level (8h escalate / 48h alarm).
  - Handler: write an audit record, and if the escalation carries a PIC WhatsApp number, fire a **WA-to-PIC** alert via the existing Twilio channel adapter.
  - Signature/secret verification consistent with existing Zendesk webhooks.

### Zendesk configuration (documented, not code)

- One **SLA policy** (first-response + resolution targets from `sla_minutes`).
- Two **time-based Automations**: at 8h with no agent response → escalate + call the webhook; at 48h unresolved → alarm + call the webhook.

### Tests

- `test_audit_log.py`: mock adapter records append/list; state transitions emit exactly one record each with correct from/to.
- `test_sla_webhook.py`: webhook handler writes audit + triggers WA alert on a PIC-carrying payload; rejects bad signatures.

---

## Item 4 — Routing enablement

**Goal:** make Zendesk-native routing/views/SLA work by ensuring every ticket carries the structured metadata they key on. No custom UI.

### In-repo

- Covered by Item 1's structured tags (`division_*`, `category_*`, `dept_*`, `pic_*`) plus the existing urgency→priority mapping (`zendesk.py:91`). Confirm ticket creation always sets priority and the routing tags.

### Zendesk configuration (documented)

- Enable **Agent Workspace** + **omnichannel routing** (presence, per-agent channel priority, status-based assignment, overflow to idle agents).
- Build **views** filtered by division/department/priority.
- Configure **desktop + sound + in-system notifications** for new inbound (satisfies BRD block A's pop-up alert on the Zendesk surface).

### Tests

- `test_zendesk_ticket_tags.py`: `create_ticket` emits the full expected tag set + priority for representative classifications.

---

## Item 5 — Agent-assist FAQ

**Goal:** give agents real-time keyword-matched FAQ suggestions with one-click reference and quality feedback, reusing the live KnowledgePort.

### Endpoints (in-repo)

- `GET /kb/suggest?session_id=<id>` **or** `?q=<text>` → reuses `KnowledgePort.search_kb`; returns ranked articles `{article_id, title, snippet, url}`. When `session_id` is given, derives the query from the latest customer turn in session state.
- `POST /kb/feedback` `{article_id, session_id, helpful: bool, score: 1-5}` → writes to the QA-label pipeline (`qa_labels`) tagged `kb_feedback`.
- New view `v_faq_quality`: avg score / helpful-rate per article.

### Zendesk sidebar app (thin scaffold)

- Minimal ZAF app under `apps/zendesk-agent-app/`: `manifest.json` + an iframe (`assets/iframe.html` + small JS) that, given the current ticket, calls `GET /kb/suggest` and renders clickable suggestions with a thumbs-up/down posting to `POST /kb/feedback`.
- Intentionally thin — full styling/polish is optional and out of scope for this block.

### Tests

- `test_kb_suggest.py`: suggest endpoint returns ranked results from a mock KnowledgePort; derives query from session state.
- `test_kb_feedback.py`: feedback writes a correctly-tagged `qa_labels` row.

---

## Non-functional & conventions

- **Ports + mocks** for all new I/O (`AuditLogPort`, `EmailReportPort`); pure logic (tag parsing, anomaly math, state transitions, KB ranking, export rendering) unit-tested without I/O.
- **Config** additions in `platform/config.py` with `.env.example` entries: SMTP/report recipients, anomaly thresholds, Zendesk SLA webhook secret.
- **Lint/type/test gates** per CLAUDE.md: `ruff format`, `ruff check --fix`, `mypy --strict`, `pytest` (backend); `vue-tsc` (frontend).
- **Backward compatibility:** all new BQ columns NULLABLE; existing views/endpoints unchanged; new endpoints additive.

## Dependencies & sequencing

1. **Item 1** first — its schema/tags underpin Items 2, 3, 4 reporting.
2. **Item 2** and **Item 3** are independent of each other; both depend on Item 1's columns for their reporting views.
3. **Item 4** is mostly config; its in-repo part ships with Item 1 (tags).
4. **Item 5** is independent (KnowledgePort already live) and can be built any time after Item 1's `qa_labels` conventions are confirmed.

## Out of scope (this block)

- Customer 360 / DMS / TSP (long-term, externally blocked).
- Custom agent console (Zendesk-native chosen instead).
- Social-media inbound channels.
- Real-time KB editing UI.

## Open questions — resolved

- **Division source:** derived from AI `category` via a static `CATEGORY_TO_DIVISION` map in `mapping.py` (Apps/Sales/Aftersales/Charging). Avoids adding a new classifier field.
- **SLA timing:** Zendesk Automations fire escalation; backend owns audit + WA alert. No in-app scheduler for SLA.
- **PIC identity:** primary source is the classification `pic_*` tag; falls back to Zendesk `assignee_id` when no tag is present.
