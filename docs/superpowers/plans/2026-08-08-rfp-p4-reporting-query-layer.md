# P4 — Reporting Query Layer: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every metrics endpoint answer the question it was asked — for the date range requested, in the tenant's timezone, filtered to the agent/team/channel requested — instead of returning all-time UTC-bucketed totals under a week header.

**Architecture:** Add the date columns that `reject_period` was written to protect against, then **delete `reject_period`**. Thread a reporting timezone through `view_ddls()` exactly as the existing code comment at `bigquery_schema.py:190-220` prescribes, defaulted to UTC so an unmodified tenant sees no change. Filters follow the `PeriodQuery` dependency pattern already in the codebase.

**Tech Stack:** Python 3.12, FastAPI dependencies, BigQuery view DDLs + parameterised queries, Power BI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p4-reporting-query-layer-design.md`

## Global Constraints

- **P3 must land first.** It adds columns these views group by.
- **`REPORTING_TIMEZONE` defaults to `UTC` and the default must be the identity transform** — with it unset, `view_ddls()` output is byte-identical to today's, character for character. Task 3 asserts this on the DDL strings.
- **Never string-interpolate a filter value into SQL.** These arrive from a query string. The adapter already parameterises the period; follow it.
- **An unsupported filter is a 400, never silently ignored.** Same principle `reject_period` encodes: an ignored parameter is a lie with a header on it.
- **Do not switch a live tenant's timezone as part of this work.** Ship the capability, ship the comparison script, leave the switch to a deliberate operator change with the comparison output attached.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/metrics/bigquery_schema.py` | **Modify.** Date columns on five views; `reporting_timezone` parameter; three new views |
| `backend/.../features/metrics/query_adapter.py` | **Modify.** Filter predicates, parameterised |
| `backend/.../features/metrics/filter_query.py` | **New.** The `MetricFilters` dependency |
| `backend/.../features/metrics/period_query.py` | **Modify.** Delete `reject_period` (last task) |
| `backend/.../features/metrics/insights_router.py` | **Modify.** Accept period + filters on every route |
| `agent/app/services/sync.py` | **Modify.** Implement `record_conversation_status` |
| `scripts/compare-reporting-timezone.py` | **New.** Pre-switch delta report |
| `features/metrics/docs/proton-crm.pbix` | **New.** The real Power BI artefact |

---

### Task 1: Date columns on the five dateless views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`

**Interfaces:**
- Consumes: `created_at`, `resolved_at`, `dealer_escalated_at` — all existing columns.
- Produces: a `day` column on `v_dept_pic_performance`, `v_dealer_escalation`, `v_resolution_sla_buckets`, `v_case_aging`, `v_callcenter`. Task 2 filters on it.

**Tests first:**

```python
def test_all_five_views_now_expose_a_day_column():
def test_v_dealer_escalation_keys_on_dealer_escalated_at_not_created_at():
def test_v_resolution_sla_buckets_keys_on_resolved_at():
def test_a_case_created_in_may_and_escalated_in_june_appears_in_junes_dealer_rows():
def test_an_unescalated_case_produces_no_dealer_escalation_row():
def test_every_column_referenced_by_the_modified_views_exists_in_the_schema():
def test_the_existing_aggregate_shape_of_each_view_is_preserved():
```

**The fourth test encodes the design decision** that a dealer's clock starts when the escalation reaches them. Write it as a named behaviour, because someone will read the June dealer count, notice it does not sum to June's case count, and file it as a bug.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/metrics/test_bigquery_schema.py -q`

---

### Task 2: Period support on the five endpoints

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/query_adapter.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/insights_router.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_dashboard_router_period.py`

**Interfaces:**
- Consumes: task 1's `day` columns, the existing `PeriodQuery` dependency and `_day_grain_block_for_period`.
- Produces: all eight `/metrics/*` routes honouring `?from=&to=&granularity=`.

**Tests first:**

```python
async def test_every_metrics_endpoint_accepts_a_period_range():
async def test_each_endpoint_returns_different_results_for_two_different_ranges():
async def test_an_endpoint_with_no_data_in_range_returns_empty_not_all_time():
async def test_previous_period_comparison_works_on_the_newly_enabled_endpoints():
async def test_granularity_week_and_month_bucket_correctly_on_each_endpoint():
async def test_an_invalid_range_still_400s_with_the_existing_message():
```

**The second test is the one that would have caught the original defect.** An endpoint that ignores the range passes "accepts a period range" and fails "returns different results for different ranges". Write it that way.

**Verify:** `uv run pytest src/chatbot/features/metrics/ -q`

---

