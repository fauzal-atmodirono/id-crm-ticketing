# P6 — Agent Presence, Custom Statuses & Workforce Dashboard

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p6-agent-presence-workforce.md`
**Closes:** 8 PARTIAL requirements + 4 GAPs (4.12, 4.13, 4.14, 4.73) that sit on the same missing primitive
**Effort:** 3 weeks · **Wave:** 2 · **Blocked by:** nothing
**Prior art:** the custom-status backlog note from 2026-08-04 identified `pick_agent` as the routing integration point and `ChannelPriorityStore` as the storage pattern; both re-verified against the code on 2026-08-08.

---

## 1. The problem, precisely

Six requirements are GAP or PARTIAL for **one shared reason**: Chatwoot's
presence field is a fixed three-value enum read at a point in time, and nothing
records its history.

`routing/presence.py::AgentRecord` carries `availability_status: str` — `"online"`
/ `"busy"` / `"offline"` — fetched live from the Chatwoot account API.
`PresenceFetcher` is a point-in-time read. There is no event store, no
transition log, no duration.

From that single absence:

- **4.12** asks for Available / Busy / Break / Toilet / Follow-up monitoring
  **with duration tracking and threshold notifications**. Only the three native
  statuses exist; nothing tracks how long.
- **4.13** — notify agent and admin at >10 min unavailable. Nothing tracks
  elapsed non-online time.
- **4.14** — notify admin at >1 h unavailable, with a WIP review. Same. (The only
  1-hour construct in the codebase is `tasks_reminder_warning_minutes`, which is
  a *case* deadline, not an agent status.)
- **4.17** — eight named statuses. The *switching* half is MET (the three-tier
  fallback in `pick_agent` is exactly it); the statuses are not. Lunch, Break,
  Coaching, Training, Toilet and Prayer have no store, no enum and no UI.
- **4.73** — a live dashboard of working days, login/logout, availability and
  activity performance. No login/logout capture, no shift or roster, no presence
  history, no activity dashboard.
- **4.69** — After-Call-Work. There is no wrap-up state and no post-call timer.

Two further PARTIALs are about *when* assignment happens rather than *who*:

- **4.16 / 3.1.6** — `pick_agent` runs **at handoff time only**. Nothing polls a
  queue and re-assigns work that is sitting unassigned, and there is no
  round-robin or fair-share rotation: the first match in dict-iteration order
  wins, so the same agent is picked repeatedly. `routing_enabled` defaults off.
- **B-WA-15 / B-SM-07 / B-EM-06** — a team leader can reassign via the stock
  Chatwoot assignee dropdown, and the transition is audited. But
  `POST /routing/assign` **auto-picks and does not accept a chosen agent id**, so
  there is no supervisor-facing reassignment API to build a tool on.

And one that is a case field, not a presence one:

- **4.18** — SLA-deadline reminders exist end to end. **There is no
  operator-settable per-ticket follow-up *date*** — only `sla_<int>` labels and
  `custom_attributes.sla_minutes`, which express a duration, not a date an agent
  chose.

## 2. What this package delivers

1. A presence-**event** store — the missing primitive.
2. Eight named statuses, each mapped to routing-eligible or not.
3. Duration tracking and the 10-minute / 1-hour threshold notifications.
4. A live workforce dashboard.
5. After-Call-Work as a first-class state.
6. Polled reassignment with fair-share rotation.
7. A supervisor reassignment endpoint.
8. A per-ticket follow-up date.

## 3. Design

### 3.1 The presence-event store

The primitive everything else needs:

```python
@dataclass(frozen=True)
class PresenceEvent:
    agent_id: int
    status: str            # a CustomStatus key, or a native status
    at: datetime
    source: str            # "agent" | "admin" | "system" | "poll"
    previous: str | None
