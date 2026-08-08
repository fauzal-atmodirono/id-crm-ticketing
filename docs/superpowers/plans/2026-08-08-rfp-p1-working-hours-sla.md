# P1 — Working-Hours SLA Enforcement & After-Hours Instrumentation: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SLA enforcement and SLA reporting agree with each other by putting both on the working-hours clock, and stop discarding the in-hours flag the system already computes.

**Architecture:** One clock module (`sla_clock.py`) that every threshold comparison goes through, with a `working_hours=False` path that reproduces today's wall-clock arithmetic to the second. That equivalence is what makes the change safe to ship dark. The existing `features/metrics/business_hours.py::working_minutes_between` does the arithmetic — this plan adds no second calendar.

**Tech Stack:** Python 3.12, FastAPI, Firestore (audit + policy store), BigQuery view DDLs, pytest with `asyncio_mode=auto`.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p1-working-hours-sla-design.md`

## Global Constraints

- **Every flag defaults off, and with all four off the system must behave byte-identically to today.** This engine drives live SLA breach alerts on a production tenant; a regression here pages a real PIC at 3 a.m.
- **Do not add a second working-hours implementation.** `working_minutes_between` is the only one. `next_working_instant` goes in the same module, shares its fixtures, and walks the same calendar.
- **Fail open, everywhere.** An inbox fetch failure returns `{}`, which falls through to calendar minutes. An SLA scan must never raise because working hours could not be resolved.
- **Never overwrite an intake stamp.** `received_in_business_hours` is a fact about arrival, not a derivation; re-computing it later would answer a different question.
- **Env vars go in three places:** `backend/.../platform/config.py` (or `agent/app/config.py`), `deploy/tenants/example.env`, and `agent/tests/conftest.py` where import-time presence is required.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/sla_clock.py` | **New.** `elapsed_minutes`, `InboxCache`; the single entry point for every threshold comparison |
| `backend/.../features/chat/test_sla_clock.py` | Its tests |
| `backend/.../features/metrics/business_hours.py` | **Modify.** Add `next_working_instant` beside `working_minutes_between` |
| `backend/.../features/metrics/test_business_hours.py` | Extend |
| `backend/.../features/chat/sla.py` | **Modify.** Replace `_conversation_age_seconds` with `elapsed_minutes`; add `_has_acknowledgement` |
| `backend/.../features/chat/test_sla_working_hours.py` | **New.** The golden Friday-18:00 case |
| `backend/.../features/chat/sla_policy_repository.py` | **Modify.** Add `working_hours_enabled: bool \| None` |
| `backend/.../features/chat/case_state.py` / audit states | **Modify.** Add `ACKNOWLEDGED` |
| `backend/.../features/chat/escalation_replies.py` | **Modify.** Record `ACKNOWLEDGED` on a linked PIC/dealer reply |
| `agent/app/services/sync.py` | **Modify.** Add `maybe_stamp_business_hours` |
| `agent/tests/test_sync_business_hours_stamp.py` | **New** |
| `agent/app/routers/chatwoot.py` | **Modify.** Dispatch the new background task |
| `backend/.../features/metrics/mapping.py` | **Modify.** Carry the two new attributes into the row |
| `backend/.../features/metrics/bigquery_schema.py` | **Modify.** Two columns, two views |
| `deploy/scripts/provision-after-hours-replies.py` | **New.** Appendix B wording per inbox |
| `deploy/tenants/example.env` | **Modify.** Four flags, documented |

---

### Task 1: `next_working_instant` beside the existing calendar walk

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/business_hours.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_business_hours.py`

**Interfaces:**
- Consumes: the same Chatwoot inbox dict shape `working_minutes_between` reads (`working_hours_enabled`, `timezone`, `working_hours` rows with `day_of_week` 0=Sunday..6=Saturday).
- Produces: `next_working_instant(after: datetime, inbox: dict) -> datetime`. Task 5 stamps its result as `attend_after`.

**Tests first:**

```python
def test_an_instant_already_inside_working_hours_is_returned_unchanged():
def test_a_friday_evening_instant_moves_to_monday_opening():
def test_a_saturday_instant_moves_to_the_saturday_opening_when_saturday_is_open():
def test_an_inbox_with_working_hours_disabled_returns_the_instant_unchanged():
def test_an_inbox_with_no_working_hours_rows_returns_the_instant_unchanged():
def test_a_closed_all_day_row_is_skipped_to_the_next_open_day():
def test_an_open_all_day_row_returns_the_instant_unchanged():
def test_the_result_is_computed_in_the_inbox_timezone_not_utc():
def test_an_unknown_timezone_falls_back_to_utc_without_raising():
```

**Implementation notes:** walk forward day by day exactly as `working_minutes_between` does, capped at 14 days to avoid an infinite loop on a pathological all-closed config; on hitting the cap, return `after` unchanged and log at debug (fail open — a case that is never "attendable" must not be a case that is never enforced).

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/metrics/test_business_hours.py -q`

