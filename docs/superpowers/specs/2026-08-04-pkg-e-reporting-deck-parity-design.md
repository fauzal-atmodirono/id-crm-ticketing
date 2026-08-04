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

G1 and G3 are the ones that block a credible weekly report. G2 is genuinely
blocked on Package C. G4 is small. G5 is a nice-to-have and should be last.

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
6. G5 (remarks) — optional

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
