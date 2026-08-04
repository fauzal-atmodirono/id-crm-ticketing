# Package E — Reporting parity with Proton's weekly & monthly decks

**Date:** 2026-08-04
**Covers demo-feedback items:** #3 in the 2026-08-04 request; feedback items #5 and #29 (Proton to share report examples — **now supplied**, so those two stop being blocked-on-client and become buildable)
**Source of truth:** `docs/client-materials/MONTHLY REPORTING FOR Proton e.MAS.pptx` (54 slides) and `docs/client-materials/Weekly Report Proton e.MAS.pptx` (16 slides)
**Decision taken:** on-screen dashboards only. The CRM does **not** generate the PowerPoint deck.
**Effort:** large — but far less than it looks, because most of the data already exists.

---

## 1. Goal

An operator opens the CRM and reads, on screen, the numbers they currently
spend days collating by hand into those two decks. They still build the deck;
they stop hunting for the figures.

## 2. What the decks actually require

Extracted from both files, in deck order:

**Monthly (54 slides)**
1. Case analysis by channel — total cases split Inquiry / Complaint / Feedback
2. Incoming call performance — total inbound, abandoned, SLA within 20s, AHT, AQT
3. Inquiry trend over 6 months, by division and vehicle model
4. Inquiry detail tables — category, subcategory, case count, per vehicle model, plus a free-text "Remarks" column summarising themes
5. Complaint detail tables — same shape
6. Compliment/feedback tables — same shape
7. Cases escalated to dealers — totals split dealer / HQ / none
8. Complaint trend by dealer
9. Average turnaround by dealer, slowest resolutions with reasons
10. WIP case tables — Case ID, division, concern, purchased from, escalated to, **car plate**, duration in days, reason for delay
11. RSA call performance and incident tables — incident date, vehicle no, model, dealership, called-in / towing-assigned / arrival times, total km, late reason
12. Summary control-item table — each metric vs its target, current month and YTD

**Weekly (16 slides)** — the same cuts over a 7-day window, plus:
13. Week-over-week change percentages on every headline number
14. Outstanding cases by aging bucket (1-3, 4-6, 7+ days)
15. Average resolution leadtime in **working days** against a 4-day target
16. Per-case complaint detail — Case ID, status, channel, customer info, aging, purchased dealer, remarks

## 3. What already exists (verified in code)

The 2026-08-03 reporting-metrics run built most of the data layer.
`features/metrics/query_port.py` already defines, and
`features/metrics/insights_router.py` already exposes:

| Endpoint | Data | Serves deck section |
|---|---|---|
| `/metrics/volume-by-type` | `month, channel, case_type, division, volume` | 1, 3 |
| `/metrics/departments` | `category, subcategory, vehicle_model, case_type, cases` + dept/PIC + reopen | 4, 5, 6 |
| `/metrics/dealer-escalation` | per-dealer counts, avg/p50/p90 turnaround days, slowest cases | 7, 8, 9 |
| `/metrics/case-aging` | per-case type, division, dealer, PIC, status, created_at, age_days, bucket | 10, 14, 16 |
| `/metrics/sla-buckets` | SLA attainment by case type | 15 (partly) |
| `/metrics/lifecycle` | per-case timings, reopen counts, state trend | 15, 16 |
| `/metrics/callcenter` | SLA, first response, resolution time, complaint types, peak hours, NPS | 15 |
| `/rsa/incidents` + `/aggregate` | full RSA incident records | 11 |

Fork pages already exist too (`0020-reports-native-merge`,
`0034-reporting-extensions`, `0035-rsa-incident-log`), including sections for
dealer escalation, SLA compliance, and WIP aging.

**So this package is mostly presentation plus five genuine data gaps.**

## 4. The five real gaps

