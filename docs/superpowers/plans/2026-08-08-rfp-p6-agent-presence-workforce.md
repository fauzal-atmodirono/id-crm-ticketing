# P6 — Agent Presence, Custom Statuses & Workforce Dashboard: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the one missing primitive — a presence-*event* store — and let the six requirements sitting on it fall out: eight named statuses, duration tracking, 10-minute and 1-hour alerts, a live workforce dashboard, After-Call-Work, and fair-share polled assignment.

**Architecture:** Append-only events, derived state computed on read. A custom status *mirrors* into Chatwoot's fixed native enum rather than trying to extend it — so Chatwoot's own UI keeps working, and `pick_agent` still excludes an unavailable agent correctly even if every custom-status surface is down.

**Tech Stack:** Python 3.12, FastAPI, Firestore (mirroring `ChannelPriorityStore` in `routing/store.py`), the Chatwoot account API, a fork admin page, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p6-agent-presence-workforce-design.md`

## Global Constraints

- **The native Chatwoot status stays the primary routing gate.** `routable` is an *additional* filter. A custom-status store outage must degrade to today's behaviour, never to "every agent is eligible" and never to "no agent is eligible".
- **Events are append-only.** Never mutate a "current status" field — a missed write would leave it permanently wrong, and the history §4.73 needs would not exist.
- **One alert per threshold per continuous unavailable period.** A 3-hour lunch produces two alerts, not eighteen.
- **An agent must never be stuck out of routing.** ACW has a timeout; every non-routable state is exitable.
- **Do not claim SSO login/logout.** What is derivable is availability history; label it that way everywhere, including the UI.
- `routing_enabled` remains default-off. This package does not switch on the routing engine.
- Env vars in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/routing/presence_store.py` | **New.** Append-only `PresenceEvent` store, derived durations |
| `backend/.../features/routing/custom_status.py` | **New.** `CustomStatus`, the eight seeds, native mirroring |
| `backend/.../features/routing/presence_poller.py` | **New.** 60 s diff-and-append |
| `backend/.../features/routing/presence_thresholds.py` | **New.** The 10-min / 1-h sweeper |
| `backend/.../features/routing/acw.py` | **New.** Wrap-up state + timeout |
| `backend/.../features/routing/service.py` | **Modify.** `routable` filter + fair share |
| `backend/.../features/routing/sweeper.py` | **New.** Polled assignment of aged unassigned work |
| `backend/.../features/routing/router.py` | **Modify.** `POST /routing/assign` accepts `agent_id` |
| `backend/.../features/routing/workforce_router.py` | **New.** `GET /admin/workforce` |
| `deploy/chatwoot-fork/patches/00NN-workforce-dashboard.patch` | **New.** |
| `agent/app/services/sync.py` | **Modify.** `follow_up_at` attribute handling |

---

### Task 1: The presence-event store

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/presence_store.py`
- Create: its test file

**Interfaces:**
- Consumes: Firestore.
- Produces: `append(event)`, `latest(agent_id) -> PresenceEvent | None`, `since(agent_id, at) -> list[PresenceEvent]`, `elapsed_in_current_status(agent_id, now) -> timedelta | None`. Tasks 3, 4, 5 and 7 all read these.

**Tests first:**

```python
async def test_appending_an_event_makes_it_the_latest():
async def test_elapsed_is_computed_from_the_latest_event_not_stored():
async def test_an_agent_with_no_events_returns_none_not_a_zero_duration():
async def test_since_returns_events_in_chronological_order():
async def test_two_appends_at_the_same_instant_are_both_retained():
async def test_the_store_is_append_only_and_exposes_no_update_method():
async def test_todays_time_in_each_status_is_derivable_from_the_event_list():
```

**Test three:** "no events" and "zero seconds in status" are different, and a
dashboard that shows a brand-new agent as "0 min in Available" implies a
transition that never happened.

Test six is a design assertion — if an `update` method exists, someone will use
it and the history stops being trustworthy.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/routing/test_presence_store.py -q`

---

