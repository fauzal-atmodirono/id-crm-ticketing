# P5 — Targets Store, Control-Item Slide & Report Delivery: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the slide PRO-NET reads first — C1 page 48, fourteen metrics against fourteen targets — with the nine rows the data supports populated and the five it does not stated as unmeasured, with reasons.

**Architecture:** A targets store following the `PicStore`/`DealerStore`/`SlaPolicyRepository` pattern already used three times in this codebase, plus one pure `evaluate()` comparison with **four** outcome states. The fourth state (`no_data`) is the design's whole point: a metric that cannot be measured must never render as a metric that was missed.

**Tech Stack:** Python 3.12, FastAPI, Firestore, BigQuery views, APScheduler-style cron, openpyxl/reportlab via the existing `export.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p5-targets-and-report-delivery-design.md`

## Global Constraints

- **P1 and P4 must land first.** Four control-item rows are working-hours figures (P1) and every row is period-scoped (P4).
- **`no_data` is never `missed`.** A metric with no source has not failed its target. This is the single most important behaviour in the package — a control-item slide that shows 0% abandon rate because there is no call queue is a false claim about performance, made to the client, on their headline slide.
- **`RESOLUTION_SLA_TARGETS_JSON` seeds the store; it does not compete with it.** Items 7 and 8 are already MET and must stay byte-identical.
- **Never hardcode a working day as 480 minutes.** Derive it from the inbox's configured hours.
- **Nothing is classified `HQ`.** Same constraint as P3, same reason (Q5).
- Env vars go in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/metrics/targets_store.py` | **New.** Firestore-backed `Target` CRUD, scope resolution |
| `backend/.../features/metrics/attainment.py` | **New.** Pure `evaluate()`, four states |
| `backend/.../features/metrics/control_items.py` | **New.** The fourteen-row declaration table |
| `backend/.../features/metrics/control_items_router.py` | **New.** `GET /metrics/control-items` |
| `backend/.../features/metrics/targets_router.py` | **New.** Admin CRUD, `targets.manage` |
| `backend/.../features/metrics/report_schedules.py` | **New.** Cron + relative-period store |
| `backend/.../features/metrics/scheduler.py` | **Modify.** Fire from schedules; keep the legacy interval |
| `backend/.../features/metrics/export.py` | **Modify.** Generalise `render_xlsx`/`render_pdf` |
| `backend/.../features/metrics/bigquery_schema.py` | **Modify.** RSA attainment + leadtime views |
| `deploy/chatwoot-fork/patches/00NN-targets-admin.patch` | **New.** The targets admin page |

---

### Task 1: The targets store

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/targets_store.py`
- Create: its test file

**Interfaces:**
- Consumes: Firestore, `settings.resolution_sla_targets_json` (seed only).
- Produces: `Target(key, comparator, value, unit, attainment_pct, scope)` and `TargetsStore.resolve(key, scope) -> Target | None`. Task 2 consumes `Target`; task 3 consumes `resolve`.

**Tests first:**

```python
async def test_a_tenant_wide_target_resolves_for_any_scope():
async def test_a_division_scoped_target_beats_the_tenant_wide_one():
async def test_an_unknown_key_resolves_to_none_not_a_zero_target():
async def test_the_store_seeds_items_7_and_8_from_resolution_sla_targets_json():
async def test_seeding_is_idempotent_and_never_overwrites_an_operator_edit():
async def test_a_working_hours_unit_round_trips():
async def test_a_target_with_an_attainment_pct_round_trips():
async def test_an_unknown_unit_is_rejected_at_write_time():
```

**Test three is a real trap.** An unknown key resolving to `Target(value=0)` would make every unconfigured metric render as "missed by everything". It must resolve to `None`, which `evaluate` turns into `no_target`.

Test five protects the seed: an operator who tightens the complaint target must not have it reverted on the next restart.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/metrics/test_targets_store.py -q`

---

### Task 2: Attainment comparison (pure)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/attainment.py`
- Create: its test file

**Interfaces:**
- Consumes: `Target` (task 1), an actual value.
- Produces: `evaluate(actual, target) -> Attainment(status, actual, target, variance)` where status ∈ `met` / `missed` / `no_data` / `no_target`.

**Tests first:**