```

Firestore-backed, mirroring `ChannelPriorityStore` — the pattern the 2026-08-04
backlog note identified and that `routing/store.py` already implements.

**Append-only, event-sourced, and derived state computed on read.** "How long has
Ahmad been on lunch" is `now - last_event.at`, not a mutable field that a missed
write leaves permanently wrong. It also means the history §4.73 asks for is a
consequence of the design rather than a second feature.

**A poller, not only a listener.** Chatwoot has no presence webhook, so status
changes made in Chatwoot's own UI are invisible to us. A poller runs every
`PRESENCE_POLL_SECONDS` (default 60), diffs against the last known status per
agent, and appends an event on change with `source="poll"`. Sixty seconds is a
deliberate compromise: the thresholds this drives are 10 minutes and 1 hour, so
a minute of granularity is well inside the noise, and a tighter poll multiplies
API calls for no decision-relevant precision.

**Login/logout (4.73)** derive from transitions to and from `offline`. Not a
true SSO session record — and the design says so rather than implying it: an
agent who closes their laptop without going offline will show as logged in until
the next transition. Real login/logout needs a Chatwoot-side signal that does not
exist. What can be honestly reported is *availability* history, and it is
labelled that way on the dashboard.

### 3.2 The eight statuses

```python
@dataclass(frozen=True)
class CustomStatus:
    key: str                 # "lunch"
    label: str               # "Lunch"
    color: str               # "#e8a33d"
    routable: bool           # eligible for new assignments
    native: str              # the Chatwoot status to mirror: online|busy|offline
    counts_as_unavailable: bool   # feeds the 10-min / 1-h thresholds
