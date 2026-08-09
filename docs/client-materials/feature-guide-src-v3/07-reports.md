# Reports

## Standard reports (Overview, Conversation, CSAT, Agent, Label, Inbox, Bot)

### What it is

Chatwoot's built-in reporting pages — Overview, Conversation, CSAT, Agent,
Label, Inbox, and Bot — give numbers on conversation volume, response and
resolution times, customer satisfaction, and per-agent, per-label, and
per-inbox performance. On top of a few of these native pages, Proton adds
its own extra section that pulls **cross-channel** numbers (every channel
combined, not just one inbox at a time) from the CRM's reporting warehouse:

- The **Agent** report gets a **Tasks per Agent (Proton)** table — cases,
  average first response, average resolution, and resolved count per
  agent/PIC.
- The **Bot** report gets a **Proton AI & Channel Analytics** section —
  bot-resolved percentage, volume by channel, bot-vs-agent resolution
  split, response speed, fallback and bounce rate, quality, first-response
  and resolution time by channel, and peak hours.
- The **CSAT** report gets a **Cross-Channel CSAT & NPS** section — blended
  CSAT and NPS scores, CSAT by channel, NPS respondent split, and NPS by
  agent.

Two of those tiles need reading precisely, because their labels promise more
than the underlying data can say:

- **The Bot report's "bot-resolved" percentage and "bot-vs-agent resolution
  split" do not measure AI versus human.** The field they are built on is
  derived from the case's status alone, so the two numbers under those labels
  are, in fact, *resolved* and *not yet resolved*. The labels are left as they
  are because existing dashboards read them, but they must not be quoted as the
  AI's share of the work. The AI performance reports that *do* attempt that
  question, and the basis on which they infer it, are described under AI Cost &
  Performance Measurement in the Administration chapter.
- **The NPS tiles are empty until NPS surveying is switched on**, which is off by
  default and is an administrator setting (see the same section). An empty NPS
  tile means the question has never been asked, not that customers gave no
  answer.

### Where to find it

**Reports** in the left sidebar. <!-- VERIFY-LIVE: confirm the exact set and order of native report tabs on the live tenant --> The Proton
sections above appear at the bottom of their respective native report
page — no extra navigation is needed to see them.

### How to use it

1. Open **Reports** from the sidebar.
2. Pick the report tab you need (Overview, Conversation, CSAT, Agent,
   Label, Inbox, or Bot).
3. Set the date range and any filters the page offers (agent, inbox,
   label).
4. On the Agent, Bot, or CSAT report, scroll to the bottom to see the
   added Proton section — it loads independently of the filters above it.
5. Use the native download/export option, where the page offers one, to
   save the numbers for offline reporting.

<!-- VERIFY-LIVE: confirm the exact set and order of native report tabs on the live tenant -->

[[SCREENSHOT: ch07-standard-reports | The standard reports overview page]]

### Example scenario

Pak Andi, the Proton e.MAS operations lead, opens the Agent report every
Monday morning to see which service advisors handled the most cases last
week. He scrolls past the native chart to the Tasks per Agent (Proton)
table, which stacks WhatsApp, email, and phone caseload for each PIC into
one comparison instead of him checking each channel separately.

### Integrations & automation

These pages read from Chatwoot conversation data and, for the Proton
sections, from the CRM's reporting warehouse. There is nothing to turn
on — the extra sections simply fail to load quietly (rather than break the
page) if that warehouse is briefly unreachable.

## Anomaly report

### What it is

A page that flags channels whose recent conversation volume looks
unusual — spiking or dropping well beyond that channel's own recent
baseline — so an operator can catch a possible outage, campaign
side-effect, or data problem early instead of noticing it days later in a
monthly total.

### Where to find it

**Reports → Anomaly** in the left sidebar.

### How to use it

1. Open **Reports → Anomaly**.
2. Check the **Flagged Channels** count at the top — this is how many
   channels currently show anomalous volume.
3. Read the table below it: each row is a channel with its current
   volume, its normal baseline (mean and standard deviation), and a
   deviation score.
4. Treat a deviation badge in the yellow or red range as worth
   investigating — it means the channel's current volume sits well
   outside its usual pattern.
5. If a channel is flagged, cross-check the relevant Inbox report or ask
   the on-duty agents whether something changed (a broken integration, a
   marketing blast, a public holiday).

