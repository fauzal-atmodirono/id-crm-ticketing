# Training delivery plan (§2.3.3)

**Scope:** the three curricula in this directory. **Status:** planned, not
delivered. No session has been run, no exercise has been dry-run, and no
sandbox tenant has been provisioned — §7 is the honest list.

A deck with no delivery plan is a file, not training. This is the schedule,
the prerequisites, the sandbox arrangements, the sign-off and the refresher
cadence.

---

## 1. The three cohorts

| Cohort | Curriculum | Topics | Exercises | Rule-derived length | Design target (spec §3.1) |
|---|---|---|---|---|---|
| Frontline agent | [`agent/`](agent/facilitator-deck.md) | 54 | 12 | 5 h 31 min | 2 h |
| Supervisor / team leader | [`supervisor/`](supervisor/facilitator-deck.md) | 71 | 20 | 7 h 49 min | 3 h |
| Administrator | [`admin/`](admin/facilitator-deck.md) | 108 | 32 | 12 h 29 min | 4 h |

The curricula are **cumulative**: a supervisor is taught the agent
curriculum plus their own additions, an administrator the supervisor
curriculum plus theirs. So a mixed cohort can attend the shared sessions
together and split for the additions — which is what §3's schedule does.

### The length finding, stated rather than smoothed over

**Every curriculum is two to three times the design's target.** The targets
were set when the handbook was a plan; it is now 108 sections, and 54 of them
are content a frontline agent needs before their first shift. Compressing the
agent curriculum into 2 hours means about 2 minutes per topic, which is not
delivery, it is a slideshow.

The generator will not scale its durations into agreement with the target —
that would produce a document that looks like it fits and does not. The
variable is the **number of sessions**, and §3 spends it. If the 2/3/4-hour
figures are contractual rather than indicative, that is a scope conversation
to have before the first cohort is booked, not after.

Durations are **derived by rule** — 3 min per topic, +1 min per two
documented steps, +5 min where the cohort does it themselves — and are **not
measured**. Re-derive them from a delivered session as soon as there is one.

---

## 2. Prerequisites

**Per trainee, before the first session:**

| Prerequisite | Why |
|---|---|
| An account on the **sandbox** tenant, with the role their cohort is being trained for | An admin cohort cannot do the admin exercises from an agent account, and a frontline cohort must not be given admin access "just for training" |
| A laptop with a current browser | The CRM is browser-only; there is nothing to install |
| Chapter 1 of the handbook, read | Login, layout and roles are assumed by every later module |
| For supervisors and administrators: the cohort below already completed, or its competency checklist signed | The curricula are cumulative; teaching escalation routing to someone who has never applied a label does not work |

**Per cohort, before the first session:**

| Prerequisite | Owner | State |
|---|---|---|
| A sandbox tenant provisioned (`deploy/scripts/add-tenant.sh sandbox`) | Platform | **NOT DONE** |
| Demo data seeded on it (`deploy/scripts/seed_demo_data`) | Platform | **NOT DONE** |
| The exercise set dry-run end to end by the facilitator | Facilitator | **NOT DONE** (§7) |
| A WhatsApp number on the sandbox tenant, for the role-plays | Platform | **NOT DONE** |
| The facilitator's own copy of the current handbook `.docx` | Facilitator | Available |

**The facilitator is not a prerequisite that can be waived.** Every role-play
exercise needs someone to play the customer, and the "Say out loud" lines in
the decks carry limitations a cohort must hear from a person rather than
discover from a customer.

---

## 3. Schedule

Sessions are sized to the derived durations in §1, at roughly 90–105 minutes
of content each, which leaves a 15-minute break in a two-hour room booking.
Session minutes below are the sum of their modules' derived minutes, so they
move when the handbook does — re-read them off the deck headings after a
regeneration.

### Shared: sessions 1–4 (agent curriculum, 331 min)

| # | Session | Content | Derived |
|---|---|---|---|
| 1 | Getting started, and the inbox | Module 01 Introduction; Module 02 up to *Priorities* | 75 min |
| 2 | Replying, with and without the AI | Module 02 from *Private notes* to *Resolving, snoozing & transcripts* | 80 min |
| 3 | Contacts, cases and how the AI behaves | Modules 03, 05, 10 | 78 min |
| 4 | Channels end to end | Modules 12, 11 (agent scenarios), 14 | 98 min |

Everyone attends sessions 1–4, including administrators. An administrator who
has never handled a conversation configures the platform for people who do.

### Supervisor additions: sessions 5–7 (+138 min)

| # | Session | Content | Derived |
|---|---|---|---|
| 5 | Cases, RSA and SLA policies | Module 05 *Case list*; Module 06 RSA; Module 09 *SLA Policies*; Module 03 *Customer 360* | 49 min |
| 6 | Reporting | Module 07, all seven report topics | 55 min |
| 7 | Supervisor scenarios | Module 11 scenarios 3, 5, 9, 10 | 34 min |

### Administrator additions: sessions 8–10 (+280 min)