### Task 2: Custom statuses and native mirroring

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/custom_status.py`
- Create: its test file

**Interfaces:**
- Consumes: Firestore (the status list), the Chatwoot availability API.
- Produces: `CustomStatus(key, label, color, routable, native, counts_as_unavailable)`, a seeded list of the eight §4.17 names, and `set_status(agent_id, key)` which writes the native status **and** appends an event.

**Tests first:**

```python
async def test_the_eight_named_statuses_are_seeded():
async def test_setting_lunch_sets_the_native_chatwoot_status_to_busy():
async def test_setting_lunch_also_appends_a_presence_event():
async def test_routable_and_counts_as_unavailable_are_independent_flags():
async def test_coaching_is_not_routable_but_does_not_count_as_unavailable():
async def test_toilet_is_not_routable_and_does_count_as_unavailable():
async def test_an_operator_can_add_a_ninth_status_without_a_deploy():
async def test_a_native_status_write_failure_does_not_append_a_misleading_event():
async def test_seeding_never_overwrites_an_operator_edited_status():
```

**Test eight is the ordering constraint:** write native first, append the event
only on success. An event claiming an agent is on lunch while Chatwoot still
shows them online would make the dashboard and the router disagree — the exact
class of defect P1 exists to fix elsewhere.

Tests five and six pin the two-flag design to concrete cases, so a later
simplification to one flag fails loudly.

**Verify:** `uv run pytest src/chatbot/features/routing/test_custom_status.py -q`

---

### Task 3: The poller

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/presence_poller.py`
- Create: its test file

**Interfaces:**
- Consumes: `PresenceFetcher.fetch_agents()` (the existing account-wide call), the event store.
- Produces: an event per *changed* status, `source="poll"`.

**Tests first:**

```python
async def test_a_changed_status_appends_one_event():
async def test_an_unchanged_status_appends_nothing():
async def test_a_new_agent_appearing_appends_an_initial_event():
async def test_an_agent_disappearing_from_the_account_appends_an_offline_event():
async def test_one_poll_makes_exactly_one_chatwoot_api_call():
async def test_a_fetch_failure_is_logged_and_the_next_tick_proceeds():
async def test_a_status_set_through_set_status_is_not_double_recorded_by_the_poll():
```

**Test five guards the cost:** `fetch_agents` is account-wide, so a poll must be
one call regardless of headcount. A per-agent loop here would be a 60-second
multiplier on API usage.

Test seven prevents duplicate history when both paths observe the same change.

**Verify:** `uv run pytest src/chatbot/features/routing/test_presence_poller.py -q`

---

### Task 4: Threshold alerts

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/presence_thresholds.py`
- Create: its test file

**Interfaces:**
- Consumes: the event store, `counts_as_unavailable`, the existing alert transport.
- Produces: warn and escalate alerts, stamped on the presence event so they fire once per continuous period.

**Tests first:**

```python
async def test_eleven_minutes_unavailable_fires_the_warn_alert():
async def test_nine_minutes_unavailable_fires_nothing():
async def test_a_three_hour_absence_fires_exactly_two_alerts():
async def test_returning_to_available_and_leaving_again_re_arms_both_thresholds():
async def test_the_one_hour_alert_includes_the_agents_open_cases():
async def test_a_status_with_counts_as_unavailable_false_never_alerts():
async def test_the_warn_alert_reaches_both_the_agent_and_the_admin():
async def test_an_alert_transport_failure_does_not_prevent_the_stamp_from_being_recorded():
```

**Test three is the anti-noise requirement**, and test four is its necessary
complement — a once-only stamp that never re-arms would silence the second
absence of the day.

Test eight is a judgement call worth naming: stamp anyway on transport failure.
Retrying an alert storm is worse than missing one alert.

**Verify:** `uv run pytest src/chatbot/features/routing/test_presence_thresholds.py -q`

---

### Task 5: After-Call-Work

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/acw.py`
- Create: its test file
- Modify: the phone call-end handler

**Interfaces:**
- Consumes: the call-ended event, the status store.
- Produces: automatic entry into `acw`; exit by agent action or `ACW_TIMEOUT_SECONDS`.

**Tests first:**

