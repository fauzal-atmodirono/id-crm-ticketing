# Bot-Metrics Dashboard — Phase 3 (NPS: web survey + recording core + dashboard) — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorm complete)
**Scope:** Phase 3 of the cross-channel Bot-Metrics dashboard. Adds a **Net Promoter Score (NPS)**
metric — a channel-agnostic recording core, a **web** collection endpoint, and the dashboard view —
mirroring the existing CSAT design. Builds on Phase 1
(`docs/superpowers/specs/2026-06-29-metrics-dashboard-phase1-design.md`).

## Context

The north-star XLSMART board has both CSAT and NPS. CSAT already exists end-to-end: a shared
`record_csat_on_ticket` (comment + `csat_<score>` tag) feeds the Phase-1 Zendesk→BigQuery sync,
which surfaces it via the `v_csat` view. NPS is "like CSAT, but 0–10," so Phase 3 parallels that
exact architecture — **tag-based, riding the existing Phase-1 sync** (NOT the Phase-2 per-turn
`turn_events` path; NPS is a one-time relationship survey, not a per-turn signal).

Standard NPS scale: **0–10**, bucketed **detractors 0–6, passives 7–8, promoters 9–10**, with
**NPS = %promoters − %detractors** (range −100…+100).

### Decisions locked during brainstorming

- **Collection scope:** the **web** channel now (a `POST /chat/nps` endpoint) + the shared
  recording core + the dashboard view. WhatsApp / email / phone NPS prompts are deferred to a
  later follow-up (the recording core is built channel-agnostic so they only need to call it).
- **Web extent:** **backend endpoint only** — no frontend NPS widget this phase (consistent with
  how the other phases were scoped; the endpoint is testable directly).
- **Data path:** tag-based on the conversation ticket (`nps_<score>`), surfaced through the
  Phase-1 `conversations` table + a new `v_nps` view. NOT per-turn streaming.
- **Decoupling:** `record_nps` does **not** touch the handoff / `awaiting_survey` / resume-AI
  state that `record_csat` manipulates — NPS is a standalone metric.

## Architecture & Data Flow

```
POST /chat/nps {session_id, score:0-10}
  → OrchestratorService.record_nps(session_id, score, channel="web")
      → look up conversation_ticket_id in session state
      → if present: record_nps_on_ticket(port, ticket_id, score, channel)   # PURE-ish, best-effort
            → append comment "📣 Net Promoter Score: {score}/10 (via {channel})"
            → add tag  nps_{score}
      → store state["nps_score"] = score
  ▼
Zendesk ticket carries an  nps_<N>  tag   (alongside any csat_<N> tag)
  ▼  scripts/sync_zendesk_metrics.py  (Phase-1 sync, unchanged flow)
     map_ticket_to_row parses nps_<N> → ConversationRow.nps_score
     WRITE_TRUNCATE load → conversations  (now with an nps_score column)
  ▼
BigQuery view  v_nps  (per channel: respondents / promoters / passives / detractors / nps)
  ▼
Looker NPS tile  (built by the user from the guide)
```

## Components

| Unit | Responsibility |
|---|---|
| `features/chat/nps.py` | `record_nps_on_ticket(port, ticket_id, score, channel) -> None` — best-effort: posts `📣 Net Promoter Score: {score}/10 (via {channel})` + an `nps_{score}` tag, try/except + structlog. Mirrors `csat.py`. |
| `features/chat/service.py` | `record_nps(session_id, score, channel="web") -> bool` — get session (False if none); if `conversation_ticket_id` in state, call `record_nps_on_ticket`; set `state["nps_score"]=score`; persist. Does NOT mutate handoff/survey state. |
| `features/chat/router.py` | `NpsRequest{session_id:str, score:int = Field(ge=0, le=10)}` + `POST /chat/nps` → `record_nps(..., channel="web")` → `{"status":"ok","message":"Thank you for your feedback!"}`. Out-of-range score → automatic 422. |
| `features/metrics/mapping.py` | add `nps_score:int|None` to `ConversationRow`; `_nps_from_tags(tags)` parsing `^nps_(10|[0-9])$`; set on row; keep a ticket when it has external_id **or** a csat **or** an nps tag. |
| `features/metrics/bigquery_schema.py` | add `nps_score INT64` to `CONVERSATIONS_SCHEMA`; add the `v_nps` view to `view_ddls`. |
| `features/metrics/sync.py` | include `"nps_score": r.nps_score` in the load row dict. |

## `record_nps_on_ticket` (the shared core)

