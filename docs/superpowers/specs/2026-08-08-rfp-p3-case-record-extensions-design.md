# P3 — Case Record Extensions & Case-State Warehouse Sync

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p3-case-record-extensions.md`
**Closes:** 9 PARTIAL requirements
**Effort:** 2 weeks · **Wave:** 1 · **Blocks:** P4 (do not reorder)
**Open question:** Q3 (who writes the WIP remarks), Q5 (what "escalated to HQ" means)

---

## 1. The problem, precisely

This package closes the most concrete class of gap in the whole analysis: **the
client's own report decks print columns that do not exist in the data model.**

Appendix C2 page 13 prints a WIP case table with these columns:

> Case ID · Concern · Status · Channel · Customer name · **Model** · **Plate
> number** · **Chassis number** · Aging days · **Purchased dealer** · Remarks
> (Issue / Action Taken / Next action)

Of those, `vehicle_model` exists. **Plate number, chassis number and purchased
dealer do not exist anywhere in the data model** — chassis/VIN does not appear
in the codebase at all — and the three-part Remarks narrative has no field.
Appendix C1 page 45 asks for the same shape plus a delay reason.

Separately, two structural mismatches make existing data unreportable:

**`case_detail` never reaches the warehouse.** Commit `42f1d66` added the
Level-2 taxonomy as a Chatwoot custom attribute (patch `0050`) — 164 distinct
values, the full Appendix A depth. `CONVERSATIONS_SCHEMA` has `category` and
`subcategory` and **no `case_detail` column**. Both C1 and C2 print Level-2
concern breakdowns ("Home Charging → Assessment 23"). Those slides cannot be
reproduced from the warehouse today, despite the data being captured on every
case.

**`CaseState` never reaches the warehouse either.** `features/chat/case_state.py`
defines `NEW, OPEN, WIP, PENDING, TEMP_CLOSED, SOLVED` with audited transitions,
stored on the conversation as the `case_state` custom attribute. But
`mapping.py` populates the BigQuery `status` column from the **Chatwoot** status
(`open`/`pending`/`resolved`/`snoozed`). So `WIP` and `TEMP_CLOSED` — two of the
four series §4.77 asks to trend — are captured on every case and are invisible
to every report. `v_case_aging` filtered to `open`+`pending` is used as a WIP
proxy, and it is a proxy for a value the system actually knows.

The shape of this package is therefore: **stop discarding two fields we already
have, and add five we do not.**

## 2. What this package delivers

1. `case_detail` in the warehouse, with the Level-1 → Level-2 nesting the decks
   print.
2. `case_state` in the warehouse as its own column, alongside — not replacing —
   the Chatwoot status.
3. Five new case fields: plate number, chassis/VIN, purchased-from dealer, delay
   reason, and the three-part WIP remarks.
4. An escalation-target dimension with room for "HQ" (Q5), added unpopulated
   rather than guessed.
5. The views that consume them.

## 3. Design

### 3.1 Where the new fields live

**Chatwoot conversation custom attributes**, following the pattern every other
case field in this system already uses (`case_state`, `case_detail`,
`dealer_escalated_at`, `sla_minutes`).

| Attribute | Type | Source | Requirement |
|---|---|---|---|
| `vehicle_plate` | string | agent-entered, or DMS when it exists | C1-10, C2-09 |
| `vehicle_chassis` | string | agent-entered, or DMS | C2-09, and 4.25 dedup later |
| `purchased_from_dealer` | string (dealer slug) | agent-entered, or DMS | C1-10, C2-09 |
| `delay_reason` | string | agent-entered | C1-10 |
| `wip_issue` | string | agent-entered | C2-09 |
| `wip_action_taken` | string | agent-entered | C2-09 |
| `wip_next_action` | string | agent-entered | C2-09 |
| `escalated_to` | enum `dealer` / `hq` / `none` | derived from labels + Q5 | C1-07 |

Not a separate table, and the reason is worth stating: every consumer in this
system — the Cases list (patch `0043`), the metrics sync, the Customer 360 page,
the escalation notifier — already reads conversation custom attributes. A
side table would need a join in each of them and a second write path to keep
consistent. The cost of the custom-attribute approach is that these fields are
strings in a schemaless bag; the mitigation is that `mapping.py` is the single
place they are typed, and it is tested.

**`purchased_from_dealer` is a dealer slug, not free text**, and validates
against `DealerStore`. C1-09's dealer turnaround analysis is only meaningful if
the dealer identifier reconciles with the one the escalation path uses. Free
text here would produce "Proton Glenmarie", "Glenmarie", and "PROTON GLENMARIE"
as three dealers by the second week.

### 3.2 The WIP remarks (open question Q3)

The three-part Remarks column is the one field whose *design* depends on a
client answer: is it free text an agent types, a structured template, or
generated?

**Assumption, chosen to be cheap to reverse:** three separate free-text
attributes (`wip_issue`, `wip_action_taken`, `wip_next_action`), operator-entered
via a fork-side panel on the conversation, with no AI generation.

Three separate fields rather than one blob, because the decks print them as
three labelled lines and because a single field would make "which cases have no
next action" — the actually useful query — impossible. If PRO-NET answers that
they want it generated, the fields stay and a generator writes them; if they
want a template, the fields stay and the UI constrains them. **Every answer to
Q3 keeps this schema.** That is the point of choosing it.

### 3.3 `case_state` in the warehouse

A **new column**, `case_state`, not a redefinition of `status`.

This is the important call. Overwriting `status` with the backend case state
would silently change the meaning of every existing view — `v_state_trend`,
`v_case_aging`, `v_resolution_split` and the dashboard all read `status` today
and would start reporting a different thing under the same name, with no
migration boundary in the data. Adding a column means old views keep working,
new views read the new column, and a reconciliation meeting can see both.

```
CONVERSATIONS_SCHEMA
  status          STRING   -- Chatwoot status: open/pending/resolved/snoozed  (unchanged)
  case_state      STRING   -- CaseState: NEW/OPEN/WIP/PENDING/TEMP_CLOSED/SOLVED  (new)
  escalated_to    STRING   -- dealer/hq/none  (new, see §3.4)
