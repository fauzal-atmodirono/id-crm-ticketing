# P9 — Notification & Alerting UX: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alert an agent when a customer actually contacts them — in the UI they are actually looking at — without producing so much noise that they turn everything off.

**Architecture:** Move the three working alert primitives out of the `my-tasks` iframe into the Chatwoot fork, and subscribe them to the ActionCable stream the SPA already uses. Nothing is reimplemented; the events and the surface change.

**Tech Stack:** Chatwoot fork patch (Vue), Web Audio + Notification API, ActionCable, Python/FastAPI for the rule store and hourly anomaly views, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p9-notification-alerting-design.md`

## Global Constraints

- **`new_inbound` defaults to toast-only.** With sound on, a tenant doing 73% of volume on WhatsApp gets a beep every few seconds, and the first thing every agent does is disable *all* alerting — including the SLA breach alerts that matter. The requirement is met by the capability being present and configurable, not by the loudest default.
- **Never go silent.** If the event stream is unavailable, fall back to the existing 60-second poll and show a degraded indicator. Silent failure in an alerting system is the worst outcome available.
- **Do not break the `my-tasks` app.** It keeps its SLA alerts. This is an addition.
- **The hourly anomaly baseline is same-hour-across-days**, never trailing hours. Trailing hours flags every lunchtime dip.
- **A minimum-volume floor is mandatory**, not a nice-to-have — without it the hourly detector alerts every night at 03:00.
- Env vars in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/chatwoot-fork/patches/00NN-inbound-alerts.patch` | **New.** The alert module in the fork |
| `backend/.../features/alerts/rules_store.py` | **New.** `AlertRule` CRUD, account + per-agent |
| `backend/.../features/alerts/rules_router.py` | **New.** Admin + self-service endpoints |
| `backend/.../features/metrics/anomaly.py` | **Modify.** Hourly detector, separate `k`, floor |
| `backend/.../features/metrics/bigquery_schema.py` | **Modify.** `v_channel_anomaly_hourly` |
| `backend/.../features/metrics/freshness.py` | **New.** The shared `as_of` / `source` contract |

---

### Task 1: The alert-rule store

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/alerts/rules_store.py`
- Create: its test file

**Interfaces:**
- Consumes: Firestore.
- Produces: `AlertRule(event, scope, modalities, enabled)`, account defaults plus per-agent overrides, and `resolve(agent_id, event) -> AlertRule`.

**Tests first:**

```python
async def test_the_six_default_rules_are_seeded():
async def test_new_inbound_defaults_to_toast_only():
async def test_sla_breach_defaults_to_all_three_modalities():
async def test_a_per_agent_override_beats_the_account_default():
async def test_an_agent_with_no_override_gets_the_account_default():
async def test_a_disabled_rule_resolves_to_no_modalities():
async def test_an_unknown_event_resolves_to_none_and_alerts_nothing():
async def test_a_store_outage_falls_back_to_the_seeded_defaults():
```

**Test two is a design assertion, not a detail** — see the constraint above. If
someone later "improves" the default to sound, this fails and they have to read
why.

Test eight: a rule-store outage must not silence alerting.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/alerts/test_rules_store.py -q`

---

### Task 2: The fork alert module

**Files:**
- Create: `deploy/chatwoot-fork/patches/00NN-inbound-alerts.patch`
- Extract the primitives from `backend/apps/chatwoot-my-tasks/index.html` into a shared module in the patch

**Interfaces:**
- Consumes: the ActionCable conversation stream, `GET /alerts/rules/mine`.
- Produces: sound / desktop / toast on the configured events.

**Tests first (fork-side, plus a manual verification list):**

```python
def test_the_patch_applies_cleanly_onto_the_pinned_upstream_ref():
def test_a_new_incoming_message_raises_the_configured_modalities():
def test_an_outgoing_agent_message_raises_nothing():
def test_a_private_note_raises_nothing():
def test_a_conversation_outside_the_configured_scope_raises_nothing():
def test_denied_notification_permission_is_surfaced_with_a_re_request_affordance():
def test_the_my_tasks_app_behaviour_is_unchanged():
```

**Tests three and four matter because an agent's own reply arriving back down
the stream is the classic self-notification bug** — the agent hits send and their
own machine beeps at them.

Test six: a browser that has denied notification permission silently drops
desktop alerts forever. The agent must be able to see that and fix it.

**Fork-patch note:** this sandbox cannot clone upstream. Reconstruct from the
structure of an existing SPA-behaviour patch. Build via Cloud Build for `amd64`;
never on the prod VM, never from an arm64 Mac.

**Manual verification, recorded with screenshots:** an inbound WhatsApp message
raises a toast within 2 seconds; sound fires when enabled; desktop notification
appears when permission is granted; nothing fires on the agent's own reply.

**Verify:** patch applies; manual checklist recorded in `docs/testing/`.

---

### Task 3: Stream subscription with poll fallback

**Files:**
- Modify: the patch from task 2
- Create: a test for the degradation path

**Tests first:**

