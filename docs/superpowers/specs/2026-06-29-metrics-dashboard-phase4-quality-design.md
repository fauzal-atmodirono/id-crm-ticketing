# Bot-Metrics Dashboard — Phase 4 (Accuracy/Quality manual QA entry) — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorm complete)
**Scope:** Phase 4 (final phase) of the cross-channel Bot-Metrics dashboard. Adds the **Accuracy /
Quality** metrics via a manual QA-entry path: an authenticated endpoint where an internal reviewer
labels a conversation, written to a dedicated BigQuery `qa_labels` table and surfaced through a
`v_quality` view. Builds on Phase 1 (`...phase1-design.md`) and reuses the Phase-2 direct-BigQuery
write pattern.

## Context

Phases 1–3 cover the metrics that are either derivable from Zendesk (volume, transfer-vs-closed,
CSAT) or self-reported by the customer (NPS). **Accuracy and Quality are different**: they're
produced by an internal QA reviewer assessing a *sample* of conversations — not the customer
rating their own ticket. So the tag-on-the-ticket pattern (CSAT/NPS) is the wrong fit; QA labels
get their own table written directly to BigQuery (the Phase-2 `insert_rows_json` pattern, made
non-blocking and best-effort), with reviewer + notes + timestamp captured per label.

### Decisions locked during brainstorming

- **Data model:** a richer record per label — `conversation_id`, `accuracy`, `quality`,
  `reviewer`, `notes`, `labeled_at` — in a dedicated **`qa_labels`** BigQuery table (NOT Zendesk
  tags, NOT the `conversations` table).
- **Entry mechanism:** an authenticated internal HTTP endpoint **`POST /qa/label`** (no CLI, no
  frontend this phase).
- **Scale:** `accuracy` and `quality` are each an integer **0–100 (percent)**; the view's `AVG` is
  directly the board percentage.
- **Per-channel breakdown:** `v_quality` JOINs `qa_labels` to the existing `conversations` table on
  `conversation_id` (channel lives in `conversations`).
- **Auth:** the endpoint requires an `X-API-Key` header matching `settings.qa_api_key`
  (constant-time compare) → 401 if unset/missing/mismatched. Default-off for the BQ write
  (`qa_provider="noop"`).

## Architecture & Data Flow

```
POST /qa/label  (X-API-Key: <qa_api_key>)
   {conversation_id, accuracy:0-100, quality:0-100, reviewer, notes?}
   → verify api key (constant-time)               → 401 if unset/missing/mismatch
   → Pydantic validation (0-100 bounds)            → 422 on out-of-range
   → QaLabel(... labeled_at = now)                 (PURE dataclass; endpoint stamps the time)
   → await qa_port.record_label(label)             best-effort, non-blocking
         → BigQueryQaLabels.insert_rows_json(qa_labels, [row])   (or NoOpQaLabels)
   ▼
BigQuery: lv-playground-genai.demo_proton.qa_labels   (one row per label)
   + view v_quality  (JOIN conversations USING(conversation_id), per channel)
   ▼
Looker tile (Accuracy % / Quality % by channel)
```

## Components

| Unit | Responsibility |
|---|---|
| `features/metrics/qa.py` | `@dataclass(frozen=True) QaLabel` (`conversation_id`, `accuracy`, `quality`, `reviewer`, `notes`, `labeled_at: datetime`); `QaLabelPort` Protocol (`async def record_label(self, label: QaLabel) -> None`); `NoOpQaLabels` (default — drops the label) |
| `features/metrics/qa_schema.py` | `QA_LABELS_SCHEMA: list[bigquery.SchemaField]` (6 fields) + `qa_view_ddls(project, dataset, table="qa_labels", conversations_table="conversations") -> dict[str,str]` (the `v_quality` view) |
| `features/metrics/qa_adapter.py` | `BigQueryQaLabels(settings, *, client=None)` — ensures the `qa_labels` table + `v_quality` view on init (best-effort); `record_label` does a best-effort, non-blocking `await asyncio.to_thread(insert_rows_json, ...)`. `build_qa_label_port(settings) -> QaLabelPort` (returns `BigQueryQaLabels` when `qa_provider=="bigquery"`, else `NoOpQaLabels`; falls back to NoOp on init failure) |
| `features/metrics/qa_router.py` | `QaLabelRequest{conversation_id:str, accuracy:int(0-100), quality:int(0-100), reviewer:str, notes:str=""}`; `build_qa_router(qa_port, settings) -> APIRouter` with `POST /qa/label` — api-key auth, builds the `QaLabel` (stamps `labeled_at`), calls `record_label`, returns `{"status":"ok"}` |
| `platform/config.py` + `main.py` | `qa_provider: Literal["noop","bigquery"]="noop"`, `bigquery_qa_labels_table:str="qa_labels"`, `qa_api_key:str=""`; `main.py` builds `build_qa_label_port(settings)` and `app.include_router(build_qa_router(qa_port, settings))` |

## `qa_labels` table

