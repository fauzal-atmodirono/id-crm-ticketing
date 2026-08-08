# P8 — AI & Agent Measurement: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI's cost, its effectiveness, and each agent's customer rating into numbers somebody can defend in a monthly review.

**Architecture:** Metering wraps the Gemini client, not the call sites — the backend makes Gemini calls from at least five places, and a per-call-site change would be unmetered again the first time someone adds a sixth. Every rate metric returns its denominator, because a per-agent score without a sample size is how a measurement becomes a grievance.

**Tech Stack:** Python 3.12, google-genai, SQLAlchemy (`ai_actions`), BigQuery views, Firestore (price table), pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p8-ai-agent-measurement-design.md`

## Global Constraints

- **P4 and P7 land first.** P4 provides the query layer; P7 provides sentiment, which several reports cut by.
- **Meter at the client boundary.** A test asserts no Gemini client is constructed outside the metered wrapper.
- **Missing usage metadata records `None`, never `0`.** A zero-token call is a real thing; "we did not capture it" is a different thing, and a cost report that conflates them understates spend.
- **Every rate returns its denominator.** No exceptions, including internal endpoints.
- **`v_csat` does not change.** Existing dashboards read it. Per-agent CSAT is a sibling view.
- **Call QA stays manual.** The phone transcript path has never run against a real Twilio call; automated scoring on it would be confident noise.
- Env vars in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `agent/app/ai/gemini.py` | **Modify.** Capture output + cached tokens |
| `agent/app/db/models.py` | **Modify.** `output_tokens`, `cached_tokens` on `ai_actions` |
| `backend/.../platform/metered_genai.py` | **New.** The metering wrapper |
| `backend/.../features/metrics/token_usage.py` | **New.** `TokenUsage` + sink |
| `backend/.../features/metrics/price_table.py` | **New.** Effective-dated prices |
| `backend/.../features/metrics/bigquery_schema.py` | **Modify.** Cost + AI performance + CSAT/KB views |
| `backend/.../features/chat/nps.py` | **Modify.** Wire into the survey flow |
| `backend/.../features/metrics/qa.py` | **Modify.** Call rubric |

---

### Task 1: Capture all three token classes in `agent/`

**Files:**
- Modify: `agent/app/ai/gemini.py`, `agent/app/db/models.py`
- Modify: `agent/tests/test_gemini.py`

**Interfaces:**
- Consumes: `response.usage_metadata`.
- Produces: `prompt_tokens`, `output_tokens`, `cached_tokens` on `AiAction`. Task 4 prices them.

**Tests first:**

```python
def test_output_tokens_are_extracted_from_usage_metadata():
def test_cached_tokens_are_extracted_when_present():
def test_absent_usage_metadata_records_none_for_all_three():
def test_a_zero_token_field_records_zero_and_not_none():
def test_the_existing_prompt_tokens_extraction_is_unchanged():
def test_the_handoff_fallback_path_still_records_what_it_knows():
```

**Tests three and four are the pair that keeps the cost report honest.** `None`
means "not captured"; `0` means "captured, and it was zero". Conflating them
makes every uncaptured call look free.

The `ai_actions` table is created via `Base.metadata.create_all` with no Alembic
(CLAUDE.md), so the new columns must be nullable and the existing rows must
continue to read — assert that.

**Verify:** `cd agent && pytest tests/test_gemini.py -q && pytest -q`

---

### Task 2: The backend metering wrapper

**Files:**
- Create: `backend/apps/backend/src/chatbot/platform/metered_genai.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/token_usage.py`
- Create: their test files

**Interfaces:**
- Consumes: the google-genai client, a usage sink.
- Produces: a wrapped client used by every backend Gemini call site; `TokenUsage(service, surface, model, prompt_tokens, output_tokens, cached_tokens)`.

**Tests first:**

```python
async def test_a_wrapped_call_records_a_token_usage_row():
async def test_the_surface_label_identifies_which_feature_made_the_call():
async def test_an_embedding_call_is_metered():
async def test_a_streaming_call_records_usage_from_the_final_chunk():
async def test_a_failed_call_records_no_usage_but_does_not_raise():
async def test_no_gemini_client_is_constructed_outside_the_wrapper():   # architectural guard
async def test_the_sink_failing_never_breaks_the_underlying_call():
async def test_the_flag_off_records_nothing_and_adds_no_latency():
```

**Test six is the task's reason for existing.** Implement it as a source scan
for direct client construction, allowlisting the wrapper module. Without it,
the sixth call site added next month is silently unmetered and the cost report
is quietly wrong.

Test seven states the priority: metering must never be able to break a customer
conversation.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/platform/test_metered_genai.py -q`

---