[[SCREENSHOT: ch07-anomaly-report | The Anomaly report page]]

### Example scenario

On a Tuesday morning, Ibu Rina notices the WhatsApp channel flagged with a
sharply higher deviation score. She finds that a service reminder blast
went out overnight, driving an unusual spike in booking inquiries, and
asks the on-shift team to add temporary coverage rather than assuming
something is broken.

### Integrations & automation

The Anomaly report is read-only reporting; it doesn't trigger any
automated action on its own. Use it as an early-warning signal alongside
the standard Inbox and Overview reports.

## Departments & PIC report

### What it is

A page that breaks conversation volume and performance down by department
and by the person-in-charge (PIC) handling each case, plus how often
resolved cases get reopened, and how case categories line up against
vehicle models.

### Where to find it

**Reports → Departments & PIC** in the left sidebar.

### How to use it

1. Open **Reports → Departments & PIC**.
2. Review the **Department / PIC Performance** table — cases, average
   first response, average resolution, and resolution rate per
   department/PIC pair.
3. Check **Top Departments by Cases** for a quick visual of where volume
   concentrates.
4. Review **Reopen / Case Reopen Rate (CRR)** to see which
   dealer/department/PIC combinations have the most cases reopened after
   resolution — a high reopen rate can point to a quality problem, not
   just a busy team.
5. Use **Category × Vehicle Model** to see which complaint or inquiry
   categories cluster around specific vehicle models.

[[SCREENSHOT: ch07-departments-report | The Departments & PIC report page]]

### Example scenario

During the weekly ops review, Pak Hendra pulls up Departments & PIC and
sees the Service department's reopen rate at Dealer Bandung Timur is
noticeably higher than other dealers, prompting a follow-up with that
dealer's PIC about whether repairs are properly verified before a case is
closed.

### Integrations & automation

This report reflects case categories and dealer/department labels that
agents apply during normal conversation handling — there is no separate
configuration step; it fills in as agents work.

## Case Lifecycle report

### What it is

A page showing how long cases take to resolve and how cases move through
their statuses over time, plus a running list of recent cases and,
further down the same page, a work-in-progress / case-aging view of cases
still open.

### Where to find it

**Reports → Case Lifecycle** in the left sidebar.

### How to use it

1. Open **Reports → Case Lifecycle**.
2. Review **Resolution-Time Distribution** to see how cases bucket across
   resolution-speed bands, from under 30 minutes up to more than 24
   hours.
3. Review **Case State Trend** to see how the mix of case statuses has
   moved month over month.
4. Scroll to **Recent Cases** for a per-conversation table (channel,
   department, dealer, status, resolution time) for the most recently
   created cases.
5. Scroll further to **Work-in-Progress / Case Aging** for currently
   open or pending cases sorted by how many days old they are, bucketed
   so aging cases are easy to spot.

[[SCREENSHOT: ch07-case-lifecycle-report | The Case Lifecycle report page]]

### Example scenario

Ibu Sari, reviewing open RSA follow-ups, uses the Work-in-Progress / Case
Aging table to find any case older than a week that's still open, and
escalates those to the relevant PIC before they slip further.

### Integrations & automation

Case status and category come from how agents and administrators manage
conversations day to day (see Cases and RSA); this report has no separate
setup of its own.

## Weekly Report

### What it is

A single page that reconciles the numbers in Proton's own weekly
reporting routine — case volume, case status trend, department/PIC
performance, call-centre and SLA performance, dealer escalation
turnaround, SLA compliance, work-in-progress aging, and a per-case detail
table — all against a chosen 7-day window, so a weekly reporting habit
doesn't require collating several report pages by hand.

### Where to find it

**Reports → Weekly Report** in the left sidebar.

### How to use it

1. Open **Reports → Weekly Report**.
2. Use the week picker at the top to choose the window you're reporting
   on. It defaults to the current Monday–Sunday week, but its start date
   can be set to any day — useful if your own weekly routine runs on a
   different 7-day cycle (for example Friday-to-Thursday).
3. Read **Case Volume** for total cases in the window, the
   week-over-week change, and a breakdown by channel and by case
   type/division.
4. Read **Case Status Trend** for how cases split across statuses within
   the window.
5. Scroll through **Inquiry / Complaint / Feedback Detail — Departments &
   PIC**, **Call Centre & SLA Performance**, **Work-in-Progress / Case
   Aging**, and **Dealer Escalation Turnaround** — each section carries a
   small badge saying whether it's scoped to "This week" or "All time",
   so it's clear which numbers are windowed and which are running
   totals.
