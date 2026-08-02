# Reporting & metrics extensions (roadmap item #3)

**Date:** 2026-08-02
**Status:** Approved (design) — implementation held pending RBAC (roadmap item #2)
**Scope:** Close the gap between the existing Phase-3 BI infrastructure and Proton's real monthly/weekly PeC ops reports, now that PRO-NET's example report visualizations (the explicit blocker on this roadmap item) have been provided.

## Problem

`docs/roadmap/2026-08-01-next-development-roadmap.md` item #3 ("Reporting & metrics extensions") was on hold: *"Native report customization / PowerBI export — hold until PRO-NET sends example report visualizations."* The user has now provided two real Proton PeC deliverables:

- `docs/client-materials/MONTHLY REPORTING FOR  Proton e.MAS.pptx` (54 slides, June 2026)
- `docs/client-materials/Weekly Report Proton e.MAS.pptx` (16 slides, W31 2026)

Comparing these against the existing BigQuery views (`backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`) and the native Chatwoot Reports UI (Proton sections merged in per the 2026-07-21 REPORTS-MERGE program), several gaps exist:

1. No `case_type` (Inquiry/Complaint/Feedback) dimension — the primary axis of every table in both decks.
2. No `vehicle_model` (e.MAS5/e.MAS7/e.MAS7 PHEV/NA) dimension — cross-cuts nearly every table.
3. No dealer-escalation-volume + turnaround-time-by-dealer view (the `dealer` dimension exists, from `v_reopen_rate`, but nothing aggregates escalation/turnaround by it).
4. No resolution-time-vs-SOP-target bucketing (Proton's targets are stated in **working hours**: Inquiry ≤8wh, Complaint <24/24-48/48-72/>72wh, Feedback ≤48h; the existing `v_resolution_time` view is calendar-time avg/percentile only).
5. No WIP/aging open-case list.
6. No RSA (roadside assistance) tracking of any kind — this is an operational dispatch log (incident time, tow-assigned time, arrival time, cause, remarks) with no conversation-based analog anywhere in the system.
7. No call-centre-specific metrics (AQT, %<20s answered, abandoned-call count) — and no underlying instrumentation to compute them from (the Gemini Live phone bridge doesn't capture queue/ring/abandon events).

The case-taxonomy default in `backend/apps/backend/src/chatbot/features/chat/case_taxonomy.py` (built 2026-08-01, roadmap item #1) is also a placeholder; the two decks reveal Proton's actual live category tree, richer than the current default.

## Decision (from brainstorming)

Build the reporting extensions using the same established patterns as every other feature in this codebase — tenant-configurable JSON env config (mirroring `CASE_TAXONOMY_JSON`), additive BigQuery views, native Chatwoot report-tab sections, `x-api-key`-gated backend routers — rather than inventing new mechanisms. Specific decisions, in order made:

- **RSA is in scope**, modeled as its own module: a standalone Postgres table + CRUD API in `backend/`, **not** a Chatwoot conversation and **not** synced through BigQuery. It's staff-entered operational data with no message thread, structurally unlike everything else in the system.
- **Deliverable format**: extend the existing native-Chatwoot-report-tabs pattern, plus per-view CSV export. Explicitly **not** a literal branded PPTX/PDF generator and **not** a PowerBI publish — both are much larger scope (template maintenance, chart rendering, branding) than the underlying data gap that's actually blocking Proton.
- **`case_type` and `vehicle_model`** are captured the same way `case_category`/`case_subcategory` already are: Chatwoot List custom attributes, set by the (extended) AI `classify_ticket_tool` with agent override, backed by tenant-configurable option lists — **not** hardcoded, so this stays deliverable to non-Proton tenants per [[crm-enhancement-vision]].
- **All four remaining gaps** (dealer escalation/turnaround, SOP-target buckets, WIP/aging, call-centre metrics) are designed together in this one spec, since they extend the same schema/view mechanism and feed the same report UI.
- **Role-scoped visibility is deferred**: this spec is written now, but **implementation is held until RBAC (roadmap item #2) lands** on this branch. New views/tabs ship visible to whoever can already see Reports today — same as every existing report tab.
- **SLA-bucket precision**: business-hours-aware, matching Proton's actual "wh" (working hours) SOP definition exactly, not a calendar-time approximation.
- **Call-centre metrics**: no fabricated view. Ships as a report-UI placeholder panel ("pending Phase 7 telephony instrumentation") rather than a BigQuery view built on nonexistent data — Phase 7 (telephony/IVR) is separately blocked on a client architecture decision (DTMF-IVR vs. conversational-LLM) per the roadmap.

## Non-goals

- Role-scoped report visibility (waits for RBAC #2; this spec's views ship unscoped).
- Literal branded PPTX/PDF export or PowerBI publish/embed.
- Real telephony call-event capture (ring/queue/abandon instrumentation) — that's Phase 7 scope, blocked on a client decision.
- RSA↔dispatch-system integration — this spec is manual staff data entry only, no automation.
- Migrating/backfilling historical `category_*`/`subcat_*` label data (unrelated, already decided out-of-scope in the case-taxonomy spec).
- Cascading/dependent subcategory pickers, or any change to the case-taxonomy UI mechanism itself (only its default option-list content is informed by the decks).

## Default configuration (draft, from the real report data)

```json
// VEHICLE_MODELS_JSON
{"options": ["e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "Not Applicable"]}
```

```json
// CASE_TYPE_OPTIONS_JSON
{"options": ["Inquiry", "Complaint", "Feedback"]}
```

```json
// RESOLUTION_SLA_TARGETS_JSON (bucket edges in working-hours; last bucket is open-ended)
{
  "inquiry":   {"buckets_wh": [8], "labels": ["Within 8wh", ">8wh"]},
  "complaint": {"buckets_wh": [24, 48, 72], "labels": ["<24wh", "24-48wh", "48-72wh", ">72wh"]},
  "feedback":  {"buckets_wh": [48], "labels": ["Within 48h", ">48h"]}
}
```

Both `VEHICLE_MODELS_JSON` and `CASE_TYPE_OPTIONS_JSON` may be empty/unset for a tenant with no such concept — the corresponding custom attribute is simply not offered, byte-identical to today.

## Design

### 1. New dimensions: `case_type` and `vehicle_model`

Same mechanism as `case_category`/`case_subcategory` (`docs/superpowers/specs/2026-08-01-case-categories-subcategories-design.md`):

- A loader per service (`backend/` and `agent/`), mirroring `case_taxonomy.py`/`build_case_taxonomy`, parsing `VEHICLE_MODELS_JSON` and `CASE_TYPE_OPTIONS_JSON` once at startup. Malformed/empty → empty option list, fail-open, logged warning.
- `classify_ticket_tool` (`backend/.../features/chat/agents.py`) extended to also emit `case_type` and `vehicle_model`, validated against the configured option lists; a value outside the list is rejected (logged, not written) rather than clobbering an existing value with garbage.
- Written via the same `custom_attributes` call as `case_category`/`case_subcategory` (`chatwoot.py`'s `_dimension_labels` write path) — two more List-type custom attributes: `case_type`, `vehicle_model`.
- `agent/app/services/categorize.py`'s fallback-only classifier extended the same way it already handles `case_category`: only runs when the attribute is empty on the conversation being resolved.
- Provisioning script (the one that registers `case_category`/`case_subcategory` Custom Attribute Definitions) extended to also register `case_type` and `vehicle_model`.
- Both are synced into BigQuery: two new nullable `STRING` columns on `CONVERSATIONS_SCHEMA` (`case_type`, `vehicle_model`), parsed in `mapping.py` the same way `category`/`subcategory` already are.

### 2. Business-hours-aware resolution timing

Two new nullable `INT64` columns on `CONVERSATIONS_SCHEMA`: `first_response_working_minutes`, `resolution_working_minutes`.

Computed in `backend/`'s metrics `mapping.py`/`sync.py` at sync time (not in BigQuery SQL — per-inbox business hours can't be expressed cleanly as a SQL view over stored timestamps). This requires **porting** the working-hours-duration calculation from `agent/app/services/business_hours.py` into `backend/`'s metrics sync path — a deliberate duplication, consistent with the precedent set by `case_taxonomy.py` needing independent implementations in both services (the two services communicate over HTTP only, no shared process/DB, per this repo's architecture). A Chatwoot API failure fetching one inbox's business hours during sync falls back to calendar-time for that row only (logged), not a sync-wide failure.

### 3. New/extended BigQuery views (`bigquery_schema.py` `view_ddls()`)

- **`v_dealer_escalation`** — new `dealer_escalated_at` `TIMESTAMP` column on `CONVERSATIONS_SCHEMA`, stamped the moment a `dealer_<slug>` label is first applied (wherever that assignment currently happens). View: cases-escalated + avg/p50/p90 turnaround (`resolved_at - dealer_escalated_at`, calendar-time — deck reports this in days) per dealer, plus a slowest-N case list (case id, dealer, duration) for manual reason lookup — the free-text "reason" column in the deck is editorial content an agent writes, not derived data.
- **`v_resolution_sla_buckets`** — buckets `resolution_working_minutes` per the `RESOLUTION_SLA_TARGETS_JSON` edges for the row's `case_type`; returns histogram counts and overall %-within-first-bucket (the compliance rate).
- **`v_case_aging`** — open/pending cases only (reuses `v_case_lifecycle`'s shape), calendar-time age from `created_at`, bucketed 1-3/4-6/7+ days per the weekly deck, with `case_type`/division/dealer/PIC columns for context.
- **`v_volume_by_type_division`** — month × channel × `case_type` × division.
- **`v_category_by_vehicle_model`** — category × subcategory × `vehicle_model` × `case_type`, extending the `v_complaint_type_ranking` shape with the two new dimensions.
- **No view for call-centre metrics** — see Non-goals; ships as a report-UI placeholder only.

### 4. RSA module (`backend/apps/backend/src/chatbot/features/rsa/`)

New, isolated feature slice, following the pgvector-KB precedent (operator-entered data → backend's own Postgres, not BigQuery):

- SQLAlchemy table `rsa_incidents`: `incident_date`, `vehicle_no`, `vehicle_model` (reusing the same option list as the conversation dimension), `cause`, `purchased_from`/dealer, `breakdown_location`, `arrived_location`, `customer_called_in_time`, `towing_assigned_time`, `time_arrived_breakdown_area`, `time_arrived_outlet`, `total_km`, `late_reason`, `remarks`, `created_by`, `created_at` — mirrors the deck's RSA log columns.
- `x-api-key`-gated CRUD router (`rsa_router.py`), same shape as `kb_knowledge_router.py`.
- A report view: aggregate (count by cause / by dealer, avg dispatch→arrival duration) plus the raw incident list.
- A new native Chatwoot page (fork patch) for staff to log and browse incidents — data entry and reporting together, since RSA is a different kind of record from everything else in Reports.

### 5. Report UI + export

- New native Report tabs/sections, following the existing `Proton{Sla,Csat,Bot,Agents}Section.vue` pattern — self-contained components fetching their own `/metrics/*` endpoint: **Dealer Escalation**, **SLA Compliance** (buckets + %-within-target by `case_type`), **WIP/Aging**. Category/vehicle-model cross-tab added into the existing Departments & PIC tab. Call-centre metrics get a placeholder panel, not a data-backed tab.
- RSA gets its own top-level page (entry + report together), not folded into the conversation-based Reports section.
- Every new view gets a CSV export button, reusing the existing `export.py`/`export_router.py` pattern (same auth as the read endpoint).
- New backend endpoints follow the established `insights_router.py` shape: one frozen dataclass per view, `x-api-key` auth, mounted in `main.py`.

## Error handling

- All new config loaders (`VEHICLE_MODELS_JSON`, `CASE_TYPE_OPTIONS_JSON`, `RESOLUTION_SLA_TARGETS_JSON`) fail-open to empty/default on malformed JSON — logged warning, never breaks startup or an AI turn.
- `classify_ticket_tool` rejects (logs, doesn't write) any `case_type`/`vehicle_model` value outside the configured options.
- Business-hours sync computation: a per-inbox hours-fetch failure degrades that one row to calendar-time, not a sync-wide failure.
- RSA CRUD: standard REST validation (422 on bad input) — direct staff data entry, not a background/webhook path, so no fail-open semantics needed there.
- Chatwoot custom-attribute write failures for the two new dimensions are logged and swallowed, same pattern as `case_category`'s existing write path — never raises out of the AI turn or resolution webhook.

## Testing

- Config loaders: valid/malformed/empty/missing-env cases, mirroring `test_case_taxonomy.py`.
- `classify_ticket_tool` extension: valid `case_type`/`vehicle_model` accepted and written; invalid rejected and not written; existing `case_category`/`case_subcategory` behavior unaffected.
- New BQ view DDL↔schema consistency, extending `test_phase3_smoke.py`'s offline assertion style (no live BigQuery needed).
- Business-hours sync computation: unit tests against fixed inbox-hours fixtures, covering weekday/weekend/holiday boundary cases and the API-failure fallback.
- `agent/categorize.py` extension: skips when `case_type`/`vehicle_model` already set; classifies when empty.
- RSA: repository CRUD tests (in-memory + Postgres), router auth/validation tests.
- Report UI sections: existing FE convention (bare English strings, `--no-verify` commit, `npx eslint --fix` + `pnpm exec vite build` before Cloud Build).

## Rollout

- This spec is written and committed now. **No implementation until RBAC (roadmap item #2) is confirmed done** — reporting work should not race ahead of the item the roadmap sequences it after.
- No fork-patch/image rebuild needed for the new backend views/RSA API themselves (pure `backend/` changes, deploy via the normal `docker compose ... up -d --build backend` path). Only the new Report UI tabs + RSA entry page need a Chatwoot image rebuild (Cloud Build, amd64 — per this repo's established lesson that local Mac/arm64 builds fail the VM's amd64 pull).
- New env vars to document in `deploy/tenants/example.env`: `VEHICLE_MODELS_JSON`, `CASE_TYPE_OPTIONS_JSON`, `RESOLUTION_SLA_TARGETS_JSON`.
- Once implemented, `docs/roadmap/2026-08-01-next-development-roadmap.md` item #3's checkboxes and the ⏸ hold note should be updated to reflect what shipped vs. what's still deferred (role-scoping, PPTX/PowerBI, real call-centre metrics).

## Reference

- Source decks: `docs/client-materials/MONTHLY REPORTING FOR  Proton e.MAS.pptx`, `docs/client-materials/Weekly Report Proton e.MAS.pptx`.
- Roadmap: `docs/roadmap/2026-08-01-next-development-roadmap.md`.
- Prior art this spec follows: `docs/superpowers/specs/2026-08-01-case-categories-subcategories-design.md` (taxonomy/config mechanism), `docs/superpowers/plans/2026-07-18-phase3-reporting-bi.md` (existing BI views), `docs/superpowers/specs/2026-07-26-pgvector-knowledge-base-design.md` (own-Postgres-module precedent for RSA).