```python
async def record_nps_on_ticket(port, ticket_id, score, channel) -> None:
    try:
        await port.append_conversation_comment(
            ticket_id, f"📣 Net Promoter Score: {score}/10 (via {channel})"
        )
        await port.add_ticket_tag(ticket_id, f"nps_{score}")
    except Exception as e:
        _log.error("record_nps_on_ticket_failed", ticket_id=ticket_id, error=str(e))
```

## The `v_nps` view

Per `channel`:
- `respondents` = `COUNTIF(nps_score IS NOT NULL)`
- `promoters` = `COUNTIF(nps_score >= 9)`
- `passives` = `COUNTIF(nps_score BETWEEN 7 AND 8)`
- `detractors` = `COUNTIF(nps_score IS NOT NULL AND nps_score <= 6)`
- `nps` = `SAFE_DIVIDE(COUNTIF(nps_score >= 9) - COUNTIF(nps_score IS NOT NULL AND nps_score <= 6), COUNTIF(nps_score IS NOT NULL)) * 100`

(Same `GROUP BY channel` shape as `v_csat`; file-level `# ruff: noqa: S608` already present.)

## `conversations` table change

One added column: `nps_score INT64` (nullable), after `csat_score`. The sync's
`WRITE_TRUNCATE` load replaces the table with the new schema, so re-running the sync adds the
column and backfills `nps_score` (NULL unless the ticket carries an `nps_<N>` tag). No migration
needed.

## Error Handling

- `record_nps_on_ticket` is best-effort (try/except + structlog) — a tagging failure never breaks
  the request.
- `record_nps` returns `False` when the session is missing; tags only when a ticket exists
  (web `sim-` sessions without a handoff/capture have no ticket — same limitation as CSAT).
- The endpoint relies on Pydantic `Field(ge=0, le=10)` for validation → FastAPI returns 422 on a
  bad score with no custom code.

## Testing

Unit (pytest):
- `record_nps_on_ticket` — posts the comment + `nps_<score>` tag (fake port asserts both); a port
  exception is swallowed (no raise).
- `record_nps` — records on the ticket when `conversation_ticket_id` exists; stores
  `state["nps_score"]`; returns `False` when the session is missing; leaves
  `handoff_triggered`/`awaiting_survey` state UNTOUCHED (asserted).
- `/chat/nps` endpoint — valid score (e.g. 9) → `{"status":"ok"...}` and `record_nps` invoked
  (via a fake/instrumented orchestrator or FastAPI TestClient); score `11` and `-1` → 422.
- `mapping._nps_from_tags` — `nps_0`…`nps_10` parse to the int; `nps_11` ignored; no nps tag → None;
  an nps-only ticket (no external_id, no csat) is KEPT (channel `Other`).
- `map_ticket_to_row` — `nps_score` populated from the tag; existing csat/channel tests still pass.
- `bigquery_schema` — `CONVERSATIONS_SCHEMA` includes `nps_score INT64`; `view_ddls` includes
  `v_nps` with the four buckets + the `SAFE_DIVIDE(... ) * 100` NPS formula.
- `sync` — the load row dict includes `nps_score` (extend the existing sync test).

Live smoke (documented, manual): submit a couple of NPS scores via `POST /chat/nps` against a
session that has a ticket, re-run `scripts/sync_zendesk_metrics.py`, confirm `conversations`
gains the `nps_score` column and `v_nps` returns an NPS number; add the Looker tile.

Quality gates: `ruff format`, `ruff check`, `mypy --strict`, `pytest`.

## Configuration

None new — NPS reuses the existing `BIGQUERY_*` settings and the existing Zendesk credentials.

## Out of Scope (later)

- WhatsApp / email / phone NPS prompts (the conversational "ask" on those channels). The
  recording core is built channel-agnostic so each is a thin follow-up.
- A frontend NPS widget in the Vue app.
- Per-turn NPS / streaming (`turn_events`) — NPS is a one-time relationship survey.
- Free-text NPS parsing (`parse_nps`) — the web endpoint takes a numeric score directly.
- Scheduling/automation of the sync (already covered by Phase 2's scheduler, which will pick up
  the `nps_score` column automatically once deployed).

## Risks / Unknowns

- **No ticket → no NPS row:** web `sim-` sessions without a Zendesk ticket can't be tag-recorded
  (inherited CSAT limitation). Acceptable for the POC; documented.
- **Schema reload:** adding `nps_score` relies on the `WRITE_TRUNCATE` reload; confirm the next
  sync run picks up the new column (it does — the load job sets the schema).
- **Tag regex:** `^nps_(10|[0-9])$` must match `nps_10` AND single digits `nps_0`–`nps_9`; the
  `10|[0-9]` ordering matters so `nps_10` isn't truncated to `nps_1`.