| # | Gap | Why it matters | Fix |
|---|---|---|---|
| **G1** | **No week granularity and no period comparison.** Every volume row is keyed by `month`; nothing computes week-over-week deltas. | The entire weekly deck, and every ↑24% figure in it. | Parameterise the queries by date range + granularity, and compute a comparison window. |
| **G2** | **No telephony metrics.** Abandoned calls, SLA-within-20s, AHT and AQT don't exist anywhere — the current call-centre view is partly a placeholder (`ProtonCallCentrePlaceholder.vue`). | Monthly section 2 and the control-item table. | Ingest Twilio call data. **Depends on Package C.** |
| **G3** | **Case rows lack car plate, purchased-from dealer, and delay reason.** `CaseAgingRow` and `CaseLifecycleRow` have division/dealer/PIC but no plate. | Every WIP/outstanding table Proton actually circulates. | Add the fields to the CRM data (custom attributes) → sync → views. |
| **G4** | **No targets, so no control-item table.** Nothing stores "AHT target = 5 min" or "resolution ≤ 4 working days". | Monthly section 12, the summary the client reads first. | Operator-editable targets, compared against actuals. |
| **G5** | **No "Remarks" theme summaries.** The decks' remarks columns are human-written thematic summaries. | Sections 4-6. | Optional AI summarisation over each category's cases; flag-gated, clearly labelled as generated. |
| **G6** | **No escalation-policy compliance metrics.** Added 2026-08-04 after reading `CRM Process Flow (1).xlsx`, which defines a 5-step escalation ladder with hard timers. Nothing measures adherence to it. | Not visible in the June/July decks, but it is a written client SOP with a **non-compliance clause**, so it will be asked for. | Track per-case escalation-step events and report against the SOP timers. Depends on **Package G** producing those events. |

G1 and G3 are the ones that block a credible weekly report. G2 is genuinely
blocked on Package C, and G6 on Package G. G4 is small. G5 is a nice-to-have
and should be last.

### 4.1 What G6 needs to report (from the client's escalation SOP)

| Metric | SOP target |
|---|---|
| Case created in CRM after first contact | ≤ 10 minutes |
| Dealer acknowledges the escalation email | ≤ 2 working hours |
| 1st reminder issued after no response | at 4 working hours |
| 2nd reminder issued after continued silence | at a further 4 working hours |
| Final (telephone) escalation | at cumulative 8 working hours |
| Dealer response after the phone call | ≤ 1 hour |
| Agent updates the customer | ≤ 4 working hours from receipt |

Per-dealer breach counts feed the **non-compliance under the Daily Complaint
Clause** that the SOP names, which makes this reporting contractually
significant rather than merely informative. Treat the numbers accordingly: they
should be reconciled and defensible before anyone acts on them.

## 5. Design

### 5.1 Structure — mirror the decks, don't invent a new IA

Two new report views, named for what the client already calls them:

- **Weekly Report** — one page, sections in the weekly deck's order, with a
  week picker and every headline carrying its WoW delta.
- **Monthly Report** — one page, sections in the monthly deck's order, with a
  month picker and MoM deltas, plus the 6-month trend and the control-item
  summary.

Both are read-only dashboards under Reports, RBAC-gated by the existing
`Reports` permission (patch `0028`). Sections reuse the components built by
patch `0034` where they already match (dealer escalation, SLA compliance, WIP
aging) rather than duplicating them — the work is composition and the missing
sections, not a rewrite.

Rejected alternative: extending the existing scattered report pages in place.
It leaves the operator assembling the deck from six screens, which is the
problem we're solving.

### 5.2 Period handling (G1)

Today `MetricsQueryAdapter._block` runs a bare `SELECT * FROM <view>` per view
and returns everything. That has to become range-aware:

- Every insights endpoint accepts `from`, `to`, and `granularity`
  (`week` | `month`), defaulting to today's behaviour when omitted so existing
  callers don't break.
- Queries use **BigQuery named parameters** for the range. Do not string-format
  dates into SQL, and do not fetch everything and filter in Python — the case
  tables grow without bound and the second approach quietly becomes a
  full-table scan on every page load.
- Comparison windows are computed by the caller: request the current period and
  the immediately preceding one of the same length, and derive deltas in the
  API layer so every consumer shows the same percentages.
- Views keyed on `month` need a week-capable equivalent. Prefer widening the
  views to expose a date column and letting the query group by week or month,
  over creating a parallel set of weekly views to maintain.

### 5.3 Case-detail fields (G3)

