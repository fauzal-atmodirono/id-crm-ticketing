# P5 — Targets Store, Control-Item Slide & Report Delivery

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p5-targets-and-report-delivery.md`
**Closes:** 6 PARTIAL requirements, and unlocks the target column on 8 more
**Effort:** 1.5 weeks · **Wave:** 2 · **Blocked by:** P4 (period plumbing), P1 (working-hours enforcement)

---

## 1. The problem, precisely

**There is no target store anywhere in the codebase.** This is gap G4 from the
Package E parity spec, and it is small, cheap and disproportionately valuable.

Appendix C1 page 48 is the summary control-item table — "the slide PRO-NET reads
first", and the densest single requirement in the pack. Fourteen metrics, each
with a stated target, shown for the month and year-to-date:

| # | Control item | Target |
|---:|---|---|
| 1 | Total incoming contact | — |
| 3 | QA performance (calls) | 85% |
| 4 | Response time (call) | `<20 s` |
| 5 | AHT call | `<5 min` |
| 6 | Response time (WhatsApp) | `<4 min` |
| 7 | Complaint resolution buckets | `<24wh` / 24-48 / 48-72 / `>72wh` |
| 8 | Case resolution | Inquiry `<8wh`, Complaint `<72wh`, Feedback `<48h` |
| 10 | Abandon call during working hours | `<5%` |
| 11 | RSA arrival time | `<60 min`, 95% |
| 12 | RSA AHT call | `<10 min`, 95% |
| 13 | Social media response | `<2WH`, 98% |
| 14 | Email response | `<2WD`, 98% |

Items 7 and 8 are **MET** — `RESOLUTION_SLA_TARGETS_JSON` defaults to exactly
`inquiry:[8wh]`, `complaint:[24,48,72wh]`, `feedback:[48h]`, built from these
decks. That is the proof this approach works; it just was not generalised.

For everything else, the metric exists and **cannot be shown against its
target**, because there is nowhere to put the target. A slide with a number and
an empty target column is not the slide.

Two adjacent items are the same shape:

- **RSA arrival attainment (C1-11, C1-12 #11).** `rsa_incidents` captures
  `customer_called_in_time` and `time_arrived_breakdown_area` — every timestamp
  needed. **Nothing computes the percentage** meeting the 60-minute target.
  ~3 days once a target store exists.
- **Resolution leadtime vs a 4-working-day target (C2-07).**
  `resolution_working_minutes` is stored per case. No per-division view expresses
  it in working *days*, and there is no target.

Separately, **report delivery is fixed-interval and always all-time**:
`scheduler.py::run_report_job` emails `bot-metrics.xlsx` and `bot-metrics.pdf`
every `report_interval_hours` (default 24) to one hard-coded recipient list, with
the same all-time dashboard bundle every time. "Email the June monthly report on
1 July" is not expressible. And of the five per-view reports, XLSX and PDF cover
only the `DashboardMetrics` bundle — the rest are CSV-only, with no export route
accepting a period filter.

## 2. What this package delivers

1. An operator-editable targets store.
2. An attainment comparison applied uniformly to any metric.
3. The C1 p48 control-item report, rendering every row the data supports and
   honestly blanking the rest.
4. RSA arrival attainment and per-division working-day leadtime.
5. Cron-style, period-scoped report scheduling with per-schedule recipients.
6. XLSX and PDF for the per-view reports, period-aware.

## 3. Design

### 3.1 The targets store

Firestore-backed, following the `PicStore` / `DealerStore` / `SlaPolicyRepository`
pattern this codebase already uses three times, with the same shape of admin
page (RBAC-gated on a new `targets.manage` permission).

```python
@dataclass(frozen=True)
class Target:
    key: str              # "response_time_whatsapp"
    comparator: str       # "lte" | "gte"
    value: float          # 4
    unit: str             # "minutes" | "working_hours" | "working_days" | "percent" | "seconds"
    attainment_pct: float | None = None   # 95 for "<60 min, 95%"
    scope: str | None = None              # None = tenant-wide; else division/channel/dealer