### Task 3: The price table

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/price_table.py`
- Create: its test file

**Interfaces:**
- Consumes: Firestore.
- Produces: `price_for(model, token_class, at) -> Decimal | None` with effective-from dating.

**Tests first:**

```python
async def test_a_price_effective_from_january_applies_to_a_february_call():
async def test_a_price_change_in_march_does_not_re_price_a_february_call():
async def test_input_output_and_cached_are_priced_independently():
async def test_an_unpriced_model_returns_none_and_is_reported_as_unpriced():
async def test_the_most_recent_effective_price_at_or_before_the_call_wins():
async def test_prices_use_decimal_not_float():
```

**Test two is the requirement.** Re-pricing history whenever a rate changes
makes last month's reported cost change after it was reported.

Test four: an unpriced model must surface as "unpriced", never as free — a new
model appearing in the cost report at zero cost is the failure mode.

Test six: floating-point money in a report a client is invoiced against will
eventually produce a cent-level discrepancy nobody can explain.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_price_table.py -q`

---

### Task 4: Cost views and endpoint

**Files:**
- Modify: `bigquery_schema.py`
- Modify: `insights_router.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_ai_cost.py`

**Interfaces:**
- Consumes: tasks 1–3.
- Produces: `v_ai_cost` (day × service × surface × model), `GET /metrics/ai-cost`.

**Tests first:**

```python
def test_cost_is_the_sum_of_the_three_token_classes_at_their_own_rates():
def test_unpriced_models_are_reported_separately_and_not_as_zero():
def test_uncaptured_usage_is_reported_as_unknown_and_not_as_zero():
async def test_the_endpoint_accepts_a_period_and_the_standard_filters():
async def test_cost_per_conversation_is_derivable_from_the_response():
def test_both_services_appear_in_the_service_dimension():
```

**Test five is the commercial question §4.28.2 is really asking** — "what does
the AI cost per conversation" — and it should be answerable from one response,
not by joining two.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_ai_cost.py -q`

---

### Task 5: NPS wiring

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/nps.py`
- Modify: the lifecycle survey flow and the phone post-call path
- Create: `backend/apps/backend/src/chatbot/features/chat/test_nps_wiring.py`

**Interfaces:**
- Consumes: `NPS_SAMPLE_RATE`, the survey flow.
- Produces: `record_nps` actually called; `nps_score` on the conversation; the agent attributed at survey time.

**Tests first:**

```python
async def test_a_sample_rate_of_zero_asks_no_nps_question():
async def test_a_sample_rate_of_one_asks_nps_instead_of_csat():
async def test_nps_replaces_csat_rather_than_being_appended_to_it():
async def test_the_score_is_attributed_to_the_agent_assigned_at_survey_time():
async def test_a_reassignment_after_the_survey_does_not_re_attribute_the_score():
async def test_the_phone_path_records_nps():
async def test_an_out_of_range_score_is_rejected_not_clamped():
async def test_v_nps_by_agent_is_populated_end_to_end():
```

**Test three prevents the obvious mistake.** Two surveys at the end of one
conversation halves the response rate for both, and a sparse metric is what this
package exists to fix.

Test five: attribution recorded at survey time, not derived later from the
current assignee.

**Verify:** `uv run pytest src/chatbot/features/chat/test_nps_wiring.py -q`

---

### Task 6: CSAT per agent

**Files:**
- Modify: `bigquery_schema.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_csat_by_agent.py`

**Interfaces:**
- Consumes: the existing `csat_<n>` labels, agent attribution from task 5.
- Produces: `v_csat_by_agent` returning score **and count**.

**Tests first:**

```python
def test_v_csat_is_completely_unchanged():
def test_v_csat_by_agent_groups_by_agent_id():
def test_every_row_returns_the_rating_count_alongside_the_average():
def test_an_agent_below_the_minimum_sample_size_is_excluded_from_rankings():
def test_that_agent_still_appears_in_the_unranked_listing_with_their_count():
def test_an_agent_with_no_ratings_appears_with_a_null_score_not_a_zero():
```

**Tests four and five together are the design.** Suppress from *rankings*, not
from *existence* — hiding an agent entirely makes the list look complete when it
is not. Test six: a zero score is a terrible rating; no ratings is not.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_csat_by_agent.py -q`

---

### Task 7: Call QA rubric

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/qa.py`, `qa_schema.py`
- Modify: the QA admin surface
- Modify: `test_qa.py`

**Interfaces:**
- Consumes: the existing `qa_labels` mechanism.
- Produces: a `channel` dimension and a five-criterion call rubric scored as a percentage against P5's 85% target.

**Tests first:**

