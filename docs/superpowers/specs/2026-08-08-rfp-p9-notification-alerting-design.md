# P9 — Notification & Alerting UX

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p9-notification-alerting.md`
**Closes:** 5 PARTIAL requirements
**Effort:** 2 weeks · **Wave:** 3 · **Blocked by:** nothing

---

## 1. The problem, precisely

**Every alerting primitive already works, in the wrong place, for the wrong
event.**

`backend/apps/chatwoot-my-tasks/index.html` implements all three modalities
§3.1.7 asks for: a Web Audio `beep()`, `Notification.requestPermission()` for
desktop notifications, an in-page toast, and a 60-second poll. It is complete and
it works.

Two things are wrong with it:

1. **It fires on SLA warn/breach only, never on new inbound.** §4.2 asks for a
   pop-up notification on new inbound contact; §3.1.1 asks for pop-up alerts in
   the single-view interface. Neither is what this fires on.
2. **It lives in a separate dashboard-app iframe**, not the main Chatwoot UI. An
   agent working in the conversation list does not have it open.

For new inbound, the gap analysis is precise and worth repeating: **nothing
custom was built.** Whatever fires today is upstream Chatwoot's own browser
notification — unconfigured, unverified, with no evidence in this repo. Claiming
§4.2 on the basis of an upstream default nobody has tested is exactly the kind of
claim the gap analysis flagged 17 times in the vendor response.

Separately, **the anomaly dashboard cannot detect the thing its own requirement
uses as an example.** `v_channel_anomaly` + `anomaly.py`'s z-score (k=3.0,
configurable) + `GET /metrics/anomalies` + `ProtonAnomaly.vue` all exist and
work — at **daily grain, comparing yesterday against a 7-day mean**. §4.79 asks
for a configurable, real-time anomaly warning dashboard, and its example is an
intra-day channel explosion. A daily-grain baseline cannot see one until the next
day, and the page fetches once on mount.

## 2. What this package delivers

1. New-inbound alerting in the main Chatwoot UI, across all three modalities.
2. Operator-configurable alert rules — who gets alerted about what.
3. Hourly-grain anomaly detection with push.
4. An honest freshness statement wherever "real-time" is claimed.

## 3. Design

### 3.1 Move the primitives, do not rebuild them

The `my-tasks` app's `beep()`, permission request and toast are working, tested-in-
anger code. They move into the Chatwoot fork as a small shared module rather than
being reimplemented, and the fork surface subscribes them to a wider set of
events.

**Why the fork rather than the iframe:** the same architectural fact P7 relies
on. A dashboard-app iframe is cross-origin and sandboxed; it cannot know what the
agent is looking at, cannot reliably raise a notification the browser attributes
to the CRM, and is only loaded when the agent has that panel open. The fork is
loaded whenever Chatwoot is.

The `my-tasks` app keeps working unchanged — it is a task list, and its SLA
alerts are legitimately its own. This is an addition, not a migration.

### 3.2 The event source

Chatwoot's ActionCable stream already pushes conversation events to the SPA —
it is how the conversation list updates without a refresh. The alert module
subscribes to the same stream rather than adding a poll.

This matters: a 60-second poll for new-inbound alerting would mean an agent is
notified up to a minute after the customer's message is already visible in their
list, which is worse than no alert because it trains agents to ignore it.

Fallback: if the stream is unavailable, alerting degrades to the existing 60-second
poll rather than going silent, and the UI shows a degraded-mode indicator.

### 3.3 Alert rules

Not every agent should be alerted about every conversation. An operator-editable
rule set, stored per account:

```python
@dataclass(frozen=True)
class AlertRule:
    event: str          # "new_inbound" | "assigned_to_me" | "sla_warn" | "sla_breach" | "escalated" | "anomaly"
    scope: str          # "mine" | "my_inbox" | "my_team" | "all"
    modalities: list[str]   # "sound" | "desktop" | "toast"
    enabled: bool