### Task 3: Reporting timezone

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `backend/.../platform/config.py`, `deploy/tenants/example.env`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_bigquery_schema_timezone.py`

**Interfaces:**
- Consumes: `settings.reporting_timezone`.
- Produces: `view_ddls(..., reporting_timezone: str = "UTC")`.

**Tests first:**

```python
def test_the_default_produces_ddl_byte_identical_to_the_previous_implementation():
def test_every_date_call_in_every_view_threads_the_timezone():
def test_a_2300_myt_case_buckets_to_the_next_day_under_asia_kuala_lumpur():
def test_the_same_case_buckets_to_the_current_day_under_utc():
def test_an_unknown_timezone_is_rejected_at_startup_not_at_query_time():
def test_no_view_hardcodes_a_timezone_string():
```

**The first test is the safety argument for the whole task** and must be written first: capture today's `view_ddls()` output as a fixture and assert the defaulted call reproduces it exactly. The sixth test is the guard that a future view will not quietly bypass the parameter.

**Preserve the explanatory comment block at lines 190-220.** Update it to say the fix is now available and defaulted off, rather than deleting it — it is the clearest statement of the failure mode anywhere in the repo, and the reconciliation meeting will want it.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_bigquery_schema_timezone.py -q`

---

### Task 4: The timezone comparison script

**Files:**
- Create: `scripts/compare-reporting-timezone.py`
- Create: its test

**Interfaces:**
- Consumes: a tenant's dataset, a window, two timezone values.
- Produces: a per-bucket delta table — how many cases move, and between which buckets.

**Tests first:**

```python
def test_identical_timezones_report_zero_movement():
def test_a_utc_to_myt_comparison_reports_the_cases_that_move_forward():
def test_the_output_names_both_the_source_and_destination_bucket():
def test_the_script_is_read_only_and_creates_no_views():
def test_a_summary_line_states_the_total_percentage_of_rows_affected():
```

**The fourth test is a safety property.** This script is run against a production dataset by an operator deciding whether to switch; it must not be able to modify anything.

**Verify:** dry-run against a scratch dataset.

---

### Task 5: Filters

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/filter_query.py`
- Create: its test file
- Modify: `query_adapter.py`, `insights_router.py`, `dashboard_router.py`

**Interfaces:**
- Consumes: query-string params `agent_id`, `team`, `department`, `channel`, `dealer`.
- Produces: a `MetricFilters` FastAPI dependency and parameterised `WHERE` predicates.

**Tests first:**

```python
async def test_an_agent_filter_narrows_the_result_set():
async def test_a_channel_filter_narrows_the_result_set():
async def test_two_filters_compose_as_an_and():
async def test_a_filter_on_a_view_that_lacks_the_column_returns_400_naming_both():
async def test_filter_values_are_passed_as_query_parameters_not_interpolated():
async def test_a_filter_value_containing_sql_syntax_is_harmless():
async def test_previous_period_applies_the_same_filters_to_both_windows():
async def test_the_flag_off_treats_filter_params_as_unknown_and_400s():
```

**Tests five and six are the security pair.** Assert the *parameter binding*, not the rendered SQL — a test that greps the SQL string for the value would pass on an interpolated query that happened to escape correctly.

The seventh test prevents a subtle wrongness: an unfiltered previous-period baseline makes every week-over-week delta for a filtered view meaningless.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_filter_query.py -q`

---

### Task 6: The reopen event

**Files:**
- Modify: `agent/app/services/sync.py` — implement `record_conversation_status`
- Create: `agent/tests/test_reopen_event.py`
- Modify: `backend/.../features/metrics/bigquery_schema.py` — coverage note on `v_reopen_rate`

**Interfaces:**
- Consumes: the Chatwoot `conversation_status_changed` webhook payload.
- Produces: `reopen_count` (int) and `last_reopened_at` custom attributes.

**Tests first:**

```python
async def test_a_resolved_to_open_transition_increments_reopen_count():
async def test_a_resolved_to_pending_transition_increments_reopen_count():
async def test_an_open_to_pending_transition_does_not_increment():
async def test_an_open_to_resolved_transition_does_not_increment():
async def test_a_second_reopen_increments_to_two():
async def test_a_duplicate_webhook_delivery_does_not_double_count():
async def test_a_chatwoot_api_failure_is_logged_and_swallowed():
async def test_the_flag_off_leaves_the_stub_a_no_op():
```

**Implementation note:** the stub already exists as the router's dispatch target and CLAUDE.md describes it as being kept "so a future Chatwoot-side integration has a place to hook in". This is that integration. Keep the docstring's explanation of why the stub existed; extend it rather than replacing it.

Duplicate deliveries are already handled upstream by `claim_delivery`, but the sixth test asserts it end-to-end because a double-counted reopen rate is a metric that quietly inflates.

**Verify:** `cd agent && pytest tests/test_reopen_event.py -q && pytest -q`

---

### Task 7: Per-dealer first response and the attainment rate

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: its test file

**Interfaces:**
- Consumes: `first_response_working_minutes` (stored today, read by nothing — P1 gives it its first reader, this is its second), `dealer`.
- Produces: `v_first_response_by_dealer`, and an attainment-rate expression taking the threshold as a parameter (P5 supplies it from the targets store).

**Tests first:**

