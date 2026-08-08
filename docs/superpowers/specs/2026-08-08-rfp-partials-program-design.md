# RFP 2026_028 — PARTIAL-Closure Programme: Master Design

**Date:** 2026-08-08
**Branch:** `dev-yuda`
**Input:** `docs/analysis/2026-08-08-rfp-2026_028-gap-analysis.md` (commit `d85f0d4`)
**Scope:** the **119 requirements classified PARTIAL**. GAP, MET and UNCLEAR
items are out of scope except where a PARTIAL cannot be closed without dragging
an adjacent GAP with it — those are named explicitly in §6.
**Status:** design. No code written. Each package below has its own spec and its
own implementation plan; this document is the decomposition, the sequencing and
the traceability register that ties them together.

---

## 1. Why this programme exists, and why it is not one plan

The gap analysis found that the PARTIAL column — not the GAP column — is where
this bid is actually decided. 49% of the requirement set is *built and stops
just short*. That failure mode is more dangerous than an honest absence: a
feature that is 70% there demos well, survives a proposal review, and then
contradicts itself in front of the client during reconciliation.

Three examples from the analysis, all of them PARTIAL rather than GAP:

- SLA **reporting** is working-hours-aware and SLA **enforcement** is not. Both
  halves exist. They will disagree with each other, in writing, in front of
  PRO-NET.
- `first_response_working_minutes` is computed on every case and stored in
  BigQuery, and **no view reads it**. The expensive part is done; the cheap part
  is missing.
- The in-hours/out-of-hours flag is computed at intake and thrown away, so the
  after-hours volume report that §4.52 asks for cannot be built even though the
  system knows the answer every single time it decides an auto-close grace.

The instinct is to write one plan for "the partials". That is wrong, for a
reason worth stating once: **the 119 items are not 119 pieces of work.** They
are roughly fourteen pieces of work that the RFP's own section structure
scattered across ten documents. §3.1.3, §4.52, §4.53, §4.54, B-WA-14, B-SM-06,
B-EM-05 and two rows of the C1 control-item slide are all *the same two-week
job*. Planning them separately would build the same thing four times and still
leave the seams showing.

So the decomposition below is **by shared mechanism, not by RFP section**, and
the traceability register in §5 is what maps it back to the section numbering
PRO-NET will audit against.

## 2. The fourteen packages

Named `P1`–`P14` to avoid colliding with the existing `pkg-a`…`pkg-g` series
(2026-08-04), which these do not supersede.

| # | Package | Closes | Effort | Wave | Blocked by |
|---|---|---:|---|---|---|
| **P1** | Working-hours SLA enforcement & after-hours instrumentation | 15 | 1–2 wk | 1 | — |
| **P2** | Omnichannel escalation delivery | 7 | 2 wk | 1 | customer-ack wording on chat channels |
| **P3** | Case record extensions & case-state warehouse sync | 9 | 2 wk | 1 | — |
| **P4** | Reporting query layer: period, timezone, filters | 13 | 2–3 wk | 2 | P3 |
| **P5** | Targets store, control-item slide & report scheduling | 6 | 1.5 wk | 2 | P4 |
| **P6** | Agent presence, custom statuses & workforce dashboard | 8 | 3 wk | 2 | — |
| **P7** | AI conversational quality | 13 | 3–4 wk | 3 | — |
| **P8** | AI & agent measurement | 6 | 3 wk | 3 | P4, P7 |
| **P9** | Notification & alerting UX | 5 | 2 wk | 3 | — |
| **P10** | Self-service taxonomy admin, category→department, data-scoped RBAC | 5 | 2 wk | 2 | — |
| **P11** | Voice partials not blocked by the call queue | 12 | 3 wk | 3 | Studio-vs-bridge decision |
| **P12** | Screen-pop Customer 360 | 3 | 2 wk | 3 | DMS spec for the data, not for the pop |
| **P13** | Ops hardening: restore, monitoring, retention, security policy | 7 | 3 wk | 2 | — |
| **P14** | Handover, enablement & governance artefacts | 4 | 2 wk | 4 | the rest, by definition |
| — | Not developable now — see §6 | 6 | — | — | client answer or commercial decision |
| | **Total** | **119** | | | |

Effort is engineering-weeks for one competent developer, excluding review and
deployment. Waves are dependency order, not calendar: wave 1 is three parallel
tracks that share no files.