| # | Session | Content | Derived |
|---|---|---|---|
| 8 | Administration | Module 09, everything except *SLA Policies* (covered in session 5) | 103 min |
| 9 | Knowledge, campaigns and the Help Center | Modules 04, 08 | 82 min |
| 10 | Integrations and administrator scenarios | Module 13; Module 11 scenarios 4, 6, 8, 11, 17 | 95 min |

**Recommended shape:** sessions 1–4 over two days for a mixed cohort;
supervisors add a half day; administrators add a further day and a half.
Deliver 8 and 9 close together — the Knowledge module configures the
assistant that Module 09's inbox settings route to.

---

## 4. The sandbox tenant, and resetting between cohorts

**Every exercise runs on the sandbox tenant. None runs on a production
tenant.** Training an agent to escalate by escalating a real customer's
complaint is not a viable exercise, and neither is teaching SLA policies by
editing the ones a live queue is measured against.

```bash
# provision once
deploy/scripts/add-tenant.sh sandbox

# between cohorts
docs/client-materials/training/reset-sandbox-tenant.sh
```

`reset-sandbox-tenant.sh` purges the previous cohort's seeded batch and seeds
a fresh one, by wrapping `deploy/scripts/seed_demo_data` — which has its own
dry-run and typed confirmation — rather than issuing its own SQL.

**What the reset does NOT undo, and this matters for the admin cohort:** the
seeder owns conversations and contacts, not configuration. Labels, SLA
policies, escalation routing, custom attributes, roles and inbox settings that
a cohort changed during sessions 5–10 **stay changed**. For an administrator
cohort, either

- re-provision the tenant (`deploy/scripts/remove-tenant.sh sandbox` then
  `add-tenant.sh sandbox`, which is destructive and takes longer), or
- give each administrator trainee their **own** sandbox tenant for those
  sessions, and accept the cost.

Pick one before booking an admin cohort. Discovering it mid-session means the
second cohort inherits the first cohort's escalation routing.

---

## 5. Competency sign-off

One checklist per cohort, generated from the same source as the deck:
[`agent/competency-checklist.md`](agent/competency-checklist.md),
[`supervisor/`](supervisor/competency-checklist.md),
[`admin/`](admin/competency-checklist.md).

- Sign a row when the trainee has done it **unaided** — for exercise rows,
  without the exercise sheet in front of them.
- A row assessed by "Q&A / observation" is a topic the handbook documents as
  behaviour to understand rather than a procedure to perform; ask for it in
  their own words.
- **An unsigned row is not a failure, it is an untrained topic.** Record which
  rows are unsigned per cohort; a row that is unsigned across most of a cohort
  is a problem with the session, not with the trainees.
- Sign-off feeds milestone artefact 05 (training manual) —
  `../governance/milestone-artefacts/05-training-manual.md`.

---

## 6. Refresher cadence

| Trigger | Who | What |
|---|---|---|
| **A fork patch or backend change that alters a documented surface is deployed** | The cohorts affected | Regenerate the curricula, diff them, and re-run only the modules whose topics changed. This is the *primary* trigger — a date-based refresher that ignores a shipped change teaches last quarter's UI |
| Quarterly | Frontline agents | 60 min: the diff since the last refresher, plus the limitations list in each channel playbook |
| Every 6 months | Supervisors | 90 min: reporting and SLA modules, re-run against the current quarter's real figures |
| Annually | Administrators | Full re-run of sessions 8–10; RBAC and escalation routing drift fastest because they are edited rarely and remembered badly |
| New joiner | Anyone | Their cohort's curriculum within their first two weeks, and their checklist signed before they take unsupervised conversations |
| **A topic moves out of §7's "cannot teach yet" list** | The cohorts affected | A new module, not a footnote. Nine topics are currently untaught for reasons outside training's control |

**The regeneration is part of the cadence, not a chore beside it.** Run
`build_crm_feature_guide.py --check` in CI: it fails while a committed
curriculum is stale, which is how the frontline deck avoids going quietly out
of date.

---

## 7. What has NOT been verified

Read this before quoting anything above as delivered.

| Claim a reader might assume | Actual state |
|---|---|
| The exercises work as written | **Not dry-run.** No sandbox tenant exists; there is no live Chatwoot, Gemini or Twilio in the environment these were generated in. "Completable as written" is owed |
| The sandbox reset script works | **Never executed.** It wraps tooling that has its own tests, and the wrapper itself has never run against a tenant |
| The durations are realistic | **Derived by rule, never measured.** No session has been delivered or timed |
| A cohort could be booked today | The sandbox tenant, its demo data and its WhatsApp number are all "NOT DONE" in §2 |
| The curricula cover everything the programme built | **They do not.** Nine topics are listed as untaught at the end of each deck, with the reason — most are fork patches that have never been built |
| The phone/voice exercises can be practised | No real Twilio call has ever been placed, and every `PHONE_*` capability switch is off on the tenant. Those topics are presentation-only |

**Related risks:** `../governance/risk-register.md` R7 (nine unbuilt fork
patches), R10 (no real call), R16 (built-and-unreachable), and
`../../analysis/2026-08-09-blocked-work-register.md` §3h, §3j, §3m.
