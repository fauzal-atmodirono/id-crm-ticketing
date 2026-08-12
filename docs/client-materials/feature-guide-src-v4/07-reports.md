# Reports
<!-- TRAINING: audience=supervisor -->

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

There's a second thing to know about every Proton-built section on this
page, and about five of the standalone report pages later in this chapter
(Anomaly, Departments & PIC, Case Lifecycle, Weekly Report, and the
cross-channel sections on the SLA reports page, including Dealer Escalation
Turnaround): they all read from one shared cross-channel reporting
connection, separate from Chatwoot itself, and **that connection has not
yet been switched on for this account.** Until it is, those sections show a
fixed set of illustrative example numbers rather than Proton e.MAS's actual
conversations — and today nothing on the page marks them as illustrative;
the tables and charts render exactly as they will once the connection goes
live. Chatwoot's own native numbers are unaffected and always reflect this
account's real activity — conversation counts, native CSAT, the native SLA
table, and any table that reads straight from Chatwoot conversations (the
Weekly Report's Per-Case Detail, for instance). Treat any number inside a
section labelled "Proton" on these pages as a preview of that report's
layout, not as this account's real performance, until we confirm the
connection is live.

Two of those tiles need reading precisely, because their labels promise more
than the underlying data can say:

- **The Bot report's "bot-resolved" percentage and "bot-vs-agent resolution
  split" do not measure AI versus human.** The field they are built on is
  derived from the case's status alone, so the two numbers under those labels
  are, in fact, *resolved* and *not yet resolved*. The labels are left as they
  are because existing dashboards read them, but they must not be quoted as the
  AI's share of the work. No report on this platform currently answers the
  AI-versus-human question directly; ask us before quoting one that appears to.
- **The NPS tiles are empty**, and will stay empty. The end-of-conversation NPS
  survey is not running on this platform, so the question is never asked
  automatically — a score can only reach the tile if someone submits one
  through the API by hand. An empty NPS tile means the question was never
  asked, not that customers declined to answer.

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
week, reading it off the native chart — it's live and reflects this
account's real activity. He also glances at the Tasks per Agent (Proton)
table beneath it, which is built to stack WhatsApp, email, and phone
caseload for each PIC into one comparison, but its reporting warehouse
connection hasn't gone live for this account yet, so today it shows the
same illustrative example figures every time — he doesn't read a
conclusion off it until that connection is confirmed live.

### Integrations & automation

These pages read from Chatwoot conversation data and, for the Proton
sections, from the CRM's reporting warehouse — see the note above about
that connection's current status. Once it is live, a brief warehouse outage
fails the extra sections quietly (an empty section, not a broken page)
rather than surfacing an error.

## Anomaly report
<!-- TRAINING: audience=supervisor, exercise -->

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

The Anomaly report is built for a moment like this: a service reminder
blast goes out overnight, driving an unusual spike in booking inquiries,
and on Tuesday morning Ibu Rina would see the WhatsApp channel flagged
with a sharply higher deviation score, prompting her to ask the on-shift
team to add temporary coverage rather than assume something is broken.
Until the reporting warehouse connection goes live for this account,
though, what the page actually shows is the same illustrative example
figures every time, not this account's real channel volume — so treat
this as what the page will do, not a finding to act on yet.

### Integrations & automation

The Anomaly report is read-only reporting; it doesn't trigger any
automated action on its own. It reads from the same reporting warehouse
connection described under Standard reports — until that connection is
switched on for this account, the channels and figures shown here are
illustrative, not this account's real traffic. Once it's live, use the
page as an early-warning signal alongside the standard Inbox and Overview
reports.

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

This is the kind of pattern Departments & PIC is built to surface: the
Service department's reopen rate at one dealer creeping noticeably above
the others, prompting a follow-up with that dealer's PIC about whether
repairs are properly verified before a case is closed. Until the
reporting warehouse connection goes live for this account, the numbers on
the page today are the same illustrative example figures every time, not
Proton e.MAS's actual reopen rates — so treat this as a preview of what
the report will flag, not a finding to follow up on yet.

### Integrations & automation

In principle this report reflects case categories and dealer/department
labels that agents apply during normal conversation handling, with no
separate configuration step. Today, though, it reads from the same
not-yet-connected reporting warehouse described under Standard
reports — so what's on screen is illustrative rather than a live
reflection of how cases are actually being handled, until that connection
is switched on.

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

This is what the Work-in-Progress / Case Aging table is built for:
finding any case older than a week that's still open and escalating it to
the relevant PIC before it slips further. Until the reporting warehouse
connection goes live for this account, though, the table shows the same
illustrative example cases every time rather than this account's actual
open work, so for now Ibu Sari can't yet use it for that — it's a preview
of the workflow, not today's real aging list.

### Integrations & automation

Case status and category come from how agents and administrators manage
conversations day to day (see Cases and RSA), with no separate setup of
its own — but this report also reads from the same not-yet-connected
reporting warehouse described under Standard reports, so its numbers are
illustrative rather than live until that connection is switched on.

## Weekly Report
<!-- TRAINING: audience=supervisor, exercise -->

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

Right now those two sources aren't just "different" — one of them isn't
live yet. Per-Case Detail reads straight from Chatwoot and is always real.
Case Volume, Case Status Trend, Departments & PIC Detail, Call Centre & SLA
Performance, Work-in-Progress / Case Aging, and Dealer Escalation
Turnaround all read from the reporting warehouse connection described
under Standard reports, which has not yet been switched on for this
account — so those sections currently show illustrative figures rather
than this account's real week. Per-Case Detail is the one section on this
page you can already read as real today.

<!-- VERIFY-LIVE: confirm this reconciliation note's exact wording on the live tenant -->

[[SCREENSHOT: ch07-weekly-report | The Weekly Report page]]

### Example scenario

Every Friday, Ibu Dewi sets the Weekly Report's window to the
just-finished reporting period and checks **Per-Case Detail** at the
bottom — the one section on this page read live from Chatwoot's current
conversations — for the actual cases behind the week. Case Volume,
Departments & PIC, and Dealer Escalation Turnaround are still on the
reporting warehouse connection that hasn't gone live for this account, so
she doesn't read a conclusion off them for the client update — no "this
dealer's turnaround has crept up," no case-volume trend — until that
connection is confirmed live, relying on Per-Case Detail or a bulk export
of the real data instead.

### Integrations & automation

The Weekly Report reads from the same reporting warehouse and live case
data as the other report pages — see the note above about the warehouse
connection's current status. There is no export button on the page
itself, so figures are typically copied by hand into a weekly deck, or
pulled in bulk via BI/reporting exports for a larger dataset (see the
Integrations chapter).

## SLA reports
<!-- TRAINING: audience=supervisor, exercise -->

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

Pak Yudi checks the native SLA table before a monthly client review — it's
live and measures achievement directly against each conversation's
assigned SLA policy — to confirm the team is holding above its committed
percentage. Cross-Channel SLA Achievement and SLA Compliance by Bucket
would normally be the fastest way to see this across every channel and by
case type, but their reporting warehouse connection hasn't gone live for
this account yet, so he treats what's on screen there as a preview of the
layout, not this month's real compliance, until that connection is
confirmed live.

### Integrations & automation

The native table at the top measures SLA achievement directly against
whichever SLA policy is applied to a conversation (configured on the SLA
Policies admin page, see Administration) — no separate setup, and it's
live today. Cross-Channel SLA Achievement and SLA Compliance by Bucket
below it read from the same not-yet-connected reporting warehouse
described under Standard reports, so treat their numbers as illustrative
until that connection is switched on.

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
applied, the case's escalation clock starts — that per-case timestamp is
written straight onto the conversation and is real today. Once the case
is later resolved, that elapsed time is what the turnaround table's
averages and percentiles are built from — but the table itself still
reads from the reporting warehouse connection that hasn't gone live for
this account, so Ibu Sari can't yet use it to track Dealer Kelapa Gading's
responsiveness over time. Once that connection is live, this is exactly
the view she'll use for that.

### Integrations & automation

The turnaround clock starts automatically, once, the first time a dealer
label is applied to a conversation — an agent doesn't need to do anything
extra beyond applying the label as part of normal escalation handling
(see Labels, and Escalation labels & the escalation email in the AI
Behaviour chapter). That timestamp is written straight onto the
conversation and is always real. The turnaround table itself — averages,
percentiles, and the Slowest Cases list — is one of the sections fed by
the reporting warehouse connection described under Standard reports, so
until that connection is switched on for this account, what's on screen
there is illustrative rather than computed from these real timestamps.
