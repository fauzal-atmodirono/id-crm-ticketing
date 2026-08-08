# P3 — Case Record Extensions & Case-State Warehouse Sync: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the columns PRO-NET's own report decks print actually exist — and stop discarding the two the system already captures (`case_detail`, `case_state`).

**Architecture:** New case fields are Chatwoot conversation custom attributes, typed in exactly one place (`mapping.py`), because every consumer in this system already reads custom attributes. `case_state` becomes a **new** warehouse column beside `status`, never a redefinition of it — that is what keeps every existing view honest.

**Tech Stack:** Python 3.12, BigQuery view DDLs, the Chatwoot API + a fork patch for the entry panel, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p3-case-record-extensions-design.md`

## Global Constraints

- **Never overwrite the `status` column.** Four existing views read it. `case_state` is additive.
- **Absent means NULL, not a default.** Every row synced before this package has none of these attributes; mapping them to `OPEN`, or to `False`, fabricates history. Asserted in task 2.
- **No `hq` classifier until Q5 is answered.** Ship the `escalated_to` dimension with `dealer`/`none` only. A plausible wrong number is worse than a truthful zero.
- **`purchased_from_dealer` is a validated dealer slug, never free text.** Free text fragments the dealer dimension within days.
- **Coverage disclosure defaults ON.** A view built on sparse agent-entered data must say how sparse.
- Env vars go in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/case_fields.py` | **New.** The attribute names, their types, validation |
| `backend/.../features/chat/test_case_fields.py` | Its tests |
| `backend/.../features/metrics/mapping.py` | **Modify.** The single typing point for all new attributes |
| `backend/.../features/metrics/bigquery_schema.py` | **Modify.** Nine columns, three views |
| `backend/.../features/metrics/coverage.py` | **New.** Coverage percentage helper |
| `backend/.../features/chat/case_fields_router.py` | **New.** Read/write endpoints for the fork panel |
| `deploy/chatwoot-fork/patches/0051-case-detail-panel.patch` | **New.** The conversation-side entry panel |
| `deploy/tenants/example.env` | **Modify.** Three settings |

---

### Task 1: The field definitions (pure)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/case_fields.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_case_fields.py`

**Interfaces:**
- Consumes: `DealerStore` (for slug validation only).
- Produces: `CASE_FIELDS: dict[str, FieldSpec]` naming each attribute, its type and its validator; `validate(name, value) -> str | None` returning a normalised value or raising `InvalidCaseField`. Tasks 2 and 5 both read this — it is the single source of truth for the field set.

**Tests first:**

```python
def test_every_field_in_the_spec_has_a_name_a_type_and_a_validator():
def test_a_plate_number_is_normalised_to_upper_case_without_spaces():
def test_a_chassis_number_is_normalised_to_upper_case():
async def test_a_purchased_from_dealer_slug_that_exists_is_accepted():
async def test_a_purchased_from_dealer_slug_that_does_not_exist_is_rejected():
async def test_the_rejection_names_the_unknown_slug_so_the_operator_can_fix_it():
def test_a_free_text_wip_field_accepts_any_string_within_the_length_cap():
def test_an_over_long_value_is_rejected_rather_than_silently_truncated():
def test_escalated_to_accepts_dealer_and_none_but_not_hq():
```

**The last test is a deliberate, temporary constraint** and its name says so. `hq` is rejected until Q5 is answered; when it is answered, this test changes and the reviewer knows exactly why.

Plate normalisation matters more than it looks: `WXY 1234`, `wxy1234` and `WXY-1234` are one car, and if they enter the warehouse as three the vehicle dimension is worthless.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_case_fields.py -q`

---

### Task 2: Map the attributes into the warehouse row

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/mapping.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_mapping.py`

**Interfaces:**
- Consumes: `CASE_FIELDS` (task 1), a Chatwoot conversation dict.
- Produces: nine new keys on the mapped row: `case_detail`, `case_state`, `escalated_to`, `vehicle_plate`, `vehicle_chassis`, `purchased_from_dealer`, `delay_reason`, `wip_issue`, `wip_action_taken`, `wip_next_action`.