6. Check **Per-Case Detail** at the bottom for the individual
   conversations behind the week's numbers.

> The page notes that Per-Case Detail (read live from current
> conversations) and the Case Volume total above (read from the reporting
> warehouse) can legitimately show slightly different counts, since they
> come from two different data sources — this is expected, not a bug.

<!-- VERIFY-LIVE: confirm this reconciliation note's exact wording on the live tenant -->

[[SCREENSHOT: ch07-weekly-report | The Weekly Report page]]

### Example scenario

Every Friday, Ibu Dewi sets the Weekly Report's window to the
just-finished reporting period, notes the Case Volume and Departments &
PIC numbers for the client's weekly update, and checks the Dealer
Escalation Turnaround table before the call to flag any dealer whose
average turnaround has crept up.

### Integrations & automation

The Weekly Report reads from the same reporting warehouse and live case
data as the other report pages; there is no export button on the page
itself, so figures are typically copied by hand into a weekly deck, or
pulled in bulk via BI/reporting exports for a larger dataset (see the
Integrations chapter).

## SLA reports

### What it is

Chatwoot's native SLA report, showing which conversations met or missed
their assigned SLA policy, plus a Proton section beneath it that rolls
SLA achievement up across every channel and shows SLA compliance broken
down into time buckets. This is a different page from **SLA Policies**
under Administration, which is where SLA rules are configured rather than
reported on.

### Where to find it

**Reports → SLA** in the left sidebar. <!-- VERIFY-LIVE: confirm exact SLA reports UI wording on the live tenant -->

### How to use it

1. Open **Reports → SLA**.
2. Review the native SLA table for individual conversations that hit or
   missed their SLA.
3. Scroll down to **Cross-Channel SLA Achievement** for the overall
   met/missed percentage across every channel, plus a per-channel
   breakdown chart.
4. Continue to **SLA Compliance by Bucket** for a chart of how many cases
   fall into each SLA time bucket, split by case type.

<!-- VERIFY-LIVE: confirm exact SLA reports UI wording on the live tenant -->

[[SCREENSHOT: ch07-sla-reports | The SLA reports page]]

### Example scenario

Pak Yudi checks Cross-Channel SLA Achievement before a monthly client
review to confirm the team is holding above its committed percentage,
then drills into SLA Compliance by Bucket to see whether one particular
case type is dragging the average down.

### Integrations & automation

SLA achievement is measured against whichever SLA policy is applied to a
conversation (configured on the SLA Policies admin page, see
Administration); this report doesn't need separate setup of its own.

## Dealer escalation turnaround

### What it is

A measurement of how long a dealer takes to act on a case escalated to
it. The clock starts the first time a case is marked as escalated to a
specific dealer — not from when the case was originally created — and
stops when the case is resolved. This keeps time spent before escalation
from unfairly counting against the dealer.

### Where to find it

The **Dealer Escalation Turnaround** table appears on the SLA reports
page and again on the Weekly Report, both showing the same underlying
numbers.

### How to use it

1. Open the SLA reports page or the Weekly Report.
2. Find the Dealer Escalation Turnaround table: cases escalated, average
   turnaround, and P50/P90 turnaround in days, per dealer.
3. Check the **Slowest cases** sub-table for the specific conversations
   taking the longest to turn around — these are the ones worth
   following up individually.
4. Compare dealers to spot ones consistently slower than the group, and
   raise it with that dealer's PIC via Escalation Routing (see
   Administration).

[[SCREENSHOT: ch07-dealer-turnaround | A dealer escalation turnaround timestamp shown in a report]]

### Example scenario

An agent applies a dealer label to a customer's complaint about a delayed
repair, forwarding it to Dealer Kelapa Gading. The moment that label is
applied, the case's escalation clock starts; once the case is later
resolved, the elapsed time appears against Dealer Kelapa Gading in the
turnaround table, letting Ibu Sari track that dealer's responsiveness
over time.

### Integrations & automation

The turnaround clock starts automatically, once, the first time a dealer
label is applied to a conversation — an agent doesn't need to do anything
extra beyond applying the label as part of normal escalation handling
(see Labels, and Escalation labels & the escalation email in the AI
Behaviour chapter).
