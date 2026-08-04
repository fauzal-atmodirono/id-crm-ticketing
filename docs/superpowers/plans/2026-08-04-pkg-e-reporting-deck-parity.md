# Package E — Reporting Deck Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator read every number in Proton's weekly and monthly decks off two screens, instead of collating them by hand.

**Architecture:** Most of the data already exists — the 2026-08-03 metrics run built `VolumeByTypeDivisionRow`, `CategoryByVehicleModelRow`, `DealerEscalationRow`, `CaseAgingRow`, `CallCentreMetrics` and `SlaBucketRow`, all exposed under `/metrics/*`. The work is presentation plus five data gaps. This plan covers **G1 (period granularity and comparison) and the Weekly Report page**, which is the smallest increment that produces a usable deliverable; the remaining gaps are sequenced at the end.

**Tech Stack:** Python 3.12, FastAPI, BigQuery (`google-cloud-bigquery`), pytest, Vue 3 (Chatwoot fork).

**Spec:** `docs/superpowers/specs/2026-08-04-pkg-e-reporting-deck-parity-design.md`

## Global Constraints

- **Reconciliation is the acceptance test.** Every section must be checked against the corresponding slide in the June monthly deck and the 17-23 July weekly deck. If the numbers do not match, the report is wrong — a page that renders is not a passing result.
- **Never string-format dates into SQL.** Use BigQuery named query parameters. Beyond injection, `_block()` currently runs `SELECT *` with no predicate, and fetching everything to filter in Python becomes a full-table scan on every page load.
- Omitted period arguments must preserve today's behaviour exactly, so existing callers keep working.
- A failed or missing view already degrades to an empty block rather than a 500 (`query_adapter._block`). **Keep that.**
- The CRM produces **on-screen dashboards only**. Generating the PPTX was explicitly decided against; a "just add an export" request reopens the spec rather than being absorbed.
- Any definitional difference (what counts as a "case", when the clock starts) must be written on the page itself.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/metrics/period.py` | Create: period parsing, bucketing, previous-window calculation, delta maths |
| `backend/.../features/metrics/test_period.py` | Create: its tests — the highest-value tests in the package |
| `backend/.../features/metrics/query_port.py` | Modify: `PeriodRange` and period-aware protocol methods |
| `backend/.../features/metrics/query_adapter.py` | Modify: parameterised, range-filtered queries |
| `backend/.../features/metrics/insights_router.py` | Modify: `from` / `to` / `granularity` query parameters |
| `deploy/chatwoot-fork/patches/0044-weekly-report.patch` | Create: the Weekly Report page |

---

### Task 1: Period arithmetic

Pure functions, no I/O. Every week-over-week percentage in the deck depends on this being right, so it gets tested hardest.

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/period.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_period.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) class PeriodRange: start: date; end: date; granularity: str`
  - `def parse_period(from_: str | None, to: str | None, granularity: str | None) -> PeriodRange | None` — `None` when all arguments are absent, meaning "today's behaviour"
  - `def previous_period(period: PeriodRange) -> PeriodRange`
  - `def delta_pct(current: float, previous: float) -> float | None`
  - `def bucket_key(d: date, granularity: str) -> str`
  - Tasks 2-4 all consume these.

- [ ] **Step 1: Write the failing tests**

```python
"""Period arithmetic. Every 'up 24%' figure in the weekly deck comes from here."""

from __future__ import annotations

from datetime import date

import pytest

from chatbot.features.metrics.period import (
    PeriodRange,
    bucket_key,
    delta_pct,
    parse_period,
    previous_period,
)


def test_absent_arguments_mean_no_period_filter():
    assert parse_period(None, None, None) is None


def test_parses_an_explicit_week():
    p = parse_period("2026-07-17", "2026-07-23", "week")
    assert p == PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")


def test_rejects_an_inverted_range():
    with pytest.raises(ValueError):
        parse_period("2026-07-23", "2026-07-17", "week")


def test_rejects_an_unknown_granularity():
    with pytest.raises(ValueError):
        parse_period("2026-07-17", "2026-07-23", "fortnight")


def test_previous_period_is_the_immediately_preceding_window_of_equal_length():
    p = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    assert previous_period(p) == PeriodRange(date(2026, 7, 10), date(2026, 7, 16), "week")


def test_previous_period_of_a_full_month_is_the_prior_month():
    p = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")
    assert previous_period(p) == PeriodRange(date(2026, 5, 1), date(2026, 5, 31), "month")


def test_delta_matches_the_weekly_deck():
    # 297 inquiries vs 240 the prior week is the deck's "up 24%".
    assert round(delta_pct(297, 240)) == 24


def test_delta_from_zero_is_undefined_not_infinite():
    assert delta_pct(10, 0) is None


def test_delta_of_zero_to_zero_is_zero_not_undefined():
    assert delta_pct(0, 0) == 0.0


def test_week_buckets_do_not_split_across_a_month_boundary():
    # 2026-07-30 and 2026-08-01 fall in the same ISO week.
    assert bucket_key(date(2026, 7, 30), "week") == bucket_key(date(2026, 8, 1), "week")


def test_month_buckets_are_year_month():
    assert bucket_key(date(2026, 6, 15), "month") == "2026-06"
```

- [ ] **Step 2: Run and watch fail**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/metrics/test_period.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Use `date.isocalendar()` for week bucketing so the month-boundary test passes — a naive `strftime("%Y-%W")` splits that week and produces two wrong buckets. `delta_pct` returns `None` when `previous == 0` and `current != 0`, and `0.0` when both are zero; anything else renders as an infinite percentage on screen.

- [ ] **Step 4: Run and watch pass**