```python
async def test_a_call_ending_puts_the_agent_into_acw():
async def test_an_agent_in_acw_is_not_routable():
async def test_an_agent_can_leave_acw_manually():
async def test_acw_auto_exits_after_the_timeout():
async def test_the_acw_duration_is_recorded_as_a_presence_event():
async def test_acw_does_not_count_as_unavailable_for_the_threshold_alerts():
async def test_an_agent_already_offline_is_not_moved_into_acw():
async def test_the_flag_off_leaves_call_end_handling_unchanged():
```

**Test four is the safety property.** Without the timeout an agent who forgets
to leave wrap-up is silently removed from routing for the rest of the shift, and
it will be reported as a routing bug.

Test seven: an agent who hung up and went home should not be resurrected into a
wrap-up state.

**Verify:** `uv run pytest src/chatbot/features/routing/test_acw.py -q`

---

### Task 6: Fair-share selection

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/routing/service.py`
- Create: `backend/apps/backend/src/chatbot/features/routing/test_routing_fairshare.py`

**Interfaces:**
- Consumes: `fetch_agent_open_counts` (already called when the concurrency cap is on), the `routable` flag.
- Produces: within-tier selection by fewest open conversations, tie-broken by least-recently-assigned.

**Tests first:**

```python
async def test_the_least_loaded_eligible_agent_is_picked():
async def test_a_tie_is_broken_by_least_recently_assigned():
async def test_tier_order_is_unchanged_first_priority_still_beats_any_priority():
async def test_a_non_routable_custom_status_excludes_an_online_agent():
async def test_a_custom_status_store_outage_falls_back_to_the_native_status_filter():
async def test_the_flag_off_reproduces_todays_first_match_selection():
async def test_every_existing_three_tier_routing_test_still_passes():
async def test_ten_conversations_across_two_equal_agents_split_five_five():
```

**Test three is the constraint that keeps fair share from breaking channel
specialisation** — fair share operates *within* a tier, never across tiers.
Test five is the degradation guarantee. Test eight is the observable outcome the
requirement is really asking for.

**Verify:** `uv run pytest src/chatbot/features/routing/ -q`

---

### Task 7: The assignment sweeper

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/sweeper.py`
- Create: its test file

**Interfaces:**
- Consumes: unassigned conversations, `pick_agent`.
- Produces: assignment of unassigned conversations older than `ROUTING_SWEEP_MIN_AGE_SECONDS`.

**Tests first:**

```python
async def test_an_aged_unassigned_conversation_is_assigned():
async def test_a_fresh_unassigned_conversation_is_left_to_the_event_path():
async def test_an_already_assigned_conversation_is_skipped():
async def test_no_eligible_agent_leaves_the_conversation_unassigned_without_error():
async def test_two_concurrent_sweeps_do_not_double_assign():
async def test_the_sweep_respects_the_per_agent_concurrency_cap():
async def test_the_flag_off_runs_no_sweep():
```

**Test two prevents the race** the minimum-age gate exists for: without it the
sweeper and the handoff path both try to assign the same new conversation.

**Verify:** `uv run pytest src/chatbot/features/routing/test_sweeper.py -q`

---