```

Seeded with the eight §4.17 names — Available, Busy, Lunch, Break, Coaching,
Training, Toilet, Prayer — and operator-editable, because the list will change
and it should not need a deploy.

**`native` is the field that makes this work without forking Chatwoot's enum.**
Chatwoot's presence field is a fixed enum with no extension point. So a custom
status *mirrors* into a native one: selecting "Lunch" sets the agent's Chatwoot
status to `busy` **and** appends a `lunch` presence event. Chatwoot's own UI
keeps working and shows `busy`; our surfaces show "Lunch"; and — the part that
matters operationally — **`pick_agent` continues to exclude them correctly even
if every custom-status surface is down**, because the native status is the real
gate.

`routable` and `counts_as_unavailable` are separate flags because they answer
different questions. "Coaching" is not routable but a supervisor probably does
not want a 10-minute alert about it; "Toilet" is not routable and a 10-minute
alert is exactly the point. Collapsing them would force one policy on both.

`pick_agent` gains the `routable` check alongside its existing
`availability_status == "online"` filter — the integration point the backlog note
identified — and keeps the native check as the primary gate. Belt and braces, in
that order.

### 3.3 Thresholds (4.13, 4.14)

A sweeper alongside the poller: for each agent whose current status has
`counts_as_unavailable`, compute elapsed time from the last event.

| Elapsed | Action |
|---|---|
| > `PRESENCE_WARN_MINUTES` (10) | Notify **the agent**, and the admin |
| > `PRESENCE_ESCALATE_MINUTES` (60) | Notify the admin, with the agent's WIP list |

Two properties that keep this from becoming noise:

- **Once per threshold per continuous period**, stamped on the presence event.
  A 3-hour lunch produces one 10-minute alert and one 1-hour alert, not 18.
- **The 1-hour alert carries the agent's open cases**, because §4.14 asks for a
  WIP review, and an alert that says "Ahmad has been away an hour" without
  saying "and these six cases are his" makes the supervisor do the lookup.

Delivery reuses the existing alert transport — the same path SLA breach alerts
take — rather than adding a second notification channel.

### 3.4 After-Call-Work (4.69)

A status like the others (`routable=False`, `counts_as_unavailable=False`),
entered automatically when a call ends and exited by the agent or by a timeout.

`ACW_TIMEOUT_SECONDS` (default 120) exists because an agent who forgets to leave
wrap-up would otherwise be excluded from routing indefinitely — a self-inflicted
outage that would be blamed on the routing engine.

ACW duration per call flows into the presence store, which is what makes the ACW
column §4.69 asks for computable. **The AHT half of 4.69 stays blocked on R9**
(no call queue, no handling-time instrumentation), and this package does not
claim it.

### 3.5 The workforce dashboard (4.73)

`GET /admin/workforce` — per agent: current status with elapsed time, today's
time in each status, availability percentage, assigned open cases, cases closed
today, and (once P8 lands) CSAT.

Rendered as a fork admin page alongside Agent Priorities (patch `0024`), gated on
a new `workforce.view` permission.

**"Real-time" here means a 30-second poll of a live store**, not a streamed feed
— and the page says so with a "last updated" stamp. Every other dashboard in
this system is 6-hour batch; this one reads the presence store directly and is
genuinely current, and stating the mechanism prevents "real-time" from being
read as something it is not.

### 3.6 Polled assignment and fair share (4.16)

Two defects, one fix.

Today `pick_agent` iterates `priority_map.items()` and takes the first match.
Dict iteration order is insertion order, so the same agent is picked every time
until they hit `routing_max_concurrent_per_agent`. That is not "polling
assignment"; it is a queue with one server and a fallback.

- **Fair share:** among eligible agents at the same tier, pick the one with the
  fewest open conversations, tie-broken by least-recently-assigned.
  `fetch_agent_open_counts` is already called when the concurrency cap is on —
  the data is there and is currently used only as a ceiling.
- **Polling:** a sweeper assigns conversations that are unassigned and older
  than `ROUTING_SWEEP_MIN_AGE_SECONDS`, so work that arrived when everyone was
  busy gets assigned when someone frees up instead of waiting for a new event.

The minimum age matters: without it, the sweeper races the event-driven path and
two assigners fight over the same conversation.

### 3.7 Supervisor reassignment (B-WA-15, B-SM-07, B-EM-06)

`POST /routing/assign` gains an optional `agent_id`. Supplied, it assigns to that
agent, bypassing selection; absent, it auto-picks as today.

Guardrails: RBAC-gated on a new `routing.reassign` permission; the target must
exist; a warning (not a refusal) when the target is not routable, because a team
leader assigning to someone about to return from lunch is legitimate; and the
reassignment is audited with the actor, which patch `0026` already renders.

### 3.8 Per-ticket follow-up date (4.18)

A `follow_up_at` conversation custom attribute (ISO-8601), operator-settable from
the conversation panel, feeding the existing reminder machinery in
`tasks/deadline.py` and `GET /tasks/mine`.

**Distinct from `sla_minutes`, deliberately.** An SLA deadline is a commitment
derived from policy; a follow-up date is an agent's own note that this case needs
attention on Thursday. Conflating them would make an agent's reminder look like
a breached commitment on every report that reads SLA fields.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| A custom status makes an agent invisible to routing when the status store is down | Native status is the primary gate; `routable` is an additional filter, never the only one |
| Alert storms from a long absence | Once per threshold per continuous period, stamped on the event |
| The poller multiplies Chatwoot API calls | 60 s, one account-wide `fetch_agents` call per tick — the same call `pick_agent` already makes |
| An agent stuck in ACW is excluded from routing forever | `ACW_TIMEOUT_SECONDS` auto-exit |
| The sweeper and the event path double-assign | Minimum-age gate plus the existing assignment idempotency |
| "Login/logout" is read as SSO session tracking | Labelled as availability history on the dashboard and in the docs |
| Fair-share starves a specialist | Fair share applies *within* a tier; channel priority still decides the tier first |

## 5. Testing

- **Event store** (`test_presence_store.py`): append-only; duration derived; no
  event on an unchanged poll; concurrent appends ordered.
- **Statuses** (`test_custom_status.py`): mirroring to native; `routable`
  excludes from `pick_agent`; `counts_as_unavailable` independent of `routable`;
  a store outage still excludes via native status.
- **Thresholds** (`test_presence_thresholds.py`): one alert per threshold per
  period; the 1-hour alert carries the WIP list; a status flip resets.
- **ACW** (`test_acw.py`): entered on call end; exited by agent; exited by
  timeout; duration recorded.
- **Routing** (`test_routing_fairshare.py`): least-loaded wins; tie broken by
  least-recent; tier order unchanged; the existing three-tier tests still pass.
- **Sweeper** (`test_routing_sweeper.py`): assigns aged unassigned work; skips
  fresh; no double assignment.
- **Reassign** (`test_routing_reassign.py`): explicit id honoured; RBAC; warns
  but proceeds on a non-routable target; audited.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `PRESENCE_TRACKING_ENABLED` | `false` | Off = no poller, no events |
| `PRESENCE_CUSTOM_STATUSES_ENABLED` | `false` | Off = three native statuses only |
| `PRESENCE_THRESHOLD_ALERTS_ENABLED` | `false` | Off = no 10-min/1-h alerts |
| `ACW_ENABLED` | `false` | Off = no wrap-up state |
| `ROUTING_FAIR_SHARE_ENABLED` | `false` | Off = today's first-match selection |
| `ROUTING_SWEEP_ENABLED` | `false` | Off = event-driven assignment only |
| `FOLLOW_UP_DATE_ENABLED` | `false` | Off = panel hidden |

Note `routing_enabled` itself still defaults off. P6 does not change that — a
routing engine should be switched on deliberately, per tenant.

## 7. Requirements closed

3.1.6, 4.10, 4.16, 4.17, 4.18, B-WA-15, B-SM-07, B-EM-06 — plus **4.12, 4.13,
4.14 and 4.73**, which are GAP today only because the presence-event store does
not exist, and the ACW half of **4.69**.

**Not closed:** 4.69's AHT half and 4.63's telephony KPIs, both blocked on R9.