```python
def test_an_actual_inside_an_lte_target_is_met():
def test_an_actual_outside_an_lte_target_is_missed():
def test_an_actual_above_a_gte_target_is_met():
def test_a_none_actual_is_no_data_and_never_missed():          # the load-bearing test
def test_a_none_target_is_no_target_and_never_missed():
def test_an_attainment_pct_target_compares_the_percentage_not_the_raw_value():
def test_the_variance_is_signed_so_a_slide_can_show_direction():
def test_a_zero_actual_is_distinguishable_from_a_missing_actual():
def test_equality_counts_as_met_for_both_comparators():
```

**Tests four and eight are the package's reason for existing.** `0` and `None`
must never collapse into the same thing: an abandon rate of 0% and an abandon
rate that cannot be measured are different statements, and only one of them is
true today.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_attainment.py -q`

---

### Task 3: The control-item declaration table and endpoint

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/control_items.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/control_items_router.py`
- Create: their test files

**Interfaces:**
- Consumes: `TargetsStore.resolve`, `evaluate`, the metrics query port, P4's period plumbing.
- Produces: `CONTROL_ITEMS: list[ControlItemSpec]` (fourteen entries, each naming its source view, target key and — where absent — its blocking reason) and `GET /metrics/control-items?from=&to=`.

**Tests first:**

```python
async def test_the_response_always_contains_exactly_fourteen_rows():
async def test_the_nine_supported_rows_populate_from_fixtures():
async def test_the_five_unsupported_rows_report_no_data():
async def test_each_no_data_row_carries_a_human_readable_blocking_reason():
async def test_abandon_rate_reports_no_data_and_not_zero_percent():
async def test_the_response_carries_both_a_month_and_a_ytd_column():
async def test_a_row_whose_target_is_unset_reports_no_target_not_missed():
async def test_item_7_and_item_8_match_the_existing_v_resolution_sla_buckets_output():
async def test_the_endpoint_is_rbac_gated():
```

**Test five is the one to write first and never delete.** It is the specific
false claim this package must not make.

Test eight is the compatibility guard for the two rows that already work.

**Blocking reasons** must be the client-facing sentence, not a code reference —
e.g. *"No call queue is instrumented; abandon rate has no queue to measure
(gap R9)."*

**Verify:** `uv run pytest src/chatbot/features/metrics/test_control_items.py -q`

---

### Task 4: RSA arrival attainment

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_rsa_attainment.py`

**Interfaces:**
- Consumes: `rsa_incidents` (`customer_called_in_time`, `time_arrived_breakdown_area`), P3's `coverage.py`.
- Produces: `v_rsa_arrival_attainment` — median, p90, percentage within target, count, excluded count.

**Tests first:**

```python
def test_the_view_computes_the_percentage_within_sixty_minutes():
def test_rows_with_a_missing_arrival_timestamp_are_excluded_not_counted_as_late():
def test_the_excluded_count_is_reported_alongside_the_percentage():
def test_a_period_with_no_complete_rows_reports_no_data_not_zero_percent():
def test_the_target_threshold_comes_from_the_targets_store_not_a_constant():
def test_median_and_p90_are_both_returned():
```

**Test two matters because `rsa_incidents` is manually entered by design.**
Treating an unfilled arrival time as a late arrival would make the metric
degrade with data-entry backlog rather than with actual RSA performance.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_rsa_attainment.py -q`

---

### Task 5: Per-division working-day leadtime

**Files:**
- Modify: `bigquery_schema.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_leadtime_by_division.py`

**Interfaces:**
- Consumes: `resolution_working_minutes`, the inbox's configured working hours.
- Produces: `v_resolution_leadtime_by_division` in working days, plus attainment against the 4-day target.

**Tests first:**

```python
def test_540_working_minutes_is_one_working_day_for_an_0830_to_1730_inbox():
def test_480_working_minutes_is_one_working_day_for_an_0900_to_1700_inbox():
def test_the_same_minute_count_yields_different_day_counts_for_the_two_inboxes():
def test_the_four_working_day_target_comes_from_the_targets_store():
def test_a_case_with_no_resolution_time_is_excluded_from_the_average():
```

**Test three is the whole point of the task.** If it passes trivially because
both configs produce the same answer, the conversion has been hardcoded.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_leadtime_by_division.py -q`

---

### Task 6: Report schedules

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/report_schedules.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/scheduler.py`
- Create: their test files

**Interfaces:**
- Consumes: a cron expression, a relative period expression, the export renderers.
- Produces: `ReportSchedule(name, cron, period, bundle, formats, recipients, enabled)` and `resolve_period(expr, now) -> PeriodRange`.

**Tests first:**