### Task 8: Supervisor reassignment

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/routing/router.py`
- Create: `backend/apps/backend/src/chatbot/features/routing/test_routing_reassign.py`
- Modify: the permission registry — add `routing.reassign`

**Tests first:**

```python
async def test_an_explicit_agent_id_is_honoured_and_bypasses_selection():
async def test_an_absent_agent_id_auto_picks_exactly_as_today():
async def test_an_unknown_agent_id_is_rejected_with_a_useful_message():
async def test_assigning_to_a_non_routable_agent_succeeds_with_a_warning():
async def test_an_unauthorised_caller_is_rejected():
async def test_the_reassignment_is_audited_with_the_acting_user():
```

**Test four encodes the policy:** a team leader assigning to someone due back
from lunch in five minutes is a legitimate act. Warn, do not refuse — a refusal
here would push supervisors back to the stock dropdown and lose the audit trail.

**Verify:** `uv run pytest src/chatbot/features/routing/test_routing_reassign.py -q`

---

### Task 9: The workforce dashboard

**Files:**
- Create: `backend/.../features/routing/workforce_router.py`
- Create: `deploy/chatwoot-fork/patches/00NN-workforce-dashboard.patch`
- Modify: the permission registry — add `workforce.view`

**Tests first:**

```python
async def test_the_response_lists_every_agent_with_a_current_status_and_elapsed_time():
async def test_todays_time_per_status_is_returned_per_agent():
async def test_the_availability_percentage_is_computed_over_the_working_day_not_24h():
async def test_open_case_counts_are_included():
async def test_an_agent_with_no_events_today_renders_without_error():
async def test_the_response_carries_a_last_updated_timestamp():
async def test_an_unauthorised_caller_is_rejected():
```

**Test three:** availability over 24 hours would show every agent at ~35% and
mean nothing. Compute it over configured working hours — reusing the P1 helper,
not a new calendar.

Test six supports the honesty commitment: the page states its freshness rather
than implying a live feed.

**UI labelling requirement:** the login/logout column is titled **"Availability
history"**, not "Login/logout". Derived from transitions to and from `offline`,
it is not a session record, and the docs must say so.

**Verify:** `uv run pytest src/chatbot/features/routing/test_workforce_router.py -q`

---

### Task 10: Per-ticket follow-up date

**Files:**
- Modify: `agent/app/services/sync.py`, `backend/.../features/chat/tasks/deadline.py`
- Create: `agent/tests/test_follow_up_date.py`
- Modify: the conversation panel patch (share P3's patch if both land together)

**Tests first:**

```python
async def test_a_follow_up_date_can_be_set_on_a_conversation():
async def test_the_reminder_fires_at_the_follow_up_date():
async def test_a_follow_up_date_is_not_treated_as_an_sla_deadline():
async def test_clearing_the_date_cancels_the_reminder():
async def test_a_past_date_is_rejected_with_a_usable_message():
async def test_the_follow_up_appears_in_tasks_mine():
async def test_sla_minutes_behaviour_is_completely_unchanged():
```

**Tests three and seven are the separation guarantee.** A follow-up date is an
agent's note; an SLA deadline is a policy commitment. If they merge, every
agent's reminder becomes a breach on the SLA report.

**Verify:** `cd agent && pytest tests/test_follow_up_date.py -q && pytest -q`

---

### Task 11: Flags, env, docs

**Files:**
- Modify: `deploy/tenants/example.env`, both `config.py` files, `agent/tests/conftest.py`
- Modify: `README.md`, `docs/feature-guide/`

**Tests first:**

```python
def test_all_seven_settings_are_present_in_example_env():
def test_all_seven_default_to_false():
def test_routing_enabled_still_defaults_to_false():
def test_both_services_start_with_none_of_the_new_vars_set():
```

**Docs note (the deliverable):**

> Custom statuses mirror into Chatwoot's native Online/Busy/Offline. Selecting
> "Lunch" shows as **Busy** inside Chatwoot's own UI and as **Lunch** on the
> workforce dashboard. This is deliberate: Chatwoot's presence field is a fixed
> enum, and mirroring means an agent is still correctly excluded from routing
> even if the custom-status service is unavailable.
>
> The "Availability history" column is derived from transitions to and from
> Offline. It is **not** a login/logout record — an agent who closes their
> laptop without going offline stays shown as available until their next
> transition.

**Verify:** both suites green with all flags off, then with all on.

---

## Definition of done

- [ ] All seven flags off → suites green, behaviour identical to `d85f0d4`.
- [ ] Eight named statuses seeded, operator-extensible, mirroring to native.
- [ ] A custom-status store outage provably degrades to today's routing behaviour.
- [ ] A 3-hour absence fires exactly two alerts; a second absence re-arms both.
- [ ] The 1-hour alert carries the agent's WIP list.
- [ ] No state can trap an agent out of routing; ACW times out.
- [ ] Ten conversations across two equal agents split five/five.
- [ ] Availability computed over working hours, not 24 h.
- [ ] "Availability history" labelled as such everywhere; no SSO claim.
- [ ] Follow-up dates provably do not appear as SLA breaches.
- [ ] Nothing merged to `main`.