| column | type | source |
|---|---|---|
| `conversation_id` | STRING (REQUIRED) | the labeled ticket/conversation id |
| `accuracy` | INT64 | 0–100 |
| `quality` | INT64 | 0–100 |
| `reviewer` | STRING | who labeled it |
| `notes` | STRING | free-text rationale (may be empty) |
| `labeled_at` | TIMESTAMP | endpoint stamp (`now`) |

## The `v_quality` view

Per `channel`, joining the QA labels to the synced conversations:

```sql
CREATE OR REPLACE VIEW `<p>.<d>.v_quality` AS
SELECT c.channel,
       COUNT(*) AS labels,
       AVG(q.accuracy) AS avg_accuracy,
       AVG(q.quality)  AS avg_quality
FROM `<p>.<d>.qa_labels` q
JOIN `<p>.<d>.conversations` c USING (conversation_id)
GROUP BY c.channel
```

`avg_accuracy` / `avg_quality` are the board percentages. (Multiple labels per conversation are all
averaged in — latest-wins / multi-rater weighting is out of scope.)

## Auth

- `qa_api_key` (default `""`). The endpoint compares the request's `X-API-Key` header to it with
  `hmac.compare_digest` (constant-time). **401** when `qa_api_key` is empty (endpoint locked until
  configured — secure by default), when the header is missing, or when it mismatches. This is a
  genuine gate (per the repo's Security Mandate), not a deferred go-live TODO.

## Error Handling

- `record_label` is best-effort: the adapter wraps `insert_rows_json` (run via `asyncio.to_thread`
  so it never blocks the event loop) in try/except + structlog; any failure is logged and swallowed
  — the 200 response is unaffected.
- `build_qa_label_port` wraps `BigQueryQaLabels(settings)` construction in try/except → falls back
  to `NoOpQaLabels` on failure (e.g. missing ADC), so a misconfigured BQ never crashes bootstrap.
- Default `qa_provider="noop"` → `NoOpQaLabels` (dev/tests/CI write nothing).
- The endpoint's auth + Pydantic validation produce 401 / 422 before any write.

## Testing

Unit (pytest):
- `QaLabel`: frozen dataclass, exact 6-field shape; pure (no clock inside).
- `qa_schema`: the 6 `QA_LABELS_SCHEMA` fields (names + types); `qa_view_ddls` produces `v_quality`
  with the JOIN on `conversation_id`, `AVG(accuracy)`/`AVG(quality)`, `GROUP BY channel`, and the
  fully-qualified `qa_labels`/`conversations` targets.
- `BigQueryQaLabels` (fake injected BQ client, NO live GCP): init ensures dataset + `qa_labels`
  table + `v_quality` view; `record_label` inserts a row dict with all 6 fields and `labeled_at`
  ISO-serialized; an insert exception is swallowed (no raise).
- `build_qa_label_port`: returns `NoOpQaLabels` by default; returns `BigQueryQaLabels` when
  `qa_provider="bigquery"`; falls back to `NoOpQaLabels` when adapter init raises.
- `NoOpQaLabels.record_label` is a no-op.
- `POST /qa/label` (FastAPI TestClient + a fake `QaLabelPort`): valid label with the right
  `X-API-Key` → 200 and `record_label` called once with the submitted fields; wrong key → 401;
  missing key → 401; (`qa_api_key` unset) → 401; `accuracy`/`quality` of 101 or -1 → 422.

Live smoke (documented): run the Phase-1 sync (so `conversations` exists), set
`QA_PROVIDER=bigquery` + `QA_API_KEY`, `POST /qa/label` a couple of labels for real ticket ids,
confirm rows land in `qa_labels` and `v_quality` returns avg accuracy/quality per channel; add the
Looker tile.

Quality gates: `ruff format`, `ruff check`, `mypy --strict`, `pytest`.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `qa_provider` | `noop` | `bigquery` enables the live QA-label write |
| `bigquery_qa_labels_table` | `qa_labels` | the QA-label table |
| `qa_api_key` | `""` (locked) | shared secret for `POST /qa/label` (`X-API-Key`) |
| (reused) `bigquery_project_id` / `bigquery_dataset` | — | BQ target |

## Out of Scope (later)

- A frontend QA-review UI and a CLI bulk-import (CSV → `qa_labels`).
- Multi-rater aggregation / latest-label-wins (the view averages all label rows).
- Auto-sampling which conversations to send for QA review.
- Per-turn / streaming QA — labels are a manual, conversation-level annotation.

## Risks / Unknowns

- **View ordering / JOIN dependency:** `v_quality` references `conversations`, so that table must
  exist when the view is created. The adapter creates the view best-effort (swallows if
  `conversations` is missing); the live-enable doc instructs running the Phase-1 sync first, then
  starting with `qa_provider=bigquery`. The `conversations` table persists across syncs
  (`WRITE_TRUNCATE` truncates+loads, never drops), so `v_quality` keeps resolving.
- **No-ticket conversations** can't be labeled meaningfully (no `conversations` row to join) —
  same family of limitation as CSAT/NPS.
- **Streaming-insert visibility:** rows are queryable after a short streaming-buffer delay (same
  as Phase-2 `turn_events`); acceptable for the POC.