```python
def test_v_first_response_by_dealer_appears_in_view_ddls():
def test_the_attainment_rate_is_the_percentage_meeting_the_threshold():
def test_a_threshold_of_120_working_minutes_matches_the_two_working_hour_target():
def test_cases_with_no_first_response_are_excluded_from_the_rate_not_counted_as_failures():
def test_the_denominator_is_reported_alongside_the_percentage():
```

**The fourth test is a real decision, not a detail.** An open case with no first response yet has not missed the target — it has not answered it. Counting it as a failure makes the rate drop as volume rises. The fifth test exists because a 100% attainment rate over 3 cases and over 3,000 are different statements.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_bigquery_schema.py -q`

---

### Task 8: Tag-keyword report (§4.80)

**Files:**
- Modify: `bigquery_schema.py` — `v_volume_by_tag`
- Modify: `insights_router.py` — `GET /metrics/by-tag`
- Create: its test file

**Tests first:**

```python
def test_v_volume_by_tag_unnests_the_labels_column():
async def test_the_endpoint_filters_to_a_single_tag():
async def test_the_endpoint_accepts_a_period_and_the_standard_filters():
async def test_a_tag_that_matches_nothing_returns_an_empty_list_not_404():
async def test_a_case_with_three_labels_appears_under_each_of_them():
async def test_the_total_across_tags_exceeds_the_case_count_and_the_response_says_so():
```

**The last test is the honesty guard.** Tag counts double-count by construction — a case with three labels is in three buckets — and a slide that sums them into a total will be wrong. The response carries a note saying so.

**Verify:** `uv run pytest src/chatbot/features/metrics/ -q`

---

### Task 9: Delete `reject_period`

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/period_query.py`
- Modify: `insights_router.py` — remove the five call sites
- Modify: `test_bigquery_schema.py` — add the replacement guard

**Interfaces:**
- Produces: a schema-level test replacing the runtime guard.

**Tests first:**

```python
def test_every_view_backing_a_period_capable_endpoint_has_a_day_column():
def test_reject_period_is_no_longer_referenced_anywhere():
async def test_no_endpoint_returns_all_time_data_when_given_a_range():
```

**Why this is a task and not a cleanup:** `reject_period` prevented a real defect. Removing it without replacing the protection invites the next view to be added dateless and re-enter the same failure silently. The first test is the replacement — it fails at the schema level, at build time, instead of at runtime with a 400.

**Verify:** `uv run pytest src/chatbot/features/metrics/ -q`

---

### Task 10: Power BI artefact (§4.55)

**Files:**
- Create: `backend/.../features/metrics/docs/proton-crm.pbix`
- Modify: `backend/.../features/metrics/docs/power-bi-runbook.md`

**Deliverable:** a `.pbix` binding the 19 views, DirectQuery configured, service-account auth, documented refresh schedule, and the five cuts §4.55 names (channel, division, trend, PIC, CRR, SLA) as pages.

**Verification is manual and must be recorded:** open the file, confirm every page renders against a real dataset, confirm refresh succeeds under the service account, screenshot each page into the runbook. This item was raised by the client at the 2026-07-28 demo (feedback item 5) and a claim that it is done needs evidence attached.

The runbook is **demoted, not deleted** — retitle it "Rebuilding the Power BI artefact".

---

### Task 11: Flags, env, and the timezone runbook step

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`
- Modify: `README.md`

**Tests first:**

```python
def test_reporting_timezone_defaults_to_utc_in_example_env():
def test_metrics_filters_enabled_and_reopen_tracking_enabled_default_to_false():
def test_the_service_starts_with_none_of_the_new_vars_set():
```

**The runbook step (the deliverable):**

> **Before changing `REPORTING_TIMEZONE` on a live tenant**, run
> `scripts/compare-reporting-timezone.py` for the tenant and a representative
> window, and attach its output to the change record. Switching from `UTC` to
> `Asia/Kuala_Lumpur` re-buckets **every historical figure on every dashboard**
> in a single deploy: totals stay correct, but cases move between adjacent
> days, weeks and months. Schedule the switch at a month boundary so the seam
> in any published series falls at a natural break, and tell the reporting team
> before, not after.

**Verify:** full suite green with defaults, then green with `REPORTING_TIMEZONE=Asia/Kuala_Lumpur` and both flags on.

---

## Definition of done

- [ ] All eight `/metrics/*` endpoints honour a date range, and each returns different data for different ranges.
- [ ] `reject_period` deleted, replaced by a schema-level test.
- [ ] `view_ddls()` with the default produces byte-identical DDL to `d85f0d4`.
- [ ] Comparison script run against a scratch dataset and its output reviewed.
- [ ] Filters parameterised, composable, and 400 when unsupported.
- [ ] Reopen events emitted; `v_reopen_rate` carries a coverage note.
- [ ] Per-dealer FRT view with an attainment rate that excludes unanswered cases.
- [ ] `.pbix` opens, refreshes, and every page is screenshotted into the runbook.
- [ ] Nothing merged to `main`.