Expected: 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/metrics/period.py backend/apps/backend/src/chatbot/features/metrics/test_period.py
git commit -m "feat(metrics): period parsing, bucketing and delta arithmetic"
```

---

### Task 2: Range-aware query adapter

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/query_adapter.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/query_port.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_query_adapter.py`

**Interfaces:**
- Consumes: `PeriodRange` (Task 1).
- Produces: `MetricsQueryPort` methods accept `period: PeriodRange | None = None`; `None` runs today's unfiltered query.

- [ ] **Step 1: Write the failing tests**

Assert: with `period=None` the emitted SQL is unchanged from today (a regression guard on the whole existing dashboard); with a period, the SQL carries a `WHERE` clause and **named query parameters** rather than interpolated literals; the parameters carry the right start and end dates; and a query failure still yields an empty block rather than raising.

Assert the absence of interpolation directly — something like `assert "2026-07-17" not in emitted_sql` — because that is the property that matters and it is easy to regress.

- [ ] **Step 2: Run and watch fail**, then implement `_block` to accept an optional predicate plus `bigquery.ScalarQueryParameter` values via `QueryJobConfig`, then re-run until green.

- [ ] **Step 3:** Widen the views that are keyed on `month` so they expose a date column and let the query group by week or month. **Prefer widening over creating a parallel set of weekly views** — a second view set doubles the maintenance and the two drift.

- [ ] **Step 4: Run the full metrics suite and commit**

```bash
.venv/bin/pytest src/chatbot/features/metrics/ -q
git commit -m "feat(metrics): parameterised date-range queries with week and month granularity"
```

---

### Task 3: Period parameters on the insights endpoints

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/insights_router.py`
- Modify: `backend/apps/backend/src/chatbot/features/metrics/test_insights_router.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: every `/metrics/*` insights endpoint accepts `from`, `to`, `granularity`, and returns `{"current": ..., "previous": ..., "deltas": ...}` when a period is supplied, or today's bare shape when it is not.

- [ ] **Step 1: Write the failing tests** — covering the unchanged no-period shape, the wrapped with-period shape, a `400` on an invalid range, and that deltas are computed in the API layer so every consumer shows identical percentages.
- [ ] **Step 2: Run, implement, re-run until green.**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat(metrics): period and comparison parameters on the insights endpoints"
```

---

### Task 4: The Weekly Report page

**Files:**
- Create: `deploy/chatwoot-fork/patches/0044-weekly-report.patch`

**Interfaces:**
- Consumes: the endpoints from Task 3.
- Produces: one page, RBAC-gated by the existing `Reports` permission (patch `0028`).

- [ ] **Step 1:** Author against upstream `v4.15.1` — same clone-and-apply procedure as Package B Task 6.
- [ ] **Step 2:** Lay the sections out in the **weekly deck's own order**, with a week picker and a week-over-week delta beside every headline number.
- [ ] **Step 3:** **Reuse** the components patch `0034` already added — dealer escalation, SLA compliance, WIP aging — rather than duplicating them. The work here is composition.
- [ ] **Step 4:** Show an explicit empty state per section. An empty section must read as "no data for this period", never as a zero.
- [ ] **Step 5:** Put the definitions on the page — what counts as a case, when the clock starts — so a number can be argued about from shared assumptions.
- [ ] **Step 6:** Verify the patch applies from a clean clone alongside the whole stack, then commit.

---

### Task 5: Reconcile against the client's own decks — the real acceptance gate

- [ ] **Step 1:** Set the picker to **17-23 July 2026** and compare against the weekly deck: total inquiries **297**, week-over-week **+24%**, channel mix WhatsApp **73%** / phone **16%** / email **9%** / social **2%**, and the division split (Sales 49, Aftersales 47, Apps 39, Charging 26, Product 9, Marketing 9, Others 85).
- [ ] **Step 2:** For every figure that does not match, determine whether the definition differs or the query is wrong. **Write the finding down either way** — a definitional difference is a client conversation, not a bug to silently absorb.
- [ ] **Step 3:** Repeat for June against the monthly deck: total cases **1811**, inquiries **1024**, complaints **770**, feedback **17**, escalated to dealers **353**, to HQ **245**.
- [ ] **Step 4:** Do **not** show this to Proton until reconciliation passes or every discrepancy is explained. Numbers that contradict their own deck destroy trust faster than a missing feature.
- [ ] **Step 5:** Record the reconciliation results in the spec, then commit.

---

## Remaining gaps, in sequence

| Gap | Blocked on | Note |
|---|---|---|
| **G3** — `vehicle_no`, `purchased_from`, `delay_reason` on cases | nothing | Add as conversation custom attributes, surface in the sidebar, map in `mapping.py`, extend the aging/lifecycle views. Historic cases stay blank; render blanks rather than breaking, and be honest that backfill is manual. |
| **G4** — targets and the control-item table | nothing | Store-backed CRUD + fork page, exactly like the escalation-routing admin. Seed with the decks' own targets: AHT ≤ 5 min, SLA within 20s = 100%, resolution ≤ 4 working days, abandoned calls = 0. Reuse `business_hours.py` for "working days". |
| **Monthly Report page** | G3, G4 | Same shape as Task 4 with month granularity, the 6-month trend, and the control-item summary. |
| **G2** — telephony metrics | **Package C** | Abandoned calls, SLA-within-20s, AHT and AQT do not exist anywhere; they need Twilio call data. |
| **G6** — escalation compliance | **Package G** | Needs Package G to emit per-step events. Contractually significant — see spec §4.1. |
| **G5** — AI "Remarks" summaries | nothing | Last. Flag-gated, cached per period, always labelled as AI-generated. Never present generated text as an operator's own remark. |
