# Bot-Metrics Dashboard — Phase 2 (per-turn MetricsPort → BigQuery) — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorm complete)
**Scope:** Phase 2 of the cross-channel Bot-Metrics dashboard. Two parts:
1. The per-event `MetricsPort` instrumentation pipeline and the three signals Zendesk can't
   give us — **speed of response, fallback rate, bounce rate** — for the **text channels**
   (everything that flows through `handle_turn`).
2. **Automate the Phase-1 Zendesk→BigQuery sync** with an in-app scheduler (the "trigger"),
   so the `conversations` table refreshes on a schedule instead of a manual script run.

Builds on Phase 1 (`docs/superpowers/specs/2026-06-29-metrics-dashboard-phase1-design.md`).

## Context

Phase 1 derived Volume / Transfer-vs-Closed / CSAT from a Zendesk → BigQuery sync (no app
instrumentation, because those metrics already live in Zendesk). Speed, fallback, and bounce
do **not** live in Zendesk — they need per-turn instrumentation. Phase 2 builds that: a
best-effort `MetricsPort` that emits one event per turn into a BigQuery `turn_events` table,
from which three views derive the new tiles.

`handle_turn(session_id, text) -> TurnResult` is the single shared choke point for web,
WhatsApp, email, and messaging (6 router call sites), so one instrumentation point covers all
text channels. Voice (`handle_voice_turn`) and phone (the Gemini Live bridge) are separate
streaming paths with different timing semantics — out of scope here.

### Decisions locked during brainstorming

- **Channel scope:** text channels only (instrument `handle_turn`). Voice/phone later.
- **Fallback definition:** a turn is a fallback when the bot returned the empty-reply fallback
  (`_EMPTY_REPLY_FALLBACK`) or the technical-error reply — i.e. it produced no real answer.
  Code-derivable, no new model logic.
- **Bounce definition:** a session with exactly **one** turn (derived from turn counts; no
  per-event flag).
- **Transport:** BigQuery **streaming insert** (`insert_rows_json`), best-effort fire-and-forget.
- **One event type** (`turn_events`); all three signals are views over it (mirrors Phase 1).
- **Sync automation (the "trigger"):** an **in-app `BackgroundScheduler`** (APScheduler) runs
  the Phase-1 Zendesk→BQ sync on a fixed interval. Chosen over Cloud Scheduler because the POC
  runs locally (not deployed); the scheduler runs the synchronous sync in its own thread, needs
  no event loop, and starts at bootstrap. **Default OFF** (`METRICS_SYNC_ENABLED=false`) so
  dev/tests/CI never fire it; opt-in per environment.

## Architecture & Data Flow

```
handle_turn(session_id, text)        [web · WhatsApp · email · messaging]
  t0 = perf_counter()
  … run the turn (unchanged) …
  is_fallback = reply_text in _FALLBACK_REPLIES
  event = build_turn_event(session_id, occurred_at, latency_ms,
                           turn_count, is_fallback, handed_off)   # PURE
  await self._metrics.emit_turn(event)        # best-effort; never affects the turn
        → BigQueryMetricsAdapter.insert_rows_json(turn_events, [row])   (or NoOpMetrics)
  ▼
BigQuery: lv-playground-genai.demo_proton.turn_events   (one row per turn)
  + views: v_speed_of_response, v_fallback_rate, v_bounce_rate
  ▼
Looker Studio tiles (Initial/Subsequent Speed, Fallback Rate, Bounce Rate)
```

## Components

| Unit | Responsibility |
|---|---|
| `features/metrics/events.py` | `@dataclass(frozen=True) TurnEvent`; pure `build_turn_event(session_id, occurred_at, latency_ms, turn_count, is_fallback, handed_off) -> TurnEvent` — derives `channel` (from session_id prefix) and `is_first_turn` (`turn_count == 1`) |
| `features/metrics/mapping.py` | promote the existing private `_channel` to a public `channel_from_external_id(external_id) -> str` so both `map_ticket_to_row` (Phase 1) and `build_turn_event` reuse one prefix→channel mapping (DRY) |
| `features/chat/ports.py` | `MetricsPort` Protocol: `async def emit_turn(self, event: TurnEvent) -> None` |
| `features/chat/adapters/bigquery_metrics.py` | `BigQueryMetricsAdapter` — ensures the `turn_events` table + the 3 views exist on init; `emit_turn` does a best-effort `insert_rows_json`. `NoOpMetrics` (default) |
| `features/metrics/turn_schema.py` | `TURN_EVENTS_SCHEMA: list[bigquery.SchemaField]` + `turn_view_ddls(project, dataset, table) -> dict[str, str]` (the 3 views) |
| `features/chat/service.py` | `OrchestratorService.__init__` gains `metrics_port: MetricsPort = NoOpMetrics()`; `handle_turn` times the turn, computes `is_fallback`/`handed_off`/`turn_count`, and emits |
| `features/metrics/scheduler.py` | `run_sync_job(settings)` (best-effort `run_sync` + `ensure_views`, swallows errors) + `start_metrics_scheduler(settings)` (builds/starts a `BackgroundScheduler` running `run_sync_job` on an interval when `metrics_sync_enabled`, else returns `None`) |
| `platform/config.py` + `main.py` | `metrics_provider: Literal["noop","bigquery"] = "noop"`, `bigquery_turn_events_table: str = "turn_events"`, `metrics_sync_enabled: bool = False`, `metrics_sync_interval_hours: int = 6`; `main.py` builds the metrics adapter, injects it, and starts the scheduler (stopping it on app shutdown) |

## `turn_events` table

