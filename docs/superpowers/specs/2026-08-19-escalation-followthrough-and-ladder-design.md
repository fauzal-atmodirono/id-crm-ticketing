# Escalation follow-through and the dealer ladder

**Date:** 2026-08-19
**Covers:** the remaining PARTIAL items in Proton's CRM process flow (Email tab
of `docs/client-materials/CRM Process Flow (1).xlsx`), the five-step escalation
matrix below it, and three defects found in a live end-to-end run on the
`proton` tenant on 2026-08-19.
**Supersedes in scope:** `docs/superpowers/specs/2026-08-04-pkg-g-escalation-policy-engine-design.md`
(Package G) — that spec's §4.1–4.5 are carried forward here with one deliberate
deviation, recorded in §3.1.
**Related:** `docs/analysis/2026-08-08-rfp-2026_028-gap-analysis.md` §9.3,
`docs/analysis/2026-08-05-email-channel-questions-for-proton.md`

---

## 1. Why this exists

A live run on 2026-08-19 walked the whole email escalation path: a customer
email arrived, an agent labelled the case `dept_aftersales` + `escalate`, the
`aftersales` PIC received the escalation mail, replied, and the reply was
correctly linked back onto the case as a private note with an AI-drafted
customer reply beside it.

Everything worked. The customer heard nothing further, which is by design — a
PIC's reply is internal correspondence and a human decides what the customer is
told. But the run exposed that the design's *follow-through* is uninstrumented,
and it surfaced two defects visible to the customer.

This spec closes those, then builds the reminder ladder the SOP specifies but
which has never existed, then closes the small remaining PARTIALs.

## 2. What is being built

Four tracks, in this order. Track 0 lands first because two of its three items
are visible to a real customer and all three are small.

| Track | Item | Requirement |
|---|---|---|
| 0 | Clean customer-ack subject | defect |
| 0 | Customer-update clock after a dealer/PIC reply | B-EM-05 follow-through |
| 0 | One-click *Send to customer* on the draft note | operational |
| 0 | Thread the ack onto the customer's own email thread | defect |
| 1 | The five-step dealer ladder | B-EM-08 / escalation policy matrix |
| 2 | `attend_after` surfaced in My-Tasks | B-EM-04 |
| 2 | Explicit-agent reassignment endpoint | B-EM-06 / B-WA-15 |
| 3 | After Sales escalation runbook | verification |

Out of scope, and why:

- **B-EM-01** (inbound to `e.mascentre@pronet.my`) — needs Proton's IMAP/SMTP
  credentials (Q1). No code closes it.
- **Enabling flags on `proton`** — `EMAIL_AUTOACK_ENABLED`,
  `SLA_WORKING_HOURS_ENABLED` and everything shipped here are tenant-env
  changes handed to the operator as commands, not applied from this repo.
- **Placing the step-5 call automatically** — Package C territory.
- **CC policy beyond what the SOP lists** — see §4.4.

---

## 3. Track 0 — follow-through and defects

### 3.1 The customer-ack subject

`agent/app/services/sync.py::_single_line` takes the first 100 characters of the
customer's first message as the escalation title. That title is used for all
three legs, so the customer's acknowledgement arrives as:

> Update on your case: Hi, I bought an e.MAS 7 from Proton e.MAS Petaling Jaya
> last month, plate VAB 3271. The home charger

— the customer's own words, quoted back, cut mid-word at 100 characters.

The title is *right* for the PIC and dealer legs: an inbox full of escalations
is far easier to triage when the subject says what the case is about, and those
legs also carry the `[CASE-n]` tag. It is wrong only for the customer.

**Design:** the notifier takes a separate `customer_subject`, defaulting to
`Update on your case (#<conv_id>)`, operator-overridable via the same tenant
settings facade that already owns `email_escalation_ack_template`. The internal
legs are untouched. A test asserts the customer subject never contains the
message body.

### 3.2 The customer-update clock

When a dealer or PIC replies, `escalation_replies.py` stamps
`escalation_replied_at`, adds the `escalation_replied` label, records an
acknowledgement in the audit trail, and posts the draft. Then nothing. No timer
measures how long the customer waits after the answer already exists.

**Design:** a third breach type alongside the existing two, computed from
`escalation_replied_at` rather than `created_at`:

```
CUSTOMER_UPDATE_DUE   escalation_replied_at + escalation_customer_update_hours
                      (default 4, working hours, per the SOP's B-EM-05)
```

- Cleared when an outgoing public message is sent to the customer after
  `escalation_replied_at`. Note this is *outgoing and public* — the private
  notes that carry the dealer's reply and the draft must not satisfy it, or
  the clock would clear itself the instant it started.
- Fires a warning at half-time, mirroring `tasks_reminder_warning_minutes`.
- Surfaces in My-Tasks on its own field, the way P6's `follow_up_at` does —
  never folded into `resolution_deadline_iso`, so it cannot be misread as an
  SLA breach in reporting.
- Runs in working hours when `SLA_WORKING_HOURS_ENABLED` is on for the inbox,
  wall-clock otherwise. Same helper as everything else; no second calendar.