---

### Task 2: The clock module

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/sla_clock.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_sla_clock.py`

**Interfaces:**
- Consumes: `working_minutes_between` (task 1's module), the Chatwoot inbox API.
- Produces: `elapsed_minutes(start, now, inbox, *, working_hours: bool) -> float` and `InboxCache` with `async get(inbox_id) -> dict`. Task 3 is the only caller.

**Tests first:**

```python
def test_working_hours_false_returns_plain_calendar_minutes():
def test_working_hours_false_matches_the_old_age_seconds_arithmetic_exactly():
def test_working_hours_true_excludes_a_weekend():
def test_working_hours_true_on_an_inbox_with_no_config_equals_calendar_minutes():
def test_end_before_start_is_zero_not_negative():
async def test_inbox_cache_fetches_each_inbox_once_per_instance():
async def test_inbox_cache_returns_empty_dict_when_the_fetch_raises():
async def test_inbox_cache_returns_empty_dict_for_a_none_inbox_id():
async def test_two_cache_instances_do_not_share_state():
```

**The second test is the load-bearing one.** It asserts `elapsed_minutes(..., working_hours=False) * 60` equals the value `_conversation_age_seconds` returns for the same input. That equivalence is the entire safety argument for shipping this dark — write it before touching `sla.py`.

**Verify:** `uv run pytest src/chatbot/features/chat/test_sla_clock.py -q`

---

### Task 3: Route SLA enforcement through the clock

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_sla_working_hours.py`

**Interfaces:**
- Consumes: `sla_clock.elapsed_minutes`, `sla_clock.InboxCache`, `settings.sla_working_hours_enabled`.
- Produces: `scan_conversations(..., inbox_cache: InboxCache | None = None)`. No caller is required to pass it; `None` means construct one per scan.

**Tests first:**

```python
async def test_with_the_flag_off_every_existing_sla_test_still_passes():   # regression sweep
async def test_a_friday_1800_arrival_does_not_breach_a_2_working_hour_target_at_2000():
async def test_a_friday_1800_arrival_breaches_that_target_on_monday_at_1000():
async def test_a_weekend_spanning_case_accrues_zero_working_minutes():
async def test_an_inbox_with_no_working_hours_behaves_identically_flag_on_or_off():
async def test_the_inbox_is_fetched_once_per_scan_not_once_per_conversation():
async def test_an_inbox_fetch_failure_falls_back_to_wall_clock_and_does_not_raise():
async def test_per_channel_ack_minutes_are_interpreted_as_working_minutes_when_enabled():
async def test_a_per_conversation_sla_minutes_label_is_interpreted_as_working_minutes():
async def test_tier2_escalation_hours_use_the_same_clock():
```

**Implementation notes:**
- Replace the body of `_conversation_age_seconds` rather than its call sites, so the diff stays small and the wall-clock path is provably unchanged: it becomes a thin wrapper that calls `elapsed_minutes` and multiplies by 60.
- Construct the `InboxCache` once at the top of `scan_conversations`, before the conversation loop. The fourth test asserts this; a per-conversation fetch is a ~100× API amplification on a real tenant.
- The `age > threshold` comparisons keep their shape. Only the source of `age` changes.

**Verify:** `uv run pytest src/chatbot/features/chat/ -q` — the whole chat suite, not just the new file. The first test in the list is a regression sweep and must pass before any other work in this task is considered done.

---