**Tests first:**

```python
def test_case_detail_is_read_from_custom_attributes():
def test_case_state_is_read_from_the_case_state_attribute():
def test_the_status_column_is_still_the_chatwoot_status_not_the_case_state():
def test_a_conversation_with_no_new_attributes_maps_every_one_to_none():
def test_absent_attributes_map_to_none_not_to_empty_string():
def test_a_malformed_attribute_value_maps_to_none_and_logs():
def test_escalated_to_is_derived_as_dealer_when_a_dealer_label_is_present():
def test_escalated_to_is_none_when_no_dealer_label_is_present():
def test_every_existing_mapping_test_still_passes_unchanged():
```

**The third test is the load-bearing one** — it asserts the design decision that `status` keeps its meaning. The ninth is the regression sweep; run the whole existing mapping suite, not just the new cases.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_mapping.py -q`

---

### Task 3: Schema columns

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`

**Interfaces:**
- Consumes: task 2's row keys.
- Produces: nine `NULLABLE` STRING columns on `CONVERSATIONS_SCHEMA`.

**Tests first:**

```python
def test_all_nine_columns_are_present_in_conversations_schema():
def test_all_nine_columns_are_nullable():
def test_no_existing_column_changed_type_or_mode():
def test_a_historical_row_without_the_new_fields_still_validates_against_the_schema():
```

**Implementation note:** every one of these is NULLABLE and that is not incidental — the sync loads historical rows on every run, and a REQUIRED column would fail the whole load job on the first old conversation.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_bigquery_schema.py -q`

---

### Task 4: Coverage disclosure

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/coverage.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_coverage.py`

**Interfaces:**
- Consumes: a list of rows and a field name.
- Produces: `coverage_pct(rows, field) -> float` and a `CoverageNote` shape the report endpoints attach to any response grouped by a sparse field.

**Tests first:**

```python
def test_three_of_five_populated_reports_sixty_percent():
def test_an_empty_row_set_reports_none_not_zero():
def test_empty_strings_count_as_unpopulated():
def test_whitespace_only_values_count_as_unpopulated():
def test_the_note_names_the_field_so_the_slide_can_caption_it():
```

**The second test matters:** 0% coverage and "no cases at all" are different statements, and a slide that prints "0% have a plate number" when there were no cases is a wrong statement.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_coverage.py -q`

---

### Task 5: The entry panel (fork patch + endpoints)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/case_fields_router.py`
- Create: its test file
- Create: `deploy/chatwoot-fork/patches/0051-case-detail-panel.patch`

**Interfaces:**
- Consumes: `CASE_FIELDS` (task 1) — the panel renders from the spec, so adding a field later is a one-line change in one file.
- Produces: `GET /cases/{conv_id}/fields`, `PATCH /cases/{conv_id}/fields`, RBAC-gated on an existing permission.

**Tests first:**

```python
async def test_get_returns_every_field_with_its_current_value():
async def test_patch_writes_only_the_fields_supplied():
async def test_patch_rejects_an_unknown_field_name():
async def test_patch_rejects_an_invalid_dealer_slug_with_a_usable_message():
async def test_patch_normalises_a_plate_number_before_storing():
async def test_an_unauthorised_caller_is_rejected():
async def test_the_flag_off_returns_404_so_the_panel_does_not_render():
```

**Fork-patch note:** per `docs/superpowers/.../chatwoot-fork-patch-network-restriction`, this sandbox cannot clone upstream to generate a patch. Reconstruct the patch from the structure of an existing conversation-sidebar patch (`0041` is the closest shape) rather than attempting a clone. Build off-VM for `amd64` via Cloud Build — never on the prod VM, and never from an arm64 Mac.

**Verify:** `uv run pytest src/chatbot/features/chat/test_case_fields_router.py -q`