```

`v_state_trend` gains a sibling, `v_case_state_trend`, reading the new column and
trending the four series §4.77 names: **Higher escalation / WIP / Temporary
Closed / Closed**. `v_case_aging` gains a `case_state` column so the WIP figure
can stop being a proxy.

Rows synced before this package have no `case_state` attribute. They map to
`NULL`, not to a default — the same nullability rule P1 applies to its flags, and
for the same reason: defaulting historical rows to `OPEN` would fabricate
history.

### 3.4 "Escalated to HQ" (open question Q5)

C1 page 35 shows 245 HQ escalations in June. The concept does not exist in the
case model, and the gap analysis is explicit that it "needs a product decision
before it can be scoped".

**This package adds the dimension and does not invent the workflow.** An
`escalated_to` column with values `dealer` / `hq` / `none`, derived from the
existing `dealer_*` labels (→ `dealer`) and defaulting to `none`. No case will
be classified `hq` until PRO-NET answers Q5, and the views that group by it will
correctly show zero.

That is the honest position: the *reporting shape* C1-07 needs is in place, the
column is real, and the value is empty because the workflow that would populate
it has not been defined. A guess here — say, treating any `dept_*` label as an
HQ escalation — would produce a number that looks right and is wrong, which is
worse than a zero.

### 3.5 `case_detail` through the pipeline

Mechanically the smallest item and the one with the widest report impact:

1. `mapping.py` reads `custom_attributes.case_detail` into the row.
2. `CONVERSATIONS_SCHEMA` gains `case_detail STRING NULLABLE`.
3. `v_category_by_vehicle_model` extends to
   `category × subcategory × case_detail × vehicle_model × case_type`.
4. A new `v_concern_pivot` renders the Level-1 → Level-2 nesting with grand
   totals that C2-03 and C2-06 print.

The gap analysis estimated ~3 days for this alone. It is bundled here because it
touches the same three files as everything else in P3, and touching
`bigquery_schema.py` twice for two reasons in the same fortnight is how view
definitions drift.

### 3.6 Where the data comes from

An honest note that belongs in the client conversation, not buried in code:
plate, chassis and purchased-from are **DMS fields**. Until R11 delivers a real
DMS adapter, they are agent-entered, and the coverage on any report built from
them will be whatever the agents typed.

The design makes that visible rather than hiding it: every view that groups by
these fields reports a **coverage percentage** alongside the figure. A WIP table
where 60% of rows have no plate number should say so on the slide, not print 40%
of the cases and let the reader assume it is all of them.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Overwriting `status` breaks every existing view | Not done — `case_state` is a new column; §3.3 |
| Historical rows default to a fabricated state | Nullable, mapped to `NULL`; asserted in tests |
| Free-text dealer names fragment the dealer dimension | `purchased_from_dealer` validates against `DealerStore` |
| A guessed HQ definition produces a plausible wrong number | `escalated_to` ships with no `hq` classifier at all until Q5 is answered |
| Sparse agent-entered fields make reports look complete when they are not | Coverage percentage on every view that uses them |
| Q3 comes back as "generated, not typed" and the schema is wrong | Three free-text fields survive every possible answer to Q3; §3.2 |

## 5. Testing

- **Mapping** (`test_mapping.py` additions): each new attribute maps to its
  column; absent attributes map to `NULL` not to a default; a malformed value
  degrades to `NULL` and logs rather than failing the row.
- **Schema** (`test_bigquery_schema.py`): new columns present and nullable;
  every column referenced by a new view exists (the suite's existing
  consistency guard).
- **Case state** (`test_case_state_sync.py`): `status` unchanged for every
  existing fixture; `case_state` populated independently; a conversation with
  both reports both.
- **Dealer validation** (`test_case_fields.py`): a slug not in `DealerStore` is
  rejected at write time with a usable error, not silently stored.
- **Coverage** (`test_coverage_metrics.py`): a view over rows with 3 of 5 plates
  populated reports 60%.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `CASE_EXTENDED_FIELDS_ENABLED` | `false` | Off = the fork panel is hidden, nothing written |
| `CASE_STATE_SYNC_ENABLED` | `false` | Off = `case_state` column stays NULL |
| `REPORT_COVERAGE_DISCLOSURE` | `true` | On = views emit coverage percentages |

The third defaults **on**, unlike everything else in this programme. A coverage
figure is a statement about data quality; suppressing it by default would make
the honest behaviour the opt-in one.

## 7. Requirements closed

4.37, 4.62, C1-04, C1-05, C1-06, C1-10, C2-03, C2-06, C1-12 #9 — and it unblocks
C2-09 (GAP) apart from the parts that need a real DMS, and **4.77** once P4 adds
the date handling its trend needs.