```

Three decisions worth stating:

**`unit` is explicit and includes working-hours variants.** Four of the fourteen
targets are in working hours or working days. A target store that only knows
"minutes" would force the conversion into each call site, which is how
enforcement and reporting drifted apart in the first place (P1's whole subject).
The unit travels with the target.

**`attainment_pct` is a separate field, not folded into the target.** "RSA
arrival `<60 min`" and "95% of RSA arrivals `<60 min`" are different assertions,
and rows 11 and 12 need the second. A single number cannot express it.

**`scope` allows per-division and per-dealer targets** because C2-07's
4-working-day leadtime is per division and PRO-NET will eventually want a dealer
to have its own. Tenant-wide is the default and the common case.

Seeded from `RESOLUTION_SLA_TARGETS_JSON` so items 7 and 8 keep working
unchanged — the existing env var becomes the seed, not a competing source.

### 3.2 Attainment comparison

One pure function, applied everywhere:

```python
def evaluate(actual: float | None, target: Target) -> Attainment:
    """Compare an actual against a target.

    actual=None yields status="no_data", never "missed". A metric with no data
    has not failed its target; reporting it as a miss is how a dashboard turns
    an instrumentation gap into a performance story.
    """
```

`Attainment` carries `status` (`met` / `missed` / `no_data` / `no_target`),
the actual, the target, and the variance. **Four states, not two** — because the
C1 slide has rows the system genuinely cannot fill (anything telephony, blocked
on R9), and those must render as "not measured", never as a red zero.

That distinction is the difference between a slide that says "we cannot measure
abandon rate because there is no call queue" and one that says PRO-NET's abandon
rate is 0%.

### 3.3 The control-item report

`GET /metrics/control-items?period=...` returning all fourteen rows, each with
its actual, target, attainment status, and month-vs-YTD columns.

Rows are **declared in one table**, mapping each control item to its source
view and target key, so the report's structure is data:

| # | Item | Source | Status today |
|---:|---|---|---|
| 1 | Total incoming contact | `v_volume_daily` | renders |
| 2 | Total per channel | `v_volume_by_month_channel` | renders; Social and HQ missing (see below) |
| 3 | QA performance (calls) | `v_quality` | renders, not call-specific — P8 |
| 4 | Response time (call) | — | `no_data`, blocked on R9 |
| 5 | AHT call | — | `no_data`, blocked on R9 |
| 6 | Response time (WhatsApp) | `v_first_response_by_channel` | renders (P1 makes it working-hours-correct) |
| 7 | Complaint resolution buckets | `v_resolution_sla_buckets` | renders |
| 8 | Case resolution by type | `v_resolution_sla_buckets` | renders |
| 9 | WIP cases | `v_case_aging` / P3's `case_state` | renders |
| 10 | Abandon rate | — | `no_data`, no queue exists to abandon from |
| 11 | RSA arrival `<60 min`, 95% | `rsa_incidents` | renders (this package) |
| 12 | RSA AHT call | — | `no_data`, blocked on R9 |
| 13 | Social response `<2WH`, 98% | — | `no_data`, no social inbox |
| 14 | Email response `<2WD`, 98% | `v_first_response_by_hours_split` (P1) | renders |

**Nine of fourteen render; five are honestly `no_data`.** Four of those five are
the telephony block (R9) and one is the social channel (Meta verification). The
report states the reason inline, per row, rather than leaving a blank the reader
has to interpret.

This is the deliverable's most important property: it makes the true state of
the reporting stack legible in one screen — which is precisely what the gap
analysis had to be written to establish.

**Item 2's channel list needs `Social` and `HQ`.** `channel_from_external_id`
emits WhatsApp/Email/Phone/Web/Other, so social traffic would land in `Other`
and HQ is not a channel at all. Social is added to the mapping now (harmless
before the inbox exists); **HQ is not**, because it depends on Q5 — the same
decision P3 makes, for the same reason.

### 3.4 RSA arrival attainment

`v_rsa_arrival_attainment`, computing per period:

- the median and p90 arrival duration
  (`time_arrived_breakdown_area` − `customer_called_in_time`),
- the percentage within 60 minutes,
- the count, and the count excluded for missing timestamps.

The last is not decoration. `rsa_incidents` is **manually entered by design**,
so partial rows are the expected state, and an attainment percentage computed
over the subset with complete timestamps must say what subset that was. This
reuses P3's `coverage.py`.

### 3.5 Per-division working-day leadtime (C2-07)

`v_resolution_leadtime_by_division`, expressing `resolution_working_minutes` in
**working days** against the 4-day target.

The conversion needs a definition — how many working minutes make a working day
— and it must come from the inbox's configured business hours, not a hardcoded
480. An inbox open 08:30–17:30 has a 540-minute working day; one open 09:00–17:00
has 480. Hardcoding either makes the leadtime wrong for the other, in a way that
looks plausible.

### 3.6 Report scheduling

Replace `report_interval_hours` with a schedule store:

```python
@dataclass(frozen=True)
class ReportSchedule:
    name: str
    cron: str                   # "0 9 1 * *" — 09:00 on the 1st
    period: str                 # "previous_month" | "previous_week" | "mtd" | "ytd"
    bundle: list[str]           # which reports
    formats: list[str]          # xlsx | pdf | csv
    recipients: list[str]
    enabled: bool