Car plate, purchased-from dealer, and delay reason must exist on the case before
they can be reported. They're conversation custom attributes:

- `vehicle_no` — also fixes the Customer 360 approximation noted in Package B
- `purchased_from` — dealer of purchase, distinct from `dealer` (escalated-to)
- `delay_reason` — free text, agent-entered on long-running cases

Add them to the conversation sidebar (fork patch), to
`features/metrics/mapping.py`, to the sync, and to the aging/lifecycle views.
Historic cases will have them blank; the tables must render blanks rather than
break, and the UI should be honest that backfill is manual.

### 5.4 Targets and the control-item table (G4)

A small operator-editable target set (metric key → target value + unit),
following the exact pattern of the escalation-routing admin page: a
store-backed CRUD router with `require_permission`, plus a fork page. Seed with
the targets visible in the decks (AHT ≤ 5 min, SLA within 20s = 100%,
resolution ≤ 4 working days, abandoned calls = 0). The control-item table then
renders metric / target / this period / YTD, flagging misses.

"Working days" already has a home in `features/metrics/business_hours.py` —
reuse it rather than adding a second calendar.

### 5.5 Remarks summarisation (G5) — last, and clearly labelled

For each category in the detail tables, summarise its cases into two or three
themed bullets using the existing Gemini path, cached per period so a page load
doesn't re-summarise. Flag-gated, off by default, and rendered with an explicit
"AI-generated summary" marker. Never present generated text as an operator's
own remark.

## 6. Sequencing

1. G1 (period + comparison) — unlocks the weekly report at all
2. Weekly Report page over existing data + G1
3. G3 (case fields) — makes the WIP/outstanding tables real
4. Monthly Report page + G4 (targets/control-item)
5. G2 (telephony) — **after Package C**, wired into the call-performance section
6. G6 (escalation compliance) — **after Package G**, which emits the events
7. G5 (remarks) — optional

Each step is independently demoable. If the work stops after step 2, Proton
still gets a usable weekly view.

## 7. Testing

- Query layer: named-parameter ranges produce the right windows; week and month
  granularity bucket correctly across month boundaries; an empty period returns
  empty sections rather than 500ing (the existing adapter already degrades a
  failed view to an empty block — keep that).
- Delta computation: current vs previous period, including a previous period of
  zero (no division-by-zero percentages).
- Targets: CRUD, permission enforcement, missing target renders as "no target"
  rather than 0.
- Sections render with missing `vehicle_no` / `purchased_from` on legacy cases.
- Manual: reconcile one section against the corresponding slide in the June
  monthly deck and the 17-23 July weekly deck. **If the numbers don't
  reconcile, the report is wrong — that check is the acceptance test, not a
  nice-to-have.**

## 8. Risks

- **Numbers that don't match the client's own deck destroy trust faster than a
  missing feature.** Reconciliation against the supplied decks is mandatory
  before showing this to Proton, and any definitional difference (what counts
  as a "case", when the clock starts) must be written down on the page itself.
- **Demo-seeded data (Package D) inflates these reports.** Decide the exclusion
  policy before seeding.
- **BigQuery cost** rises once pages query ranges on demand; cache per period.
- **Scope creep into deck generation.** The decision is dashboards only. A
  "just add a PowerPoint export" request should reopen this spec, not be
  absorbed silently.

## 9. Out of scope

- Generating the PPTX/PDF deck (explicitly decided against).
- Narrative slides — key issues, suggestions, dealer commentary. Human analysis.
- PowerBI embedding (feedback #5 — still open; on-screen native reporting is
  the answer for now, and Proton should be told that explicitly).
- Scheduled email delivery of reports.

## 10. Definition of done

An operator can produce every number in the weekly deck and every number in the
monthly deck except the telephony block (pending Package C) from two screens,
the figures reconcile against the two supplied decks for a known period, and
the coverage doc's items #5/#29 move off "waiting on Proton" now that the
examples have arrived.

## 11. Task 5 — deck reconciliation (2026-08-04)