```python
def test_alerts_are_raised_from_the_stream_when_it_is_connected():
def test_a_stream_disconnect_activates_the_sixty_second_poll():
def test_the_degraded_indicator_is_shown_while_polling():
def test_reconnecting_returns_to_stream_mode_and_hides_the_indicator():
def test_no_alert_is_raised_twice_when_both_paths_briefly_overlap():
```

**Test five is the reconnection race**: the poll fires, the stream reconnects,
and the same message alerts twice. Deduplicate on message id.

**Verify:** manual, with the stream blocked at the network level.

---

### Task 4: Hourly anomaly detection

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/anomaly.py`
- Modify: `bigquery_schema.py` — `v_channel_anomaly_hourly`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_anomaly_hourly.py`

**Interfaces:**
- Consumes: `ANOMALY_HOURLY_ZSCORE_K`, `ANOMALY_MIN_BASELINE`.
- Produces: hourly detections comparing the current hour against the same hour on preceding days.

**Tests first:**

```python
def test_the_baseline_is_the_same_hour_across_preceding_days():
def test_the_baseline_is_not_the_trailing_hours_of_today():
def test_a_normal_lunchtime_dip_is_not_flagged():
def test_a_normal_morning_ramp_is_not_flagged():
def test_a_genuine_intra_day_spike_is_flagged():
def test_an_hour_below_the_minimum_baseline_is_suppressed():
def test_a_suppressed_hour_is_labelled_insufficient_volume_not_normal():
def test_the_hourly_detector_uses_its_own_k_and_not_the_daily_one():
def test_the_daily_detector_is_completely_unchanged():
```

**Tests three and four are why the baseline choice matters** — they fail
immediately on a trailing-hours implementation, which is the obvious thing to
write first.

Test seven: "we did not look" and "we looked and it was fine" must be
distinguishable on the dashboard. Same principle as P5's `no_data`.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_anomaly_hourly.py -q`

---

### Task 5: The freshness contract

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/freshness.py`
- Modify: every dashboard/metrics response wrapper
- Create: its test file
- Modify: the report page patches to render it

**Interfaces:**
- Produces: `as_of: datetime` and `source: Literal["live_stream","poll_60s","batch_6h"]` on every metrics response.

**Tests first:**

```python
def test_every_metrics_endpoint_response_carries_as_of_and_source():
def test_a_bigquery_backed_response_reports_batch_6h():
def test_the_alert_stream_reports_live_stream():
def test_as_of_reflects_the_last_sync_not_the_request_time():
def test_a_stale_sync_is_visible_as_an_old_as_of_rather_than_hidden():
```

**Test four is the whole point.** `as_of = now()` would make a six-hour-old
figure look current, which is precisely the misrepresentation this task exists
to remove.

**Verify:** `uv run pytest src/chatbot/features/metrics/test_freshness_contract.py -q`

---

### Task 6: The alert-preferences UI

**Files:**
- Create: `backend/.../features/alerts/rules_router.py`
- Modify: the fork patch — an agent-facing preferences panel and an admin defaults page
- Modify: the permission registry — `alerts.manage` for account defaults

**Tests first:**

```python
async def test_an_agent_can_read_and_set_their_own_overrides():
async def test_an_agent_cannot_change_the_account_defaults():
async def test_an_admin_can_change_the_account_defaults():
async def test_resetting_an_override_returns_the_agent_to_the_account_default():
async def test_the_permission_appears_in_the_permission_registry():
```

**Verify:** `uv run pytest src/chatbot/features/alerts/test_rules_router.py -q`

---

### Task 7: Flags, env, docs

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`
- Modify: `README.md`, `docs/feature-guide/`

**Tests first:**

```python
def test_the_five_settings_are_present_in_example_env():
def test_anomaly_hourly_zscore_k_defaults_to_3_5():
def test_anomaly_min_baseline_defaults_to_5():
def test_the_boolean_settings_default_to_false():
```

**Docs note (the deliverable):**

> **New-inbound alerts default to an on-screen toast only.** Sound and desktop
> notifications are available for the event and are off by default: on a tenant
> where WhatsApp carries most of the volume, an audible alert per inbound
> message means a beep every few seconds, and agents respond by disabling all
> alerting — including SLA breach alerts. Each agent can enable sound for
> new inbound in their own alert preferences.
>
> **Dashboard freshness.** Every report page now shows what its numbers are
> as-of and where they came from. Alerting and the anomaly page are live;
> BigQuery-backed dashboards are a batch sync (default 6 h) and say so. A
> difference between a dashboard figure and the live CRM is expected and its
> size is now visible.

**Verify:** suite green with flags off, then on.

---

## Definition of done

- [ ] All flags off → suite green, behaviour identical to `d85f0d4`.
- [ ] An inbound WhatsApp message raises a toast in the **main Chatwoot UI**, verified manually with a screenshot.
- [ ] An agent's own reply raises nothing.
- [ ] Stream loss falls back to polling with a visible indicator, and no alert fires twice on reconnect.
- [ ] A lunchtime dip is not an anomaly; a genuine intra-day spike is.
- [ ] Low-volume hours labelled "insufficient volume", never "normal".
- [ ] Every metrics response carries `as_of` and `source`, and `as_of` is the sync time, not the request time.
- [ ] `my-tasks` app behaviour unchanged.
- [ ] Nothing merged to `main`.