### Task 4: Per-inbox `working_hours_enabled` override

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla_policy_repository.py`
- Modify: its test file

**Interfaces:**
- Consumes: nothing new.
- Produces: `ResolvedPolicy.working_hours_enabled: bool | None`, resolved with the same tenant-default-under-inbox-specific merge as the existing fields.

**Tests first:**

```python
async def test_an_unset_working_hours_enabled_inherits_the_global_setting():
async def test_an_inbox_can_opt_in_while_the_global_setting_is_off():
async def test_an_inbox_can_opt_out_while_the_global_setting_is_on():
async def test_every_existing_stored_policy_resolves_unchanged():
```

The last test is the compatibility guard: policies written before this field existed must resolve to `None`, not `False`.

**Verify:** `uv run pytest src/chatbot/features/chat/test_sla_policy_repository.py -q`

---

### Task 5: Stamp the in-hours flag at intake

**Files:**
- Modify: `agent/app/services/sync.py`
- Create: `agent/tests/test_sync_business_hours_stamp.py`
- Modify: `agent/app/routers/chatwoot.py`
- Modify: `agent/app/config.py`, `deploy/tenants/example.env`, `agent/tests/conftest.py`

**Interfaces:**
- Consumes: a Chatwoot `message_created` payload, `business_hours.is_within_business_hours`, `next_working_instant` (task 1) when the scheduling flag is on.
- Produces: custom attributes `received_in_business_hours` (bool), `received_at_local` (ISO-8601 in the inbox timezone), and `attend_after` (ISO-8601) when out of hours. Task 7 reads all three.

**Tests first:**

```python
async def test_the_first_inbound_message_stamps_the_flag():
async def test_an_in_hours_arrival_stamps_true_and_no_attend_after():
async def test_an_out_of_hours_arrival_stamps_false_and_an_attend_after():
async def test_a_second_message_never_overwrites_the_stamp():
async def test_an_outgoing_agent_message_does_not_stamp():
async def test_a_private_note_does_not_stamp():
async def test_a_chatwoot_api_error_is_logged_and_swallowed():
async def test_the_flag_off_writes_nothing():
async def test_received_at_local_is_in_the_inbox_timezone_not_utc():
```

**Implementation notes:**
- Model on `maybe_stamp_dealer_escalation` in the same module: read the conversation, return early if the attribute exists, write once, catch everything.
- Dispatch from the `message_created` handler, not `conversation_updated` — the requirement is about *arrival*, and `conversation_updated` fires on every subsequent label write.
- Note in the module docstring **why** intake is the only correct moment (an operator can edit working hours later; a flag computed at report time answers a different question).

**Verify:** `cd agent && pytest tests/test_sync_business_hours_stamp.py -q && pytest -q`

---

### Task 6: The acknowledgement event

**Files:**
- Modify: the audit state constants module alongside `backend/.../features/chat/case_state.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_replies.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_sla_acknowledgement.py`

**Interfaces:**
- Consumes: the existing `AuditLogPort`.
- Produces: audit state `ACKNOWLEDGED`; `_has_acknowledgement(prior_states) -> bool` in `sla.py`.

**Tests first:**

```python
async def test_an_explicit_acknowledgement_is_recorded_without_an_agent_reply():
async def test_an_agent_reply_still_counts_as_acknowledgement_when_the_flag_is_off():
async def test_a_pic_email_reply_linked_by_the_reply_linker_records_an_acknowledgement():
async def test_acknowledged_and_first_response_are_independent_states():
async def test_a_second_acknowledgement_does_not_append_a_duplicate_entry():
async def test_the_ack_breach_reads_acknowledgement_and_the_update_breach_reads_first_reply():
```

The last test is the requirement: B-WA-14 ("acknowledge within 2 minutes") and B-EM-05 ("update the customer within 4 working hours") must read *different* signals, or the distinction Appendix B draws does not exist in the system.

**Verify:** `uv run pytest src/chatbot/features/chat/test_sla_acknowledgement.py -q`

---

### Task 7: Carry the flags into BigQuery

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/mapping.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `backend/.../features/metrics/test_mapping.py`, `test_bigquery_schema.py`

**Interfaces:**
- Consumes: the custom attributes task 5 writes.
- Produces: `CONVERSATIONS_SCHEMA` columns `received_in_business_hours` (BOOL, NULLABLE) and `received_at_local` (TIMESTAMP, NULLABLE). Task 8 reads them.

**Tests first:**

```python
def test_a_conversation_with_the_attribute_maps_to_the_boolean_column():
def test_a_conversation_without_the_attribute_maps_to_none_not_false():
def test_a_string_true_from_chatwoot_custom_attributes_maps_to_boolean_true():
def test_the_new_columns_are_nullable_so_historical_rows_still_load():
```

**The second and fourth tests matter more than they look.** Every row synced before this package existed has no attribute; mapping absent → `False` would silently classify the entire history as after-hours.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_mapping.py src/chatbot/features/metrics/test_bigquery_schema.py -q`