The plan's Task 5 acceptance gate is a *live* comparison: set the Weekly
Report's week picker to 17-23 July 2026 and the Monthly Report's month picker
to June 2026, against a real tenant's BigQuery data, and check the screen
against the two supplied decks. This checkout has no BigQuery credentials for
any tenant and `MetricsQueryPort`'s mock fallback (`MockMetricsQuery`) ignores
period filtering entirely — every period-scoped request against it returns
the same canned all-time payload — so that comparison cannot happen here.
Task 5 instead builds the piece that *can* be done offline and is a
prerequisite for the live check meaning anything: proof that the arithmetic
chain (BigQuery row → adapter dataclass → router delta/scope → what the page
would render) is correct, using the deck's own published figures as the
expected output. If the live check later disagrees with real data, that
disagreement is a data or definitional question, not a maths bug — because
the maths itself is pinned here.

### 11.1 What the reconciliation tests prove, and what they don't

New file: `backend/apps/backend/src/chatbot/features/metrics/test_deck_reconciliation.py`
(7 tests, all passing). Each test wires the *real* `BigQueryMetricsQuery`
adapter (§5.2's `_day_grain_block_for_period`/`_volume_block_for_period`,
i.e. the actual dict → dataclass row mapping and day-grain SQL construction)
into the *real* `build_metrics_insights_router` (the actual current/previous
`asyncio.gather` fan-out, the actual `delta_pct` computation, the actual
`BlockScope` plumbing) and drives it through a real `TestClient` HTTP round
trip — not a hand-rolled reimplementation of any of those three layers.

Proven, through that real path:

- **Weekly total: 297** for 17-23 July 2026 (`v_volume_by_type_division_daily`
  rows summed by the router's own `current.volume` payload).
- **Weekly WoW delta: +24%** — 297 vs. a previous-week total of 240, computed
  by the router's real `delta_pct`, matching `test_period.py`'s own
  297-vs-240 fixture (this task's numbers were not invented; they anchor to
  what Task 2/3 already pinned at the arithmetic-primitive level).
- **Weekly channel mix: WhatsApp 73% / Phone 16% / Email 9% / Social 2%** —
  a 217/48/27/5 split (sums to 297), share computed sum-then-divide in the
  same operation order as `ProtonWeeklyReport.vue`'s `channelMix` computed
  property (fork patch `0044`), not a per-row average.
- **Weekly division split: Sales 49 / Aftersales 47 / Apps 39 / Charging 26 /
  Product 9 / Marketing 9 / Others 85** — summed from the router's real rows,
  grouped by division. Uses its own fixture, separate from the total/
  channel-mix one — see the discrepancy log (§11.3): the division split sums
  to 264, not 297, and that gap is in the deck itself, not in this code.
- **Monthly case-type totals, June 2026: Inquiry 1024 / Complaint 770 /
  Feedback 17**, summing to the deck's total cases 1811, through the same
  adapter/router path at month granularity (the day-grain view and the
  router's fan-out are granularity-agnostic, per `query_adapter.py`'s module
  docstring — nothing month-specific had to be added for this).

What these tests do **not** prove:

1. **That BigQuery's own SQL executes correctly.** The fake BigQuery client
   (`_PeriodKeyedFakeClient`) returns pre-aggregated canned rows keyed by
   `(view, period-start)`; it does not execute the emitted
   `WHERE day BETWEEN @start AND @end` / `GROUP BY` itself. This is the same
   limitation every other adapter test in this package already has (see
   `test_query_adapter.py`'s own comment on this) — SQL *construction* is
   tested (elsewhere, extensively), SQL *execution* against real data is not.
2. **That Proton's real conversations produce these numbers.** The rows fed
   in are synthetic, chosen to land on the deck's published figures. A tenant
   whose real data does not is not a bug this test would catch — that's
   exactly what the live check in §11.2 is for.
3. **The channel-mix share and the division-split total are not computed by
   any backend code.** `sharePct` is computed client-side in Vue
   (`ProtonWeeklyReport.vue`'s `channelMix`), and there is no backend
   rollup-by-division at all — the page renders `volumeByTypeDivision`, the
   top-10 `(channel, case_type, division)` rows by volume, unaggregated. The
   test helpers (`_channel_shares`, `_division_totals`) are a controlled
   Python reimplementation of the page's arithmetic (same sum-then-divide
   order), not an execution of the Vue code — Python can't run that. This is
   also a real UI gap worth carrying forward: with more than 10 distinct
   `(channel, case_type, division)` combinations, which any real week has,
   the page cannot display enough rows for a human to even hand-sum the
   division split from the screen. Out of scope for this task to fix (it's a
   fork-patch/Vue change); recorded here so it isn't lost.

### 11.2 What could not be pinned at all

Two June-deck figures have no test, deliberately — forcing them to pass would
have meant asserting something other than what the deck actually states:

- **"Escalated to dealers: 353."** `v_dealer_escalation` /
  `fetch_dealer_escalation` has no date column at all (see
  `MetricsQueryPort`'s docstring; `insights_router.py`'s `_reject_period`
  400s any period params sent to this endpoint —
  `test_insights_router.py::test_dealer_escalation_rejects_period_params`
  already covers the 400). `SUM(cases_escalated)` over that view is real,
  working code and could be pinned to 353 — but only as an **all-time**
  total, never "for June 2026." A test asserting 353 against an unscoped
  query would misrepresent what the number means; that's the "test bent
  until it matches" the task brief warns against. This is the G1 gap (no
  period support) not yet extended to the dealer-escalation view — worth
  scheduling alongside G3/G4 in the remaining-gaps table, since without it
  the Monthly Report's escalation section can only ever show an all-time
  figure under a "June 2026" header.
- **"Escalated to HQ: 245."** There is no "escalated to HQ" concept anywhere
  in the schema. `CONVERSATIONS_SCHEMA` and every view in
  `bigquery_schema.py` track only `dealer_escalated_at` (escalation *to a
  dealer*) — nothing records an HQ escalation timestamp, flag, or dimension.
  Unlike the dealers figure above, this isn't a period-filtering gap on an
  otherwise-real number; the underlying data doesn't exist yet. This is a
  new gap, not previously listed in §4's gap table — call it **G7**: no
  concept of "escalated to HQ" (vs. escalated to dealer) in the case model
  at all. Needs product/client input on what distinguishes an
  HQ-vs-dealer escalation before it can even be scoped.

### 11.3 Live reconciliation runbook

Steps a human takes once tenant BigQuery credentials and `ensure_views()` are
available for a real (or realistic-enough) dataset:

1. **Run `ensure_views()` first.** The two new day-grain views
   (`v_state_trend_daily`, `v_volume_by_type_division_daily`) that the Weekly
   Report's week-scoped sections depend on only exist once this has synced
   on the target deployment. Until then, `/metrics/lifecycle` and
   `/metrics/volume-by-type` will both return `status: "unavailable"` for
   their period-scoped blocks with a period filter applied, and **both
   week-scoped sections of the Weekly Report page render empty** — not an
   error, just silently nothing, per this file's fail-open contract. Confirm
   the view exists (e.g. a manual `SELECT COUNT(*) FROM
   \`<project>.<dataset>.v_volume_by_type_division_daily\` LIMIT 1`) before
   concluding the *data* is what's wrong.
2. **The week picker cannot select 17-23 July exactly.** The deck's own
   window is Friday 17 July - Thursday 23 July, not a Monday-Sunday span.
   `ProtonWeeklyReport.vue`'s week picker always snaps any picked date to
   that week's Monday and sets the end to Monday+6 — it structurally cannot
   produce this window. The closest achievable weeks in the UI are 13-19
   July and 20-26 July, both of which include/exclude different days than
   the deck. Two options, in order of preference:
   - Call `GET /metrics/volume-by-type?from=2026-07-17&to=2026-07-23&granularity=week`
     and `GET /metrics/lifecycle?from=2026-07-17&to=2026-07-23&granularity=week`
     directly (with the `x-api-key` header) to reproduce the deck's exact
     window, bypassing the picker's Monday-snap, and compare those payloads
     by hand against the deck.
   - Or use the picker's nearest week and explicitly note the day-count
     offset (up to 3 days) when explaining any mismatch — a discrepancy that
     traces to this offset is a UI limitation, not a data or definitional
     difference, and should be logged as such below rather than conflated
     with either.
3. **Set the picker (or the API call) to the period, and read the figures**
   listed in §11.1 above off the page/response. For the monthly deck, June
   2026 is a full calendar month, so the picker's month mode is exact — no
   equivalent workaround is needed there.
4. **Compare against the deck, slide by slide**, for every figure in the
   Task 5 brief (weekly: total, WoW%, channel mix, division split; monthly:
   total cases, inquiries, complaints, feedback, escalated-to-dealers,
   escalated-to-HQ). Note §11.2's two figures have no code path to compare
   against yet — record them as "not yet buildable" rather than a
   pass/fail.
5. **For every other figure that does not match**, determine whether the
   definition differs (e.g. "case" counted differently, a channel bucketed
   differently, the clock starting at a different event) or the query is
   wrong. **Write the finding down either way** in the discrepancy log below
   — a definitional difference is a client conversation, not a bug to
   silently absorb, and it does not get fixed by adjusting the query to
   match without first confirming which definition is correct.
6. **Do not show this to Proton until reconciliation passes or every
   discrepancy is explained.** Numbers that contradict their own deck
   destroy trust faster than a missing feature (§8's risk, restated here
   because it is the operative instruction for whoever runs this runbook).

### 11.4 Discrepancy log

Record every live-reconciliation finding here, whether it turns out to be a
bug or a definitional difference — nothing gets silently absorbed. Entries
from the offline pass (this task), carried forward for the live check to
confirm, refute, or extend:

| Date | Figure | Deck value | Pipeline value | Verdict | Notes |
|---|---|---|---|---|---|
| 2026-08-04 | Weekly division split total vs. weekly headline total | 297 (total) vs. 264 (Sales+Aftersales+Apps+Charging+Product+Marketing+Others) | n/a — offline arithmetic check only, no live data yet | **Open** | The deck's own two slides don't sum to each other (33-case gap). Leading hypotheses: the division-split slide filters to a specific `case_type` (e.g. Inquiry only) while the headline counts every case_type; or a rounding/manual-collation artefact in how Proton compiled the original deck. Neither confirmable without the live tenant data — check whether restricting `v_volume_by_type_division_daily` to `case_type = 'Inquiry'` closes the gap. |
| 2026-08-04 | Weekly channel mix — "Social" channel | 2% of weekly volume | n/a — offline check only | **Open, likely gap** | `mapping.py::channel_from_external_id` has no "Social" output; it only produces WhatsApp/Email/Phone/Web/Other from the Chatwoot `source_id` prefix. If Proton's real inboxes include a native Instagram/Facebook channel, that traffic is currently indistinguishable from "Other" in `channel`, or possibly not synced as a distinct value at all. Confirm during the live check whether "Social" cases exist in the tenant's `channel` column as-is, or whether this needs a mapping addition (a G3-adjacent gap, not previously listed). |
| 2026-08-04 | Week picker cannot select 17-23 July | Deck window: Fri 17 - Thu 23 Jul | Picker only offers Monday-Sunday weeks | **Confirmed gap (UI)** | See §11.3 step 2. Not a data or definitional issue — the picker's `mondayOf()` snap is a hard constraint. Direct API calls with `from`/`to` bypass it; the picker itself would need a "custom range" mode to fix properly (out of scope for Task 5). |
| 2026-08-04 | "Escalated to dealers" scoped to June | 353 | Only reachable all-time (no date column on `v_dealer_escalation`) | **Confirmed gap (G1, unextended)** | See §11.2. Needs the same day-grain-view treatment G1/Task 2 gave `v_volume_by_type_division`/`v_state_trend` before a monthly figure can be pinned or reconciled at all. |
| 2026-08-04 | "Escalated to HQ" | 245 | No code path — concept absent from schema | **Confirmed gap (new: G7)** | See §11.2. Needs product/client input (what distinguishes an HQ escalation from a dealer escalation in the workflow) before it can be designed, let alone built. |

Live-reconciliation rows (to be added once a human completes §11.3 against
real tenant data) should follow the same shape: date, figure, deck value,
pipeline value, verdict (`Match` / `Bug` / `Definitional difference` /
`Not yet buildable`), and enough notes for a client conversation if needed.