```python
def test_previous_month_fired_on_1_july_resolves_to_the_whole_of_june():
def test_previous_week_fired_on_a_monday_resolves_to_the_prior_week():
def test_mtd_resolves_from_the_first_of_the_month_to_now():
def test_ytd_resolves_from_1_january_to_now():
def test_an_unknown_period_expression_is_rejected_at_write_time():
async def test_each_schedule_mails_only_its_own_recipients():
async def test_a_disabled_schedule_never_fires():
async def test_with_no_schedules_the_legacy_interval_path_is_unchanged():
async def test_a_schedule_whose_period_yields_no_data_still_sends_with_an_empty_state_note():
```

**Test one is the requirement**, stated as a test: "email the June monthly
report on 1 July" must be expressible, and it is expressible only because the
period is resolved at fire time rather than stored.

Test eight is the compatibility guarantee for every tenant that never opens the
admin page. Test nine prevents a silent failure — a scheduled report that finds
no data should arrive saying so, not not arrive.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_report_schedules.py -q`

---

### Task 7: Per-view XLSX and PDF, period-aware

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/export.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/export_router.py`
- Modify: their test files

**Interfaces:**
- Consumes: the same row-list shape `render_csv` already accepts.
- Produces: `render_xlsx(rows, *, title)` / `render_pdf(rows, *, title)` generalised beyond `DashboardMetrics`; export routes accept `?from=&to=`.

**Tests first:**

```python
def test_render_xlsx_accepts_an_arbitrary_row_list():
def test_render_pdf_accepts_an_arbitrary_row_list():
def test_the_dashboard_bundle_output_is_unchanged():
async def test_each_of_the_five_per_view_reports_exports_as_xlsx():
async def test_each_of_the_five_per_view_reports_exports_as_pdf():
async def test_an_export_route_honours_a_period_filter():
async def test_an_export_of_an_empty_result_produces_a_valid_file_with_a_header_row():
```

**Test three is the regression guard** — the dashboard bundle is what the
current scheduled email sends, and its bytes should not change because the
renderer was generalised.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_export.py src/chatbot/features/metrics/test_export_router.py -q`

---

### Task 8: Targets admin page

**Files:**
- Create: `backend/.../features/metrics/targets_router.py`
- Create: `deploy/chatwoot-fork/patches/00NN-targets-admin.patch`
- Modify: the permission registry — add `targets.manage`

**Tests first:**

```python
async def test_list_returns_every_target_with_its_scope():
async def test_create_rejects_an_unknown_unit():
async def test_update_preserves_the_seeded_targets_when_editing_another():
async def test_delete_falls_back_to_the_seeded_value_rather_than_to_no_target():
async def test_an_unauthorised_caller_is_rejected():
async def test_the_permission_appears_in_the_permission_registry():
```

**Test four:** deleting an operator's override of a seeded target should restore
the seed, not leave the metric target-less. Otherwise a delete silently turns a
measured row into `no_target`.

**Fork-patch note:** reconstruct from the shape of the SLA Policies admin patch
(`0025`/`0047`) — the closest existing analogue. Build via Cloud Build for
`amd64`; never on the prod VM, never from an arm64 Mac.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_targets_router.py -q`

---

### Task 9: Flags, env, and the control-item caveat note

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`
- Modify: `README.md`

**Tests first:**

```python
def test_the_three_settings_are_present_in_example_env():
def test_all_three_default_to_false():
def test_the_service_starts_with_none_of_them_set():
```

**The note (the deliverable), for whoever presents this slide:**

> Nine of the fourteen control items render from real data. Five report
> **"not measured"** with a stated reason: items 4, 5, 10 and 12 need call-queue
> instrumentation that does not exist (gap R9, 4–6 weeks), and item 13 needs a
> social inbox that cannot be connected until Meta Business verification
> completes. These rows are deliberately blank rather than zero. **A zero would
> be a claim about performance; a blank is a statement about instrumentation.**

**Verify:** full suite green with flags off, then on.

---

## Definition of done

- [ ] All three flags off → suite green, behaviour identical to `d85f0d4`.
- [ ] `no_data` provably never renders as `missed`, and abandon rate never renders as 0%.
- [ ] Fourteen rows always; nine populated; five carrying client-facing reasons.
- [ ] Items 7 and 8 byte-identical to their current output.
- [ ] RSA attainment excludes and counts incomplete rows.
- [ ] Working-day conversion differs between two differently-configured inboxes.
- [ ] "June report on 1 July" expressible and tested against a frozen clock.
- [ ] All five per-view reports export as CSV, XLSX and PDF, period-aware.
- [ ] Nothing merged to `main`.