### 2.1 What each package is, in one paragraph

**P1 — Working-hours SLA enforcement & after-hours instrumentation.** Persist a
`received_in_business_hours` flag at intake instead of computing and discarding
it; move SLA *enforcement* onto the existing `working_minutes_between` helper so
alerts and reports use one clock; capture a real acknowledgement event rather
than inferring it from the first agent reply; add the after-hours volume and
business/non-business response-time views that the persisted flag unlocks. This
is the highest-leverage package in the programme: it is small, it is unblocked,
it uses a helper that already exists, and it removes a self-contradiction that
would otherwise surface in a client meeting.

**P2 — Omnichannel escalation delivery.** The EM-7 two-thread escalation is the
most complete thing in the build and it is gated to Email-channel conversations
by a single `channel_type` check. Appendix B makes escalation the terminal step
of the WhatsApp and Social flows too, and WhatsApp is 73% of weekly volume. Lift
the gate, source the customer acknowledgement from the channel the customer
actually used, add CC to the dealer forward (CC already exists on the PIC leg),
populate the attachment list that three call sites pass empty, check who is on
duty before notifying them, and record delivery in the audit trail.

**P3 — Case record extensions & case-state warehouse sync.** Five fields the
client's own report decks print per row do not exist in the data model: Level-2
`case_detail` (in Chatwoot, absent from BigQuery), car plate, chassis/VIN,
purchased-from dealer, and delay reason. Separately, `CaseState.WIP` and
`TEMP_CLOSED` are tracked in the backend and never reach the warehouse, because
the sync populates `status` from the *Chatwoot* status. Both are the same job:
extend the case record, extend the schema, extend the mapping, extend the views.

**P4 — Reporting query layer.** Five of eight metrics endpoints return HTTP 400
for any date range, because their underlying views have no date column — so a
Weekly Report page renders "All time" figures under a week header. Add the date
columns, make the reporting timezone configurable (today every bucket is a UTC
calendar day for a UTC+8 tenant), add the agent/team/department/channel filters
§4.81 asks for, add the per-dealer first-response view, and define and emit the
reopen event that `v_reopen_rate` currently reads from a column nothing writes.

**P5 — Targets store, control-item slide & report scheduling.** There is no
target store anywhere in the codebase, so the fourteen-row summary slide PRO-NET
reads first cannot be rendered even for the metrics that do exist. Add an
operator-editable targets table, an attainment comparison, the control-item
report itself, and replace the fixed-interval all-time report email with
period-scoped scheduled bundles ("email the June monthly report on 1 July").

**P6 — Agent presence, custom statuses & workforce dashboard.** Chatwoot gives
three native statuses and a point-in-time read. The RFP asks for eight named
statuses, how long an agent has been in each, notifications at 10 minutes and 1
hour, a live workforce dashboard, and After-Call-Work. All of it needs one thing
that does not exist: a presence-*event* store. Build that, and the six
requirements sitting on it fall out, along with polled reassignment and the
team-leader manual-reassign endpoint.

**P7 — AI conversational quality.** Six independent AI shortfalls that share a
prompt/retrieval surface: a sentiment field that is defined, surfaced on the API
and never written; no translation feature at all (only language mirroring); FAQ
matching that is semantic-only with an authored `keywords` field that ranking
ignores; a complete multimodal pipeline with no prompt telling the model to
diagnose anything; no index of previously resolved cases; and a summariser that
must be triggered by hand instead of firing on resolve.

**P8 — AI & agent measurement.** Prove the AI works. Capture output tokens (only
prompt tokens are recorded, and the backend — which makes most of the calls —
records none), add a price table and a cost report, wire NPS into the survey
flow it was built beside but never connected to, aggregate CSAT per agent rather
than per channel, and build the AI performance reports that the raw material
already supports.

**P9 — Notification & alerting UX.** The alerting primitives all exist —
audible, desktop and in-page — in a dashboard-app iframe, firing on SLA
warn/breach only. Move them into the main Chatwoot UI and fire them on new
inbound, which is what §4.2 and §3.1.7 actually ask for. Add intra-day anomaly
grain so the anomaly dashboard can detect the intra-day channel explosion its
own requirement uses as the example.