```python
def test_a_qa_record_carries_its_channel():
def test_the_call_rubric_scores_five_criteria_as_a_percentage():
def test_existing_channel_agnostic_qa_records_still_load():
def test_the_call_qa_percentage_compares_against_the_targets_store_value():
def test_a_partially_scored_rubric_reports_incomplete_rather_than_a_low_score():
def test_v_quality_is_unchanged_for_existing_consumers():
```

**Test five:** a half-filled QA form is not a failing call.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_qa.py -q`

---

### Task 8: The four AI performance reports

**Files:**
- Modify: `bigquery_schema.py`
- Modify: `insights_router.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_ai_performance_views.py`

**Interfaces:**
- Consumes: `resolved_by`, `ai_actions.decision`, handoff `reason`, P7's sentiment.
- Produces: `v_ai_resolution`, `v_ai_vs_human`, `v_ai_escalation_reasons`, `v_ai_deflection`, and an AI-vs-human split on CSAT.

**Tests first:**

```python
def test_a_case_resolved_with_no_agent_message_counts_as_ai_resolved():
def test_a_case_where_the_bot_replied_then_an_agent_took_over_is_not_deflected():
def test_the_deflection_definition_string_is_returned_with_the_report():
def test_ai_vs_human_volumes_sum_to_the_total_case_count():
def test_escalation_reasons_are_grouped_by_the_handoff_reason():
def test_the_csat_split_distinguishes_ai_resolved_from_agent_resolved_cases():
def test_every_rate_returns_its_denominator():
```

**Test two is the definition**, written as a test because it is the number a
client will quote and two reasonable readings differ by roughly a factor of two.
Test three puts the definition on the report so nobody has to guess which
reading was used.

**Scope discipline:** do **not** build ⑦ AI Root Cause Analysis or ⑧ KB
Improvement recommendations in this task. They are AI-analysis features and a
separate package. The vendor response has already once claimed capabilities that
were not built; do not add to that list.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_ai_performance_views.py -q`

---

### Task 9: KB health

**Files:**
- Modify: `bigquery_schema.py`, `faq_schema.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_kb_health.py`

**Interfaces:**
- Consumes: `Subcategory='Unresolved Query'`, `v_faq_quality`, FAQ `updated_at` and serve counts.
- Produces: `v_kb_coverage`, `v_kb_staleness`.

**Tests first:**

```python
def test_coverage_is_the_share_of_enquiries_with_a_match_above_the_score_floor():
def test_unresolved_query_rows_count_against_coverage():
def test_staleness_weights_age_by_how_often_the_entry_was_served():
def test_a_never_served_stale_entry_ranks_below_a_frequently_served_one():
def test_an_entry_edited_today_has_zero_staleness_regardless_of_serve_count():
def test_both_views_accept_a_period():
```

**Tests three and four are the useful behaviour:** the review queue should be
ordered by "stale *and* load-bearing", not by age alone — a year-old entry nobody
ever hits is not the problem.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_kb_health.py -q`

---

### Task 10: Flags, env, and the scope note

**Files:**
- Modify: `deploy/tenants/example.env`, both `config.py` files
- Modify: `README.md`

**Tests first:**

```python
def test_all_six_settings_are_present_in_example_env():
def test_nps_sample_rate_defaults_to_zero():
def test_csat_ranking_min_samples_defaults_to_ten():
def test_both_services_start_with_none_of_them_set():
```

**The scope note (the deliverable):**

> **§4.56 AI Performance Reporting: four of eight reports ship in this package**
> — AI Case Resolution, AI vs Human handling, AI Escalation and AI Deflection
> Rate, plus an AI-vs-human split on satisfaction. **AI Root Cause Analysis and
> KB Improvement recommendations are not built.** They are AI-analysis features
> requiring a model to summarise failure patterns — a further 2–3 weeks, in their
> own package. AI Accuracy is answered by P7's calibration baseline as a measured
> figure rather than as a report.
>
> **Deflection rate** counts cases resolved with **no agent message at all**. A
> conversation the bot answered before a human took over is *not* deflected. The
> definition is returned with the report because two reasonable definitions
> differ by roughly a factor of two.

**Verify:** both suites green with flags off, then on.

---

## Definition of done

- [ ] All six flags at defaults → suites green, behaviour identical to `d85f0d4`.
- [ ] Output and cached tokens captured in **both** services; the architectural guard test passes.
- [ ] `None` and `0` provably distinguished everywhere in the cost path.
- [ ] Prices effective-dated; last month's cost does not change when a rate changes.
- [ ] NPS collected without collapsing CSAT response rate; attributed at survey time.
- [ ] Per-agent CSAT returns counts; low-sample agents excluded from rankings but still listed.
- [ ] Deflection definition returned with the report and asserted by test.
- [ ] ⑦ and ⑧ explicitly **not** claimed anywhere in code, docs or comments.
- [ ] Nothing merged to `main`.
