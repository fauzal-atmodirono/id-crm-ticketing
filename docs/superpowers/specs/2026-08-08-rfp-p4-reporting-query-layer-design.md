# P4 — Reporting Query Layer: Period, Timezone, Filters

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p4-reporting-query-layer.md`
**Closes:** 13 PARTIAL requirements
**Effort:** 2–3 weeks · **Wave:** 2 · **Blocked by:** P3 (schema columns)

---

## 1. The problem, precisely

The BigQuery layer is the most substantial thing in this build — 28 views across
four schema modules. It fails on three axes that have nothing to do with the
metrics themselves and everything to do with how they are queried.

**Five of eight endpoints reject any date range.** `period_query.py::reject_period`
raises HTTP 400 for `/metrics/departments`, `/metrics/callcenter`,
`/metrics/dealer-escalation`, `/metrics/sla-buckets` and `/metrics/case-aging`,
because their views have no date column to filter on. Only `/metrics/dashboard`,
`/metrics/lifecycle` and `/metrics/volume-by-type` accept `?from=&to=&granularity=`.

The visible consequence is worse than a 400: the Weekly Report page (patch
`0044`) renders a week header, and the dealer, SLA-bucket and aging sections
underneath it show **all-time** figures. A reader has no way to know. C1-08 and
C1-09 have the same defect — a June dealer figure that is actually every dealer
escalation ever recorded, printed under a "June 2026" header, misrepresents it.

**Every bucket is a UTC calendar day for a UTC+8 tenant.** This is documented at
length in `bigquery_schema.py:190-220`, including the fix: *"The right eventual
fix is a tenant-configurable reporting time zone threaded through `view_ddls()`
(a `Settings` field, defaulted to UTC so today's numbers are unchanged) —
recommended, not built here."* The comment also explains why it was left: changing
`DATE(created_at)` to `DATE(created_at, 'Asia/Kuala_Lumpur')` re-buckets every
historical figure on every dashboard in one deploy.

The failure mode is precisely characterised there and worth repeating because it
determines how this gets tested: it is **not** a total wrong by a fixed amount.
It is a small systematic shift of cases between adjacent buckets — "close but
not quite" — which will pass an acceptance gate and fail a reconciliation
meeting.

**No filters exist.** §4.81 asks for filtering by date range, agent, team and
channel. There is **no `agent`, `team`, `department` or `channel` query parameter
on any `/metrics/*` route**. Those cuts exist only as pre-grouped view columns
that the page aggregates client-side, which means "show me only Ahmad's cases"
is not expressible.

Two smaller items ride along because they live in the same files:

- **`v_reopen_rate` reads a column nothing writes.** `reopen_count` comes from
  `additional_attributes` and depends on an external integration that does not
  exist, so the column can be entirely NULL. The view is correct and the metric
  is a shell (§4.60).
- **No per-dealer first-response view.** Dealer appears in the reopen, lifecycle,
  escalation and aging views but not in the FRT view, so §4.59's per-dealer
  first-response ranking cannot be produced.

## 2. What this package delivers

1. Date columns on the five views that lack them, so `reject_period` can be
   deleted rather than worked around.
2. A tenant-configurable reporting timezone, threaded exactly as the existing
   code comment prescribes, defaulted to UTC.
3. Real filter parameters on the metrics routes.
4. A defined and emitted reopen event.
5. The per-dealer first-response view.
6. A tag-keyword report (§4.80, GAP, ~1 week, and it belongs to this layer).
7. A real Power BI artefact instead of a manual runbook.

## 3. Design

### 3.1 Date columns and the deletion of `reject_period`

`reject_period` exists to prevent a specific, well-understood lie: an endpoint
that silently ignores a date range and returns all-time data. Its docstring says
so. It is a good guard and P4's goal is to **make it unnecessary**, not to
bypass it.

Each of the five views gains a date column at day grain, following the pattern
the three working views already use (`v_volume_daily`, `v_state_trend_daily`,
`v_volume_by_type_division_daily` — the day-grain sources every period-scoped
read filters through via `query_adapter._day_grain_block_for_period`).

| View | Date column derives from | Grouping consequence |
|---|---|---|
| `v_dept_pic_performance` | `created_at` | Per-day rows, aggregated at read time |
| `v_dealer_escalation` | `dealer_escalated_at` | **Not `created_at`** — see below |
| `v_resolution_sla_buckets` | `resolved_at` | A case belongs to the period it was *resolved* in |
| `v_case_aging` | `created_at` | Aging is as-of-now; the date scopes the cohort |
| `v_callcenter` | `created_at` | — |

**`v_dealer_escalation` keys on `dealer_escalated_at`, not `created_at`**, and
this is a decision with a reporting consequence worth stating on the slide: a
case created in May and escalated to a dealer in June appears in June's dealer
report. That is the right answer — the report measures dealer turnaround, and
the dealer's clock starts when the escalation reaches them — but it means
dealer counts will not sum to case counts for the same month, and somebody will
ask. `maybe_stamp_dealer_escalation` already stamps that timestamp precisely so
this is possible.

`v_case_aging`'s date column deserves a note too: aging is computed as-of-now,
so a date range on it selects *which cases* to age, not *when* they were aged.
The endpoint documents this rather than pretending the two are the same.

Once all eight endpoints accept a period, `reject_period` and its call sites are
removed — leaving it in place as dead code would invite a future view to be
added without a date column and quietly re-enter the failure mode.

### 3.2 Reporting timezone

Implemented as the existing comment prescribes, and defaulted as it prescribes:

```python
def view_ddls(project: str, dataset: str, sla_targets_json: str,
              reporting_timezone: str = "UTC") -> dict[str, str]:
    """...
    reporting_timezone threads into every DATE() call. Defaults to "UTC" so an
    unmodified tenant's numbers are byte-identical to today's. Setting it to
    "Asia/Kuala_Lumpur" re-buckets every historical figure on every dashboard
    in one deploy -- which is correct, and is a decision an operator makes
    deliberately, not a default they receive by surprise.
    """
```

`REPORTING_TIMEZONE`, default `UTC`. Every `DATE(created_at)` becomes
`DATE(created_at, @tz)`.

**The migration is the hard part, not the code.** Switching a live tenant to
`Asia/Kuala_Lumpur` moves cases between adjacent buckets across the entire
history at once. Every published figure changes slightly. So P4 ships:

- a **comparison report** (`scripts/compare-reporting-timezone.py`) that runs
  both bucketings over the same window and prints the per-bucket delta, so the
  size of the shift is known *before* the switch, not discovered after;
- a runbook step requiring that comparison to be run and reviewed;
- a recommendation to switch at a period boundary (month end), so the seam in
  any series is at a natural break.

This is the "close but not quite" failure the code comment warns about, handled
by measuring it rather than hoping.

### 3.3 Filters (§4.81)

A shared dependency, mirroring how `PeriodQuery` already works:

```python
# features/metrics/filter_query.py  (new)

@dataclass(frozen=True)
class MetricFilters:
    agent_id: int | None = None
    team: str | None = None
    department: str | None = None
    channel: str | None = None
    dealer: str | None = None
```

Applied as parameterised `WHERE` predicates in `query_adapter`, never string
interpolation — these values arrive from a query string, and the existing
adapter already parameterises the period the same way.

Two constraints that keep this honest:

- **A filter on a column a view does not carry is a 400, not silently ignored.**
  Same principle as `reject_period`: an ignored filter is a lie with a header on
  it. The error names the view and the filter.
- **Filters compose with the period**, and `previous_period` comparisons apply
  the same filters to both windows — otherwise a week-over-week delta compares
  one agent's week against everybody's.

### 3.4 The reopen event (§4.60)

`v_reopen_rate` is correctly written and reads `reopen_count` from
`additional_attributes`, which nothing in this codebase populates.

Define it: **a reopen is a transition from `resolved` to any open state.**
Chatwoot fires `conversation_status_changed`; `record_conversation_status` in
`agent/app/services/sync.py` is currently a no-op stub kept as the router's
dispatch target — exactly the hook CLAUDE.md describes it as being kept for.

The handler increments a `reopen_count` custom attribute and stamps
`last_reopened_at`. Idempotent per transition, fail-open, and it costs one
implementation in the stub that was left for it.

Historical rows keep their NULL. `v_reopen_rate` gains a coverage note (P3's
`coverage.py`) so a reopen rate computed over a period where the event was not
yet being emitted says so.

### 3.5 Per-dealer first response (§4.59)

Two additions:

- `v_first_response_by_dealer` — mirrors `v_first_response_by_channel`, grouped
  by dealer.
- **An attainment-rate metric**, which is what §4.59 actually asks for: not
  "average first response time" but "**2-hour first response *rate***". A
  percentage of cases meeting the target, per department and per dealer. It
  needs the target, which is P5 — so P4 builds the view with the threshold as a
  parameter and P5 supplies it from the targets store.

Once P1 lands, this reads `first_response_working_minutes`, so the 2-hour target
is 2 *working* hours, matching every other target in the pack.

### 3.6 Tag-keyword report (§4.80)

GAP, ~1 week, explicitly deferred in the Phase-3 reporting plan to "Chatwoot
native label filtering / Power BI slicers". It belongs here because it is a
query-layer feature: `labels` is already a column on `CONVERSATIONS_SCHEMA`.

`GET /metrics/by-tag?tag=<keyword>` plus `v_volume_by_tag`, unnesting `labels`.
Period- and filter-aware like everything else in this package.

### 3.7 Power BI (§4.55)

`features/metrics/docs/power-bi-runbook.md` is 162 lines of manual setup. All
five data cuts §4.55 names exist as views. What does not exist is a `.pbix`, a
dataset, or an automated connector — raised at the 2026-07-28 demo (feedback
item 5) and still open.

P4 ships a real `.pbix` with the 19 views bound, DirectQuery configured, a
service-account auth path and a documented refresh schedule. The runbook stays,
demoted to "how to rebuild this artefact".

This is the least technically interesting item in the package and the most
visible to the client, because it is the one they raised.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Switching the timezone silently changes every historical figure | Default UTC; comparison script; runbook step; switch at a period boundary |
| A date column on the wrong timestamp makes dealer figures unreconcilable | `dealer_escalated_at` chosen deliberately and documented on the endpoint |
| Removing `reject_period` lets a future dateless view through | Its removal is paired with a schema test asserting every period-capable view has a date column |
| Filters silently ignored on views that lack the column | 400 naming the view and the filter |
| Reopen rate looks wrong because the event was only recently emitted | Coverage note on the view |
| Re-bucketing invalidates a figure already sent to the client | The runbook requires the comparison output be attached to the change record |

## 5. Testing

- **Period** (`test_dashboard_router_period.py` extensions): all eight endpoints
  accept a range; each returns a different result for two different ranges (the
  test that would have caught the original defect); `previous_period` applies
  the same filters.
- **Timezone** (`test_bigquery_schema_timezone.py`): default UTC produces DDL
  byte-identical to today; MYT shifts a 23:00-MYT case into the next day; an
  unknown timezone is rejected at startup, not at query time.
- **Filters** (`test_filter_query.py`): each filter narrows; unsupported filter
  400s naming the view; filters parameterised not interpolated (assert the
  query parameters, not the SQL string).
- **Reopen** (`test_reopen_event.py`): resolved→open increments; open→pending
  does not; repeated identical events do not double-count; fail-open.
- **Dealer FRT** (`test_bigquery_schema.py`): view present, columns exist,
  attainment rate computed against a supplied threshold.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `REPORTING_TIMEZONE` | `UTC` | Today's bucketing exactly |
| `METRICS_FILTERS_ENABLED` | `false` | Off = filter params 400 as unknown |
| `REOPEN_TRACKING_ENABLED` | `false` | Off = `record_conversation_status` stays a no-op |

`REPORTING_TIMEZONE` is a value, not a boolean, and its default is the identity
transform — the same shape P1 uses for its clock flag, and for the same reason.

## 7. Requirements closed

2.2.3, 4.48, 4.51, 4.55, 4.59, 4.60, 4.74, 4.81, C1-08, C1-09, C2-01, C2-02,
C2-05 — plus **4.80** (GAP), which is a query-layer feature and would be
artificial to schedule separately.

**Not closed here:** §4.81's "real-time" clause. The sync is a batch on
`metrics_sync_interval_hours` (default 6). P4 makes the freshness contract
explicit in the API response rather than claiming real-time; genuinely real-time
reporting is a streaming-ingest project and is not in this programme.