```

Defaults chosen so the out-of-box behaviour is useful and quiet:

| Event | Default scope | Default modalities |
|---|---|---|
| `assigned_to_me` | mine | sound + desktop + toast |
| `new_inbound` | my_inbox | toast only |
| `sla_warn` | mine | toast |
| `sla_breach` | mine | sound + desktop + toast |
| `escalated` | my_team | toast |
| `anomaly` | all (supervisors) | desktop |

**`new_inbound` defaults to toast-only, deliberately.** A tenant handling 73%
of volume on WhatsApp would have a beep every few seconds with sound on, and the
first thing every agent would do is disable all alerting — including the SLA
breach alerts that matter. The requirement is met (the alert exists, all three
modalities are available and configurable); the default is chosen so the feature
survives contact with a real shift.

Per-agent overrides sit on top of the account defaults, because tolerance for
this genuinely varies by person and forcing one setting produces a workaround.

### 3.4 Intra-day anomaly detection (§4.79)

Two changes to a mechanism that is otherwise sound:

**Hourly grain.** A new `v_channel_anomaly_hourly` comparing the current hour
against the same hour across the trailing N days — not against the trailing N
*hours*. Comparing 14:00 against 13:00, 12:00 and 11:00 would flag every lunchtime
dip and every morning ramp as an anomaly. Comparing 14:00 Tuesday against 14:00
on the preceding Tuesdays is the comparison that means something.

**Push, not mount-fetch.** The anomaly page subscribes to the same event stream
as §3.2 and re-renders on a detection, and supervisors with the `anomaly` alert
rule get a desktop notification.

`anomaly_zscore_k` stays configurable and keeps its 3.0 default. Hourly buckets
are noisier than daily ones, so the hourly detector uses its own `k`
(`ANOMALY_HOURLY_ZSCORE_K`, default 3.5) — one threshold across both grains would
either flood at hourly or go blind at daily.

**A minimum-volume floor** is required and is not optional: at 03:00 a channel's
baseline may be 0.3 cases, and two cases is then a z-score of 6. Without a floor
the hourly detector alerts every night. `ANOMALY_MIN_BASELINE` (default 5)
suppresses detection below it, and the dashboard shows those hours as
"insufficient volume" rather than as normal.

### 3.5 Honesty about "real-time"

Three surfaces in this system are described as real-time and are not:
§2.2.3's executive dashboard (6-hour batch), §4.79's anomaly page, and §4.81's
reporting.

P9's contribution is a shared freshness contract: every dashboard response
carries `as_of` and `source` (`live_stream` / `poll_60s` / `batch_6h`), and every
page renders it. Alerting and the anomaly page become genuinely live; the
BigQuery-backed dashboards remain batch and **say so on screen**.

This is a small piece of work with a disproportionate effect on a reconciliation
meeting, where "the dashboard says 41 and the CRM says 44" is otherwise a
credibility problem rather than a six-hour sync interval.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Alert fatigue makes agents disable everything | `new_inbound` is toast-only by default; per-agent overrides; rules are operator-tunable |
| The event stream is unavailable and alerting goes silent | Degrades to the existing 60 s poll with a visible degraded indicator |
| Hourly anomalies fire every night on low volume | `ANOMALY_MIN_BASELINE` floor, surfaced as "insufficient volume" |
| Hourly and daily share a threshold and one of them is useless | Separate `k` per grain |
| Desktop notification permission is denied and alerting silently stops | Permission state surfaced in the UI with a re-request affordance |
| "Real-time" claimed for a 6-hour batch | Freshness contract rendered on every dashboard |

## 5. Testing

- **Alert rules** (`test_alert_rules.py`): defaults; per-agent override wins;
  scope filtering (`mine` does not fire for another agent's conversation);
  disabled rule is silent.
- **Event subscription** (fork tests): a new inbound message raises the
  configured modalities; an outgoing message raises nothing; a private note
  raises nothing.
- **Degradation**: stream unavailable → poll path active and the indicator shown.
- **Anomaly hourly** (`test_anomaly_hourly.py`): same-hour-across-days baseline;
  a lunchtime dip is not an anomaly; below-floor hours suppressed and labelled;
  separate `k` honoured.
- **Freshness** (`test_freshness_contract.py`): every dashboard response carries
  `as_of` and `source`; the batch sources report `batch_6h`.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `INBOUND_ALERTS_ENABLED` | `false` | Off = today's upstream-default behaviour |
| `ALERT_RULES_ENABLED` | `false` | Off = no rule store, no admin page |
| `ANOMALY_HOURLY_ENABLED` | `false` | Off = today's daily grain only |
| `ANOMALY_HOURLY_ZSCORE_K` | `3.5` | Hourly threshold |
| `ANOMALY_MIN_BASELINE` | `5` | Suppress below this hourly baseline |

## 7. Requirements closed

3.1.1, 3.1.7, 4.1, 4.2, 4.79 — and it materially improves 2.2.3, whose
"real-time" claim becomes an accurate freshness statement rather than an
overstatement. Genuine real-time reporting is a streaming-ingest project and is
not in this programme.