| column | type | source |
|---|---|---|
| `event_id` | STRING | uuid4 (set in the adapter at emit) |
| `occurred_at` | TIMESTAMP | turn time |
| `channel` | STRING | from session_id prefix |
| `session_id` | STRING | the session |
| `latency_ms` | INT64 | turn wall-clock |
| `is_first_turn` | BOOL | `turn_count == 1` |
| `is_fallback` | BOOL | bot produced no real answer |
| `handed_off` | BOOL | handoff triggered this turn |

## The three views

- `v_speed_of_response` — per `channel` × `is_first_turn`: `APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)]`
  AS `p99_latency_ms`, `AVG(latency_ms)` AS `avg_latency_ms`, `COUNT(*)` AS `turns`. (Initial =
  `is_first_turn` true → the "99% in 3s" tile; subsequent = false → "99% in 10s".)
- `v_fallback_rate` — per `channel`: `SAFE_DIVIDE(COUNTIF(is_fallback), COUNT(*))` AS `fallback_rate`,
  `COUNT(*)` AS `turns`.
- `v_bounce_rate` — per `channel`, over a per-session turn count:
  `bounced = COUNT(DISTINCT session WHERE turns = 1)`, `total_sessions = COUNT(DISTINCT session)`,
  `bounce_rate = SAFE_DIVIDE(bounced, total_sessions)`.

## Sync automation (the "trigger")

The Phase-1 sync logic (`features/metrics/sync.py:run_sync` + `ensure_views`) is unchanged and
fully reused. Phase 2 only adds a scheduler around it:

```
app bootstrap (METRICS_SYNC_ENABLED=true)
  └─ BackgroundScheduler.start()        # APScheduler, own thread; no event loop needed
       └─ every METRICS_SYNC_INTERVAL_HOURS:
            run_sync_job(settings):
              run_sync(settings)         # Phase-1: fetch Zendesk → map → WRITE_TRUNCATE load
              ensure_views(settings)     # Phase-1: (re)create the conversations views
              (any error logged + swallowed — a failed run never crashes the app)
  └─ @app.on_event("shutdown") → scheduler.shutdown(wait=False)
```

- The job is **synchronous** (Phase-1 `run_sync` uses `httpx.Client` + the BigQuery client), so a
  thread-based `BackgroundScheduler` is the right fit — it won't block FastAPI's event loop.
- The first run fires shortly after startup (`next_run_time` ≈ now) so the dashboard refreshes
  promptly, then repeats on the interval. The manual `scripts/sync_zendesk_metrics.py` still works
  for an on-demand backfill.
- Default OFF. Enabling requires the same Zendesk + BQ creds the manual sync already uses.

## Error Handling
- `emit_turn` is best-effort: the adapter wraps `insert_rows_json` in try/except + structlog;
  any failure (BQ down, schema drift) is logged and swallowed — the turn is never affected.
- `handle_turn` wraps the emit so an instrumentation error can't change the user-facing result.
- Default `metrics_provider="noop"` → `NoOpMetrics` (local/dev/tests do nothing).

## Testing
Unit (pytest):
- `build_turn_event` (pure): channel from each session_id prefix; `is_first_turn` true at
  `turn_count==1`, false otherwise; fields pass through.
- `channel_from_external_id` reuse: Phase-1 `map_ticket_to_row` tests still pass after the rename.
- `turn_schema`: the 8 fields + the 3 view DDLs (keys, FQ targets, key aggregates —
  `APPROX_QUANTILES`, `COUNTIF(is_fallback)`, the bounce CTE).
- `handle_turn` emits: with a fake `MetricsPort`, a normal turn emits `is_fallback=False`; an
  empty/error turn emits `is_fallback=True`; a handoff turn emits `handed_off=True`;
  `is_first_turn` true on the first turn of a session. (Use the existing `handle_turn` test
  harness + a capturing fake metrics port.)
- `NoOpMetrics.emit_turn` is a no-op.

Live smoke (documented): set `METRICS_PROVIDER=bigquery`, run a few turns across channels,
confirm rows land in `turn_events` and the three views return data; add the tiles to Looker.

Quality gates: `ruff format`, `ruff check`, `mypy --strict`, `pytest`.

## Configuration
| Setting | Default | Purpose |
|---|---|---|
| `metrics_provider` | `noop` | `bigquery` enables per-turn emit |
| `bigquery_turn_events_table` | `turn_events` | the event table |
| `metrics_sync_enabled` | `false` | `true` starts the in-app Zendesk→BQ sync scheduler |
| `metrics_sync_interval_hours` | `6` | how often the scheduled sync runs |
| (reused) `bigquery_project_id` / `bigquery_dataset` | — | BQ target |

## Out of Scope
- Voice (`handle_voice_turn`) and phone (Live bridge) per-turn metrics — separate streaming
  paths; a later add.
- Cloud-native scheduling (Cloud Scheduler + Cloud Run Job) — the in-app scheduler covers the
  POC; a cloud trigger is a deploy-time follow-up once the backend has a public URL.
- Changing the Phase-1 sync logic itself (full `WRITE_TRUNCATE` reload) — reused as-is.
- NPS survey (Phase 3); Accuracy / Quality manual entry (Phase 4).
- The Looker dashboard build itself (guide only, as in Phase 1).
- Backfill of historical turns (no per-turn history exists pre-instrumentation; `turn_events`
  accrues from deploy forward).

## Risks / Unknowns
- **Streaming-insert latency/cost:** rows are queryable after a short streaming-buffer delay and
  carry a small per-row cost — acceptable for the POC volume.
- **`turn_count` source:** derived from the session's `chat_history` in `handle_turn`; confirm
  the count reflects user turns at emit time (the plan pins the exact expression).
- **No history:** `turn_events` is empty until `METRICS_PROVIDER=bigquery` is deployed and
  traffic flows; the speed/fallback/bounce tiles are blank until then.