**P10 — Self-service taxonomy admin, category→department, data-scoped RBAC.**
Changing a case category today means editing two env JSON blobs, editing a
Chatwoot attribute definition separately, and restarting the agent service.
Appendix A says the list "will be expanded from time to time". Build the admin
screen; while in there, wire case category to escalation department (they are
currently separate taxonomies, which is an operational trap), and add the
data-level scoping §4.83 asks for on top of the function-level RBAC that exists.

**P11 — Voice partials not blocked by the call queue.** The telephony metrics
need a queue that does not exist (R9, 4–6 weeks, out of scope here). But seven
voice PARTIALs do *not* need the queue: recording retrieval and playback (the
recordings are already being made and the IDs stored), voicemail ingestion into
a case, an after-hours message on the AI bridge, an RSA after-hours bypass, live
transcript surfacing, real handoff numbers instead of `+60300000001`, and the
end-of-call rating transfer.

**P12 — Screen-pop Customer 360.** Customer 360 is a search page with one text
box. The requirement is a card that pops automatically when a call or chat
arrives. The pop is buildable now; the vehicle and service sections stay
demo-flagged until a real DMS spec exists, and the design makes that degradation
explicit rather than hiding it.

**P13 — Ops hardening.** Backups exist and are kept on the VM they protect, with
no restore script and no drill. There is no monitoring or alerting anywhere in
`deploy/`. "Retain 7 years" appears only in proposal prose. This package does not
attempt 99.9% HA — that is R17, a commercial decision — but it makes the
current architecture honestly operable and closes the security-policy partials.

**P14 — Handover, enablement & governance artefacts.** The non-code partials.
Strong raw material exists (13 feature-guide chapters, design specs, deploy
runbooks, extensive test suites); what is missing is the named form the RFP asks
for: a role-differentiated curriculum, a consolidated architecture and API pack,
a SIT/QA report against an agreed script, a QA and risk plan, and the ten
milestone sign-off documents.

## 3. Sequencing

```
Wave 1  (parallel, no shared files)     P1 ──┐
                                        P2   │
                                        P3 ──┤
                                             │
Wave 2  P4 (needs P3's schema) ──────────────┤
        P5 (needs P4's period plumbing) ─────┤
        P6 (independent)                     │
        P10 (independent)                    │
        P13 (independent)                    │
                                             │
Wave 3  P7 (independent)                     │
        P8 (needs P4 views + P7 sentiment)   │
        P9 (independent)                     │
        P11 (needs the Studio decision)      │
        P12 (independent of DMS for the pop) │
                                             │
Wave 4  P14 (documents what waves 1-3 built) ┘
```

Two sequencing rules that matter more than the diagram:

1. **P3 before P4.** P4 adds date columns and filters to views; P3 adds columns
   to the table those views read. Doing P4 first means touching every view
   twice.
2. **P1 before P5.** The control-item slide has fourteen rows and four of them
   are working-hours attainment figures. Rendering them against a target store
   while enforcement still runs on a wall clock would ship the contradiction
   into the client's headline slide.

If only one wave can be funded, **wave 1 is the one that changes what can be
honestly claimed in a clarification meeting.**

## 4. Design constraints binding on every package

These are repeated in each package plan's Global Constraints, and are stated
once here as the programme's engineering contract.

- **Ship behind a flag, default off.** Every package adds at least one setting
  in `agent/app/config.py` or `backend/.../platform/config.py`, and with the flag
  off the system must behave **byte-identically to today**. This is the existing
  repo convention (`LIFECYCLE_*`, `EMAIL_ESCALATION_ENABLED`, `ROUTING_ENABLED`)
  and it is what makes a partially-delivered programme safe to deploy.