```

`period` is a **relative expression evaluated at fire time**, not a stored date
range. That is what makes "email the June monthly report on 1 July" work: the
schedule says `previous_month`, and firing on 1 July resolves it to June. A
stored absolute range would need editing every month.

The existing `report_interval_hours` path is kept as a default schedule so a
tenant that never opens the admin page behaves exactly as today.

### 3.7 Per-view XLSX and PDF

`render_xlsx` and `render_pdf` take `DashboardMetrics` specifically; `render_csv`
takes `Any`. Generalise the first two to the same row-list shape `render_csv`
already accepts, and add the period filter to the export routes.

Small, mechanical, and it closes §4.82 — which is currently PARTIAL for the
narrow reason that two of three formats only cover one of six reports.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| A metric with no data renders as a failed target | Four-state `Attainment`; `no_data` is never `missed` |
| The telephony rows render as zeros and imply performance | Each `no_data` row states its blocking reason inline |
| Targets drift from `RESOLUTION_SLA_TARGETS_JSON` | The env var seeds the store; it does not compete with it |
| Working-day conversion hardcodes 8 hours | Derived from the inbox's configured hours; asserted for two different inbox configs |
| RSA attainment computed over partial rows looks authoritative | Excluded-row count reported alongside |
| A schedule misfires and mails an empty report | Fire-time period resolution is unit-tested against a frozen clock for every relative expression |

## 5. Testing

- **Targets** (`test_targets_store.py`): CRUD; scope resolution
  (division-specific beats tenant-wide); seeding from the env var; RBAC.
- **Attainment** (`test_attainment.py`): all four states; `lte` and `gte`;
  `attainment_pct` rows; `None` actual → `no_data`.
- **Control items** (`test_control_items.py`): fourteen rows always; the nine
  renderable ones populated from fixtures; the five `no_data` ones carry a
  reason; month and YTD differ.
- **RSA** (`test_rsa_attainment.py`): percentage correct; incomplete rows
  excluded and counted.
- **Leadtime** (`test_leadtime_by_division.py`): two inbox configs produce
  different working-day figures for the same minute count.
- **Scheduling** (`test_report_schedules.py`): each relative period resolves
  correctly against a frozen clock; cron parsed; per-schedule recipients; the
  legacy interval path unchanged when no schedule exists.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `TARGETS_STORE_ENABLED` | `false` | Off = no target column, no admin page |
| `REPORT_SCHEDULES_ENABLED` | `false` | Off = today's fixed-interval path |
| `CONTROL_ITEMS_REPORT_ENABLED` | `false` | Off = endpoint 404s |

## 7. Requirements closed

4.78, 4.82, C1-11, C2-07, C1-12 #2, C1-12 #11 — and it supplies the target
column that eight further control-item rows need, including the two (#7, #8)
that are already MET on the metric side.

**Explicitly not closed:** C1-12 #4, #5, #10, #12 (telephony, R9) and #13
(social, Meta verification). The control-item report renders them as `no_data`
with a stated reason, which is the honest result and is more useful to the client
than their absence.