---

### Task 6: `case_detail` in the reporting views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`

**Interfaces:**
- Consumes: the `case_detail` column from task 3.
- Produces: `v_category_by_vehicle_model` extended with `case_detail`; new `v_concern_pivot` with Level-1 → Level-2 nesting and grand totals.

**Tests first:**

```python
def test_v_category_by_vehicle_model_now_groups_by_case_detail():
def test_the_extended_view_still_returns_the_previous_grouping_when_case_detail_is_null():
def test_v_concern_pivot_appears_in_view_ddls():
def test_v_concern_pivot_references_only_columns_that_exist():
def test_v_concern_pivot_includes_a_grand_total_row():
def test_rows_with_a_null_case_detail_are_bucketed_as_unspecified_not_dropped():
```

**The last test is the one that decides whether the slide reconciles.** Dropping null-`case_detail` rows would make the pivot's total disagree with the headline count — which is precisely the C2 p1-vs-division discrepancy (297 vs 264) the analysis flagged as question Q8. Do not create a second instance of that problem.

**Verify:** `uv run pytest src/chatbot/features/metrics/ -q`

---

### Task 7: `case_state` trend and the real WIP count

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/insights_router.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_case_state_sync.py`

**Interfaces:**
- Consumes: the `case_state` and `escalated_to` columns.
- Produces: `v_case_state_trend` (four series: higher-escalation / WIP / temp-closed / closed), `v_case_aging` extended with `case_state`.

**Tests first:**

```python
def test_v_case_state_trend_appears_in_view_ddls():
def test_the_trend_reports_wip_and_temp_closed_as_distinct_series():
def test_v_state_trend_is_unchanged_and_still_reads_the_chatwoot_status():
def test_v_case_aging_gains_case_state_without_changing_its_existing_buckets():
def test_a_null_case_state_is_reported_as_unknown_not_folded_into_open():
async def test_the_wip_count_endpoint_prefers_case_state_and_falls_back_to_the_proxy():
```

**The last test defines the migration behaviour:** with `CASE_STATE_SYNC_ENABLED` off, or on rows predating it, the WIP figure falls back to the open+pending proxy that is used today, and **the response says which one it used**. A number that silently switches definitions mid-series is worse than either definition.

**Verify:** `uv run pytest src/chatbot/features/metrics/ -q`

---

### Task 8: Flags, env, and the Q3/Q5 note

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`
- Modify: `docs/analysis/2026-08-08-rfp-2026_028-gap-analysis.md` — append a
  cross-reference to this package under C1-07 and C2-09

**Tests first:**

```python
def test_the_three_settings_are_present_in_example_env():
def test_report_coverage_disclosure_defaults_to_true():
def test_the_other_two_default_to_false():
```

**The note (the deliverable):**

> `escalated_to` ships with values `dealer` and `none` only. Nothing classifies
> a case as `hq` because "escalated to HQ" is not defined in the case model —
> client question Q5. C1-07's HQ column will therefore report zero until that
> answer arrives, which is correct: the system has never recorded an HQ
> escalation. The column exists so that answering Q5 is a classifier change, not
> a schema migration.
>
> The WIP remarks are three free-text fields (Q3). If PRO-NET answers that they
> want them generated or templated, the schema does not change — only what
> writes them.

**Verify:** `uv run pytest -q` in both services.

---

## Definition of done

- [ ] All three flags off → suites green, behaviour identical to `d85f0d4`.
- [ ] `status` provably unchanged; `case_state` populated independently.
- [ ] Nine nullable columns; a historical row with none of them still loads.
- [ ] `case_detail` reaches the warehouse and the Level-2 pivot reconciles to the headline total.
- [ ] Plate numbers normalised; dealer slugs validated.
- [ ] Nothing classified `hq`.
- [ ] Coverage percentages emitted on every view grouped by a sparse field.
- [ ] Nothing merged to `main`.