Gated by `ESCALATION_CUSTOMER_UPDATE_ENABLED`, default off.

### 3.3 One-click send — NOT BUILT, and why

The intent stands: a **Send to customer** action on notes whose body starts
with the draft marker, loading the draft into the reply composer (not straight
out the door — the agent still presses send, which is the invariant this whole
design rests on).

It is **not built**, because it cannot be built safely from this checkout. The
action belongs on the conversation's message bubble, which is an **upstream
Chatwoot component**. A fork patch against an upstream file needs that file's
surrounding lines as diff context, and this sandbox cannot reach github.com to
clone upstream (see `project_chatwoot-fork-patch-network-restriction`). The
existing patches that modify upstream files carry only their own hunks, so
they cannot supply the context either. A hand-guessed patch that fails
`git apply` at image-build time breaks the whole Chatwoot image, which is a
worse outcome than the copy-paste it saves.

**What it needs:** a checkout of the upstream Chatwoot SPA at the pinned
version, then the patch is small. Everything it depends on — the draft note,
its marker text, the customer-update clock that makes acting on it urgent —
is shipped and working. Until then the draft is copy-paste, exactly as today.

### 3.4 Ack threading

The ack is sent over SMTP with no `In-Reply-To`/`References`, so it arrives as a
new thread from "Support" rather than a reply to the mail the customer sent.

**Design:** read the original inbound message's RFC Message-ID — Chatwoot stores
it as `source_id` on email-channel messages — and set both headers on the
customer ack. **Verify the field is actually populated before building this**;
if Chatwoot does not expose it, this item is dropped rather than faked, and the
spec is updated to say so. The invisible `Reply-To` correlation token already
works and is unaffected either way.

---

## 4. Track 1 — the dealer ladder

### 4.1 What the SOP requires

| Step | Trigger | TO | CC | Expected response |
|---|---|---|---|---|
| 1 | Case created in CRM within 10 min | Dealer CRE, Dealer Sales/Aftersales Mgr | Dealer Principal, PRO-NET Area & Regional Mgr, PRO-NET HOD | — |
| 2 | Email acknowledgement | — | — | Dealer CRE acknowledges within **2 working hours** |
| 3 | No response **4 working hours** after step 1 → 1st reminder | Dealer Principal | Dealer Owner, Sales/Aftersales Mgr, CRE, Area & Regional Mgr, HOD | Action taken + status update |
| 4 | Still no response **4 working hours** later → 2nd reminder | Dealer Owner | Principal, Sales/Aftersales Mgr, CRE, Area & Regional Mgr, HOD | Immediate action + resolution status |
| 5 | Cumulative **8 working hours** → final escalation | Dealer Principal by **telephone**, then Dealer Owner | as step 4 | Respond within 1 hour; failure = non-compliance under the Daily Complaint Clause |

Today EM-7 implements step 1 only.

### 4.2 Where ladder state lives — the deviation from Package G

Package G §4.3 specifies a Firestore `escalation_case` collection. **This spec
uses Chatwoot conversation custom attributes instead**, and the reasons are:

- `escalation_notified_at`, `dealer_escalated_at` and `escalation_replied_at`
  already live there, and the ladder must read all three. One store, not two.
- Idempotency comes free and in the same shape as `sla.py`'s append-and-scan:
  stamp `escalation_step<N>_sent_at` **before** the send, and a stamped step is
  never re-sent.
- Operators can see the ladder position in the conversation sidebar without a
  console.
- BI already syncs custom attributes, so Package E's reporting gets ladder
  history with no extra plumbing.

The cost is that state is only as durable as the conversation, which is
acceptable: if the conversation is gone there is nothing left to escalate.

Attributes written:

```
escalation_step            int    highest step sent so far (1..5)
escalation_step2_due_at     ISO   when the CRE ack was expected
escalation_step3_sent_at    ISO   1st reminder
escalation_step4_sent_at    ISO   2nd reminder
escalation_step5_raised_at  ISO   phone task raised
escalation_acknowledged_at  ISO   set by reply detection or by an agent
```

### 4.3 The ladder as data

`features/chat/escalation_policy.py`, pure, no I/O:

```
EscalationStep:
  step_no:              int
  delay_working_hours:  float     # measured from the step-1 send
  to_roles:             [str]
  cc_roles:             [str]
  template:             str
  channel:              "email" | "phone"
```

The default table encodes §4.1 exactly. `ESCALATION_POLICY_STEPS_JSON`
overrides it wholesale, so an operator retunes timers without a deploy — step 5
is already labelled "NEW PROCESS" in Proton's own document and will change
again.

Role resolution reads the dealer record (§4.4). A step whose TO list resolves
empty **skips and logs**; it never raises and never silently promotes a CC to a
TO. An incomplete contact matrix is the expected state for months.

### 4.4 The contact model

`DealerRecord` today is a flat group (`emails[]` + `cc_emails[]`, patch `0046`).
It gains named roles:

```
DealerRecord:
  dealer, name, region
  contacts: {cre, sales_aftersales_mgr, principal, owner}
  emails[]        # retained: legacy group, migrates to contacts.cre on read
  cc_emails[]
```

Plus a PRO-NET internal record keyed by region:

```
ProtonNetRecord:
  region, area_regional_mgr, hod
```

Migration is read-side only: a record with `emails[]` and no `contacts` resolves
`cre` from `emails[0]` and leaves the rest empty, so the live proton config
keeps working untouched and step 1 behaves exactly as it does today.

On CC — Package G §4.6 said do not build CC until the client confirms, because
of the leak risk. That risk is already resolved in the shipped code and this
spec keeps the resolution: **the customer acknowledgement CCs nobody, ever**,
asserted as a regression test across every flag combination. The internal legs
CC exactly the roles the SOP lists, which is what `cc_emails` already does for
step 1.

### 4.5 The sweep

`features/chat/escalation_ladder.py`, modelled on `start_sla_scheduler`:

1. Fetch conversations in scope (`sla_inbox_ids`) carrying `escalate` and a
   `escalation_notified_at` stamp.
2. Skip any with `escalation_acknowledged_at`, `escalation_replied_at`, or
   status `resolved`.
3. Compute elapsed working hours since the step-1 send, find the highest step
   now due, and **advance one step per sweep** — a 24-hour outage advances one
   rung, not four. Emailing a Dealer Owner about a case they were never given a
   chance to see is worse than a late reminder.
4. Stamp, then send.

### 4.6 Step 5

The CRM does not place the call. It raises an agent task: `follow_up_at` set to
the 1-hour deadline plus a private note carrying the Principal's number, the
case context, and what the Daily Complaint Clause requires. That surfaces in the
existing My-Tasks view — no new surface.

### 4.7 Rollout

`ESCALATION_POLICY_ENABLED` defaults off. When on, `ESCALATION_POLICY_DRY_RUN`
defaults **on**: the sweep logs `would send step N to X, cc Y, at Z` and sends
nothing. A full working week of dry-run output is reviewed before live. A ladder
that mails dealer owners on wrong timers damages a real business relationship
and is not recoverable with a hotfix.

---

## 5. Track 2 — the small partials

**B-EM-04 — "attend next business hour".** Already half-built and not credited:
`sync.maybe_stamp_business_hours` stamps `attend_after` from
`next_working_instant` at intake, gated on `BUSINESS_HOURS_STAMP_ENABLED`. What
is missing is that nothing shows it. `deadline.py` gains `attend_after_iso` on
its own field (same discipline as `follow_up_at`), `tasks_router` exposes it,
and the My-Tasks UI shows it. Closes the operational half.

**B-EM-06 — team-leader reassignment. Already closed; no work needed.** The
gap analysis (2026-08-08) says `POST /routing/assign` auto-picks and will not
accept a chosen agent. P6 Task 8 shipped exactly that afterwards: an optional
`agent_id` naming a supervisor's chosen agent, gated on the `routing.reassign`
permission inside the handler so the auto-pick path the live handoff depends on
is unaffected (`features/routing/router.py`, `test_routing_reassign.py`).
Reassignment is also audited (patch `0026`) and visible in the Cases list
(`0048`). This spec re-verifies it and updates the gap analysis rather than
building anything.

---

## 6. Testing

Per track, the tests that must exist:

- **Subject:** customer subject never contains message body text; internal legs
  keep the descriptive title; an operator override wins.
- **Customer-update clock:** starts at `escalation_replied_at`, not
  `created_at`; a private note does **not** clear it; an outgoing public message
  does; warning fires once at half-time; disabled flag → no field, no breach.
- **Policy table:** each step resolves the right TO/CC; missing role skips with
  a log; a CC role never becomes a TO; JSON override replaces the table.
- **Working hours:** a case escalated Friday afternoon does not fire its 4-hour
  reminder on Saturday.
- **Sweep:** one step per sweep; a 24-hour outage advances one; re-running a
  sweep sends nothing twice; ack halts the ladder; resolved halts the ladder.
- **Ack detection:** a dealer reply halts; an `Auto-Submitted` auto-reply does
  not; an unknown sender does not halt but is surfaced.
- **Privacy:** the customer-facing message has no CC recipients — asserted
  across every flag combination.
- **Dry run:** with dry-run on, no send function is called at all.
- **All flags off:** both suites green and behaviour byte-identical to today.

## 7. Definition of done

- A case escalated on `proton` walks the five steps on the SOP's timers in
  working hours, addressing the right roles, stopping the moment the dealer
  genuinely responds, raising a phone task at step 5, and recording every step
  where BI can read it.
- The customer's acknowledgement has a clean subject and threads onto their own
  email.
- A dealer reply starts a measured, visible clock on updating the customer, and
  the draft can be sent in one click.
- Every new behaviour is behind a flag that is off by default, and the ladder
  additionally defaults to dry-run.
- The After Sales runbook walks an operator through all of it, timers included.
- Nothing merged to `main`.