---

### Task 8: The two views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/insights_router.py`

**Interfaces:**
- Consumes: task 7's columns plus the already-stored `first_response_working_minutes`.
- Produces: `v_first_response_by_hours_split`, `v_volume_after_hours`, and `GET /metrics/after-hours`.

**Tests first:**

```python
def test_both_views_appear_in_view_ddls():
def test_every_column_referenced_by_the_new_views_exists_in_conversations_schema():
def test_the_hours_split_view_reads_first_response_working_minutes():
def test_rows_with_a_null_in_hours_flag_are_bucketed_as_unknown_not_after_hours():
async def test_the_after_hours_endpoint_returns_both_buckets():
async def test_the_after_hours_endpoint_accepts_a_period_range():
```

**Implementation notes:** the second test is a schema-consistency guard the existing suite already has a pattern for — reuse it rather than writing a new one. The fourth reflects task 7's nullability decision: three buckets (in-hours / after-hours / unknown), never two.

`v_volume_after_hours` needs a date column and gets one for free from `created_at`; unlike the views P4 has to fix, this one is new and is built period-capable from the start.

**Verify:** `uv run pytest src/chatbot/features/metrics/ -q`

---

### Task 9: Appendix B after-hours reply provisioning

**Files:**
- Create: `deploy/scripts/provision-after-hours-replies.py`
- Create: `deploy/scripts/appendix-b-after-hours-text.json`
- Create: a test asserting the JSON matches the appendix verbatim

**Interfaces:**
- Consumes: the Chatwoot inbox API, `CHATWOOT_API_TOKEN`.
- Produces: per-inbox out-of-office text set to Appendix B's bilingual wording.

**Tests first:**

```python
def test_the_english_text_matches_appendix_b_verbatim():
def test_the_malay_text_matches_appendix_b_verbatim():
def test_the_script_is_idempotent_when_the_text_is_already_correct():
def test_the_script_reports_but_does_not_modify_an_inbox_with_different_custom_text():
```

The fourth is a safety property: an operator who has deliberately customised the wording should get a warning, not a silent overwrite.

**Verify:** run against a scratch tenant, not `proton`. `--dry-run` must be the default.

---

### Task 10: Flags, docs and the migration note

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`, `agent/app/config.py`
- Modify: `README.md` (deploy runbook section)
- Modify: the SLA Policies admin help text (Chatwoot fork patch)

**The migration note is the deliverable here, not the flags.** It must say, in the operator's language:

> With `SLA_WORKING_HOURS_ENABLED` on, an SLA target of "2 hours" means **2 working hours**, measured against this inbox's configured business hours. Your existing configured targets do not need changing — they were always intended as working-hours targets. A case that arrives at 18:00 Friday will breach a 2-hour target on Monday morning, not on Friday evening.

**Tests first:**

```python
def test_every_new_setting_is_present_in_example_env():
def test_every_new_setting_defaults_to_false():
def test_the_agent_service_starts_with_none_of_the_new_vars_set():
```

**Verify:** `cd agent && pytest -q` and `cd backend/apps/backend && uv run pytest -q`, both green, then a full-suite run with all four flags forced on via env to confirm nothing raises.

---

## Definition of done

- [ ] All four flags off → both suites green and behaviour byte-identical to `d85f0d4`.
- [ ] All four flags on → both suites green.
- [ ] The Friday-18:00 golden case asserted in both directions.
- [ ] `received_in_business_hours` present on new conversations, absent (not `False`) on historical rows.
- [ ] `v_volume_after_hours` and `v_first_response_by_hours_split` in `view_ddls`, referencing only real columns.
- [ ] The migration note is in the runbook.
- [ ] Nothing merged to `main`.