- **Env vars go in three places or they do not exist:** `app/config.py` (or the
  backend's `platform/config.py`), `deploy/tenants/example.env`, and
  `tests/conftest.py` where import-time presence is required. CLAUDE.md is
  explicit; every plan restates it.
- **Fail open.** Background tasks and webhook handlers never raise for expected
  "nothing to do" cases. A monitoring feature that takes down message delivery is
  worse than the gap it closed.
- **One calendar.** `features/metrics/business_hours.py::working_minutes_between`
  is the only working-hours duration implementation. No package adds a second.
  (`agent/app/services/business_hours.py::is_within_business_hours` is a
  point-in-time boolean and stays; the two are deliberately distinct.)
- **Idempotency is mandatory** wherever a package sends something outward. A
  duplicate escalation email to a Dealer Owner is worse than a late one.
- **TDD.** Every plan is written test-first, matching the repo's existing plans:
  each task names its test file and its test function names before its
  implementation.
- **Branch `dev-yuda`. Never merge to `main`.**

## 5. Traceability register — all 119 PARTIALs

Every PARTIAL requirement in the gap analysis, mapped to the package that closes
it. This is the table to hand a PRO-NET reviewer who asks "what happens to
requirement 4.53".

### P1 — Working-hours SLA & after-hours (15)

| Req | What P1 does for it |
|---|---|
| 3.1.3 After-hours case monitoring | Persists the flag instead of discarding it, so it can drive SLA and reporting |
| 3.1.4 After-hours auto-response, all channels | Provisions Appendix B's exact wording per inbox; the voice half is P11 |
| 3.2.4 Automated SLA escalation 2h/8h/48h | Timers move to working hours; a real acknowledgement event replaces the inference |
| 4.34 Configurable 2h/8h auto-escalation | Same mechanism |
| 4.53 Response time by business vs non-business hours | Views that finally read `first_response_working_minutes` |
| 4.54 SLA calculation considering operating hours | Closes the enforcement half; reporting half already MET |
| B-WA-03 Office-hours branch | Makes it a real flow branch, not just an auto-close grace input |
| B-WA-04 After-hours auto-reply, exact text | Provisioned and asserted against Appendix B wording |
| B-WA-10 Attend next business hour | Next-business-hour scheduling, which does not exist today |
| B-WA-14 Agent acknowledges within 2 minutes | Working-hours clock + captured ack event |
| B-EM-04 Attend next business hour | Same as B-WA-10 |
| B-EM-05 Update customer within 4 working hours | Working-hours clock |
| B-SM-06 Acknowledge within 2 working hours | Working-hours clock (channel itself is blocked on Meta) |
| C1-12 #6 WhatsApp response time <4 min | Working-hours-correct figure; target column is P5 |
| C1-12 #14 Email response <2WD, 98% | The view that reads the stored working-minutes column |

### P2 — Omnichannel escalation delivery (7)

| Req | What P2 does for it |
|---|---|
| 2.1.3 Workflow engine (risk scoring) | Adds the risk score that feeds escalation priority |
| 3.2.2 Rules-based SOP routing | CC on the dealer forward; attachments populated |
| 3.2.6 System audit trail | Adds recipient, delivery record, acknowledgement, `sla_status` |
| 4.11 Check who is on duty before escalating | Presence filter applied to PIC resolution |
| 4.29 Rule-based routing to PIC with attachments | Attachment list populated at all three call sites |
| 4.32 Auto notify **and CC** relevant personnel | CC extended to the dealer forward leg |
| 4.36 Reminder of the higher-level responsible person | Distinct tier-2 recipient, not a re-alert of the same group |

### P3 — Case record extensions & warehouse sync (9)

| Req | What P3 does for it |
|---|---|
| 4.37 Records of WIP and Resolved operations | Syncs `case_state` so WIP/TEMP_CLOSED reach BigQuery |
| 4.62 WIP weekly case | Backs the WIP figure with the real state, not an open+pending proxy |
| C1-04 Inquiry detail by category/subcategory × model | Adds `case_detail` to the warehouse |
| C1-05 Complaint detail | Same |
| C1-06 Compliment/Feedback detail | Same |
| C1-10 WIP case tables | Adds car plate, purchased-from, delay reason |
| C2-03 Division/Concern × vehicle model pivot | Level-1 → Level-2 nesting becomes expressible |
| C2-06 Complaint division/concern × model pivot | Same |
| C1-12 #9 Number of WIP cases | Real WIP count |

### P4 — Reporting query layer (13)

| Req | What P4 does for it |
|---|---|
| 2.2.3 Executive dashboard (real-time) | Reduces staleness and states the freshness contract honestly |
| 4.48 Built-in reports or Power BI | Underpins both |
| 4.51 Daily/weekly/monthly trend chart | Period params stop 400ing on five endpoints |
| 4.55 Power BI integration | A real `.pbix` + dataset + refresh config, not a manual runbook |
| 4.59 Ranking of 2-hr first response rate | Adds the attainment-rate metric and the per-dealer FRT view |
| 4.60 Complaint reopen rate | Defines and emits the reopen event the view reads |
| 4.74 Lifecycle map, anomaly, tag-keyword, dispatch | Adds the missing tag-keyword report |
| 4.81 Real-time + historical, filterable | Adds agent/team/department/channel query params |
| C1-08 Complaint trend by dealer | Date column so it is no longer all-time |
| C1-09 Dealer turnaround + slowest resolutions | Same |
| C2-01 Inquiries by channel, 4-week bars | Every section on the page honours the week |
| C2-02 Inquiries by case division, 4-week trend | Same |
| C2-05 Complaint weekly highlights | Cumulative per-division running total |

### P5 — Targets, control-item slide & report delivery (6)

| Req | What P5 does for it |
|---|---|
| 4.78 Export to PDF/Excel, auto-sent regularly | Cron-style schedule + period-scoped bundles |
| 4.82 Export to CSV, XLS and PDF | Extends XLSX/PDF beyond the dashboard bundle to the per-view reports |
| C1-11 RSA call performance and incident tables | Computes the arrival-time attainment percentage |
| C2-07 Resolution leadtime by division vs 4-working-day target | Per-division working-day view + the target |
| C1-12 #2 Total per channel | Adds Social and HQ as channels (HQ pending Q5) |
| C1-12 #11 RSA arrival <60 min, 95% | Attainment percentage against a stored target |

### P6 — Agent presence & workforce (8)

| Req | What P6 does for it |
|---|---|
| 3.1.6 Duty & priority polling | Adds the polling half; today it is event-driven at handoff only |
| 4.10 On-duty check, channel priority, polling | Same |
| 4.16 Automatic polling task assignment | A scheduler that re-sweeps the queue, plus fair-share rotation |
| 4.17 Intelligent switch; 8 named statuses | The six missing statuses get a store, an enum and a UI |
| 4.18 Follow-up reminder date | An operator-settable per-ticket follow-up date field |
| B-WA-15 Team leader manual reassignment | An endpoint that accepts a chosen agent id |
| B-SM-07 Team leader reassignment | Same |
| B-EM-06 Team leader reassignment | Same |

### P7 — AI conversational quality (13)

| Req | What P7 does for it |
|---|---|
| 2.2.4 AI STT/response/FAQ-match calibration | Defines the methodology and acceptance thresholds |
| 3.2.1 Real-time AI FAQ matching | Keyword-aware ranking; 1-click apply via the composer path |
| 4.3 AI translation EN/BM/Chinese/Tamil | An actual translate action; Tamil validated or renegotiated |
| 4.4 NLU robust to spelling/abbreviation/colloquial | Normaliser, synonym dictionary, Malay SMS-style test corpus |
| 4.19 Real-time FAQ + keyword pop-up + 1-click | Same as 3.2.1 |
| 4.20 AI analyses images and videos | A prompt that actually asks for diagnosis and follow-ups |
| 4.22 Auto-match FAQ by keywords, pop-up | Keyword + semantic hybrid |
| 4.23 Suggestions from **previous resolved cases** | A resolved-case index |
| 4.24 Sentiment analysis, 4 levels + tone | A classifier that writes the field the API already exposes |
| 4.27 Auto-summarise at end of conversation | Fires on resolve instead of on demand |
| 4.28 One-click FAQ reference | Moves suggestions to the fork surface that can write the composer |
| 8.1.8 AI calibration & training | The methodology and baseline |
| B-WA-02 Bot replies in the customer's language | Tamil closes the last gap |

### P8 — AI & agent measurement (6)

| Req | What P8 does for it |
|---|---|
| 4.28.2 AI cost/pricing model | Output tokens, backend token capture, price table, cost report |
| 4.71 NPS for agent | Wires `record_nps` into the survey flow |
| 4.72 Customer rating of agent performance | CSAT aggregated per agent |
| B-WA-16 Rating survey for the live agent | Same |
| C1-12 #3 QA performance (calls), 85% | Call-specific QA scoring |
| 8.1.15 Monthly review incl. AI accuracy, KB health | The two metrics that do not exist today |

### P9 — Notification & alerting UX (5)

| Req | What P9 does for it |
|---|---|
| 3.1.1 Omni-channel single view + pop-up alerts | A real, configured, verified new-inbound alert |
| 3.1.7 Alert system (desktop/in-system/audible) | Moves the working primitives into the main UI |
| 4.1 All channels on one interface | Same |
| 4.2 Pop-up notification on new inbound | The requirement this package exists for |
| 4.79 Configurable anomaly dashboard, real-time | Hourly grain + push, so intra-day spikes are detectable |

### P10 — Admin & access control (5)

| Req | What P10 does for it |
|---|---|
| 2.2.1 Categorization & rules setup | A single admin screen; no env edit, no restart |
| 4.30 Rule engine keyed on classification | Wires case category → escalation department |
| 4.83 Permissions by function **and data** | Per-inbox/team/dealer data scoping |
| 8.1.10 Case categorisation maintenance | Same screen as 2.2.1 |
| A-6 List will be expanded from time to time | Same screen |

### P11 — Voice, excluding the queue (12)

| Req | What P11 does for it |
|---|---|
| 3.1.2 Voice recognition, real-time STT | Surfaces the live transcript on an agent screen |
| 4.5.1 Voice→text on agent screen | Same |
| 4.5.2 Route call to live agent or RSA | Replaces placeholder numbers with real routing |
| 4.7 Retrieve and playback call recordings | The retrieval endpoint and player that the permission already gates |
| 4.9 End-of-call transfer to a rating system | Wires the phone path into the survey flow |
| 4.26 AI call handling in multiple languages | BM reliability work and a real-call verification |
| B-IVR-01 Inbound on 1300-888-877 | Number provisioning captured in code, not by hand |
| B-IVR-03 After-hours greeting + voicemail | Voicemail ingested into a case — today it reaches nobody |
| B-IVR-04 Female AI voice | Verified on a real call |
| B-IVR-06 DTMF menu | Resolves the Studio-vs-bridge split |
| B-IVR-07 RSA vs non-RSA routing | Real numbers, plus the RSA after-hours bypass |
| B-IVR-08 Queue-busy prompt | Honest messaging until R9 delivers a real queue |

### P12 — Screen-pop Customer 360 (3)

| Req | What P12 does for it |
|---|---|
| 4.31 Identify PIC from caller information | Derives what it can without a DMS, and says what it cannot |
| 4.43 Call DMS/TSP for customer/vehicle/service history | The pop and the sections; data stays demo-flagged |
| 4.46 Card divided into four sections | Conversation-scoped rendering |

### P13 — Ops hardening (7)

| Req | What P13 does for it |
|---|---|
| 2.2.7 UAT environment support | A defined non-production environment |
| 2.4.4 System updates | Fork-rebase automation for the 50-patch series |
| 2.4.5 Backup & DR | Restore script, offsite copy, RTO/RPO, drill |
| 7.3 Login control / IAM | Verifies MFA and SSO against a running instance, then closes or reports |
| 7.6 Password policy | 90-day expiry policy |
| 8.1.12 Backup & DR strategy detail | Same as 2.4.5 |
| 8.1.13 Software updates & security patches | Same as 2.4.4 |

### P14 — Handover & governance (4)

| Req | What P14 does for it |
|---|---|
| 2.3.3 Product & role-based training | Role-differentiated curriculum + delivery plan |
| 2.3.4 Documentation handover | Architecture map, configuration doc, API schema chart |
| 6.1 QA & risk management | The formal plan the test suites can back |
| 6.3.2 Milestone sign-off artefacts | The ten named documents |

## 6. The six PARTIALs this programme does not close, and why

Honesty here is the point of the section. Each of these is PARTIAL, each is
listed in the traceability register above as unassigned, and none of them is an
engineering task we can start.

| Req | Why not | What unblocks it |
|---|---|---|
| **2.1.1** Software provisioning | The missing artefact is a licence/entitlement inventory and procurement for three consumption-billed third parties (Twilio, Vertex, BigQuery). Commercial, not technical. | A commercial owner |
| **2.1.2** Omnichannel intake, "no limit to attachment size and file format" | **Not achievable as written.** Twilio caps WhatsApp media at 16 MB; the repo budgets video at 14 MiB and KB ingest at 10 MiB with four allowed extensions. | Renegotiate to a stated ceiling |
| **4.63** Call-centre KPIs + agent NPS | The agent-NPS half is P8. The telephony half needs R9 — a real call queue — which is 4–6 weeks and out of this programme's scope. | R9 |
| **7.1** License types (concurrent/floating/named) | Concurrent and floating licensing cannot be enforced by a self-hosted OSS deployment. This is question Q10, a commercial-model decision. | PRO-NET's answer to Q10 |
| **B-EM-01** Inbound to `e.mascentre@pronet.my` | Tenant provisioning and a Proton-branded mailbox. The one live test used a Gmail relay. Ops task pending a real mailbox. | PRO-NET mailbox provisioning |
| **B-SM-05** Priority routing with Social first | `"social"` is already a first-class routing channel. There is nothing to route because the channel cannot be connected — blocked on Meta Business verification. | Meta verification (R12) |

Two adjacent GAPs are pulled in deliberately, because their PARTIAL siblings
cannot be closed without them: **B-WA-17 / B-SM-09** (escalation on WhatsApp and
Social) are GAP but are the terminal step of flows whose other steps are P1/P2
PARTIALs, so P2 closes them; and **4.52** (after-hours volume report) is GAP only
because 3.1.3 never persists the flag, so P1 closes it as a side effect. No other
GAP is in scope.

## 7. Open questions that change this design

These are the subset of the gap analysis's twelve client questions whose answers
would change a package's *design*, not merely its estimate. Each package spec
restates the ones binding on it and states the assumption it proceeds under.

| Q | Question | Package affected | Assumption if unanswered |
|---|---|---|---|
| Q3 | Who writes the WIP "Issue / Action Taken / Next action" remarks, and where? | P3 | Three free-text custom attributes on the case, operator-entered, no AI generation |
| Q5 | What distinguishes "escalated to HQ" from "escalated to a dealer"? | P3, P5 | Modelled as a third escalation target alongside dealer and PIC; column added, left unpopulated until answered |
| Q6 | Is a bounce mailbox available for delivery-failure alerts? | P2 | SMTP send-failure alerting only; bounce/DSN handling deferred |
| — | Twilio Studio DTMF flow vs the AI voice bridge — which ships? | P11 | The AI bridge is the deployed path; P11 brings the bridge up to Appendix B rather than reviving Studio |
| Q7 | Which regulation drives regulatory compliance verification? | out of scope (R16) | — |
| Q4 | Is there a DMS/TSP API spec and sandbox? | P12 | The pop ships; vehicle and service sections render a documented "unavailable" state |

**These assumptions are chosen so that a wrong answer costs a small rework, not a
rebuild.** Q5 in particular: adding an unpopulated column is cheap; designing an
HQ escalation workflow on a guess is not.

## 8. What this programme does not attempt

Stated so nobody reads a 119-requirement closure plan as a bid-completion plan.

- **R9, the call queue** (4–6 wk) — no `<Enqueue>`, no TaskRouter, so abandon
  rate has nothing to measure. Six of the fourteen monthly control items and all
  of C1 §2 depend on it. It is the single largest missing capability and belongs
  in its own programme.
- **R11, real DMS/TSP adapters** (6–10 wk) — cannot be estimated without an API
  spec.
- **R16, PII masking, tamper-evident audit and 7-year retention** (4–6 wk) —
  P13 does the retention policy; masking and WORM need Q7 answered first.
- **R17, multi-zone HA** (4–6 wk plus run cost) — 99.9% uptime and P1 `<2h` are
  not supportable on a single GCE VM running Docker Compose, and no amount of
  application work changes that. Commercial decision.
- **R19, the licensing subsystem** (§7) — a commercial-model question first.
- **The §4 compliance-matrix reconciliation.** The gap analysis found at least
  17 requirements marked "Fully Out-of-the-Box" in the already-drafted vendor
  response that are not built today. That is a document problem, not a build
  problem, and it needs resolving before this programme's output is quoted
  against it. **It is the most urgent item in the whole analysis and it is not an
  engineering task.**

## 9. Deliverables of this design

| Document | Path |
|---|---|
| This master design | `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md` |
| P1…P14 designs | `docs/superpowers/specs/2026-08-08-rfp-p<N>-<slug>-design.md` |
| P1…P14 plans | `docs/superpowers/plans/2026-08-08-rfp-p<N>-<slug>.md` |

Wave-1 and wave-2 packages carry full task-by-task TDD plans. Wave-3 and wave-4
packages carry plans at task-and-interface granularity, because their designs
depend on decisions (the Studio question, the DMS spec, Q5) that would make
finer detail speculative. Each such plan says so at the top.
