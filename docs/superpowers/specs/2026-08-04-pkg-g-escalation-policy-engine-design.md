# Package G — Escalation-policy engine (dealer ladder, timers, reminders)

**Date:** 2026-08-04
**Covers:** Proton's written escalation SOP in `docs/client-materials/CRM
Process Flow (1).xlsx` → **Email** tab (the 5-step matrix below the flow table)
**Supersedes in scope:** EM-7, the one-shot two-thread escalation email shipped
2026-08-03
**Effort:** large. **Partially blocked** — see §7.
**Related:** `docs/analysis/2026-08-05-email-channel-questions-for-proton.md`

---

## 1. Why this package exists

The six packages A-F were written on 2026-08-04 from the demo-feedback audit,
before anyone had read the Email tab of Proton's process-flow workbook. That
workbook contains a **written escalation policy with hard timers, role-based
recipients and a non-compliance clause** — and none of the six packages covers
it. Package A treats email escalation as "EM-7, already built"; that is only
true of the first email in a five-step ladder.

This is the largest unplanned piece of work currently visible, and it is a
contractual process rather than a nice-to-have, so it gets its own spec.

## 2. What the SOP requires

| Step | Trigger | TO | CC | Expected response |
|---|---|---|---|---|
| 1 | Call Centre creates the case in CRM **within 10 min** | Dealer CRE, Dealer Sales/Aftersales Mgr | Dealer Principal, PRO-NET Area & Regional Mgr, PRO-NET HOD | — |
| 2 | Email acknowledgement | — | — | Dealer CRE acknowledges **within 2 working hours** |
| 3 | No response **4 working hours** after step 1 → **1st reminder** | Dealer Principal | Dealer Owner, Dealer Sales/Aftersales Mgr, Dealer CRE, PRO-NET Area & Regional Mgr, PRO-NET HOD | Action taken + status update |
| 4 | Still no response **4 working hours** later → **2nd reminder** | Dealer Owner | Dealer Principal, Dealer Sales/Aftersales Mgr, Dealer CRE, PRO-NET Area & Regional Mgr, PRO-NET HOD | Immediate action + resolution status |
| 5 | Cumulative **8 working hours** → **final escalation** | Dealer Principal by **telephone**, then Dealer Owner by telephone | as step 4 | Respond **within 1 hour**; failure = non-compliance under the **Daily Complaint Clause** |

## 3. Gap against what is built

| SOP requirement | Today |
|---|---|
| Four dealer roles: CRE, Sales/Aftersales Mgr, Principal, Owner | **One** email address per dealer (`DealerStore`, patch `0039`) |
| Two PRO-NET roles: Area & Regional Mgr, HOD, possibly per region | Not modelled |
| Different TO and CC per step | Single recipient, **no CC at all** |
| Timers at 2h / 4h / 4h / cumulative 8h, in working hours | No timers |
| Automatic reminder emails | None — escalation is one-shot |
| Detect that the dealer replied, to stop the clock | None |
| Telephone step with a 1-hour response window | Not modelled |
| Non-compliance recording and reporting | Not modelled |

So EM-7 implements **step 1 only**, to a single recipient, without CC.

## 4. Design

### 4.1 Data model — extend, don't replace

`PicStore` / `DealerStore` (`features/chat/pic_store.py`, Firestore-backed,
already RBAC-gated behind `escalation.manage`) are the right home. Extend the
dealer record from one address to a **role map**:

```
DealerRecord:
  slug, name, region
  contacts: { cre, sales_aftersales_mgr, principal, owner }   # each an email
```

and add a separate PRO-NET internal contact set, keyed by region if Q7 in the
questions doc confirms Area/Regional Managers are regional:

```
ProtonNetRecord:
  region, area_regional_mgr, hod
```

Migration: the existing single dealer email becomes `contacts.cre`, which is
the closest match to how it is used today, and the other three start empty. A
step whose TO address is empty must **skip and log**, never crash — an
incomplete contact matrix is the expected state for months.

### 4.2 The ladder as data, not code

Encode the five steps as a **policy table**, not as branching logic:

```
EscalationStep:
  step_no, delay_working_hours, to_roles[], cc_roles[], template, channel(email|phone)
```

Two reasons. The SOP already changed once (step 5 is labelled "NEW PROCESS"),
and Proton will want to tune the timers. A table means an operator edits rows
rather than an engineer editing conditionals — consistent with how escalation
routing and SLA policies already work.

### 4.3 The scheduler

The hard part is not sending mail, it is *when*.

- On escalation, persist an `escalation_case` row: conversation id, dealer,
  current step, timestamps for each step, and the next due time.
- A periodic worker sweeps rows whose next-due time has passed and advances
  them one step. **Advance one step per sweep**, so a long outage doesn't fire
  steps 3, 4 and 5 within the same minute — that would email a Dealer Owner
  about a case they were never given a chance to see.
- All arithmetic runs in **working hours**, reusing
  `features/metrics/business_hours.py` rather than adding a second calendar.
  Public-holiday source is Q9 in the questions doc, still open.
- Idempotency: each step records `sent_at` before sending, and a step already
  stamped is never re-sent. Duplicate escalation emails to a Dealer Owner are
  worse than a late one.

### 4.4 Stopping the clock

The most under-specified part of the SOP and the biggest correctness risk.
Candidate rule, to confirm as Q8: **any inbound email into the escalation
thread from an address in that dealer's contact map counts as acknowledgement**,
stamping `acknowledged_at` and halting further steps.

Whatever rule is chosen, the failure modes must be deliberate:

- an out-of-office auto-reply must **not** count as acknowledgement (match on
  `Auto-Submitted` / `X-Autoreply` headers and skip);
- a reply from an unknown address should be logged and surfaced to an agent,
  not silently ignored;
- an agent must be able to mark a case acknowledged by hand, because the
  automatic rule will sometimes be wrong.

### 4.5 The telephone step

The CRM cannot place the call itself under this design (Package C could later).
Step 5 therefore creates an **agent task** — "call Dealer Principal for case
X" — with the number, the case context, and a place to record the outcome and
the 1-hour response deadline. A `chatwoot-my-tasks` dashboard app already
exists for agent tasks; reuse it rather than inventing a new surface.

### 4.6 CC — and the contradiction to resolve first

The 2026-07-28 demo recorded "two separate emails, **no CC/BCC**". This SOP is
built on CC lists. Our reading, pending confirmation (C1 in the questions doc):

- the **customer** acknowledgement CCs nobody, ever — the customer must never
  see dealer or PRO-NET addresses, and the dealer must never see the customer's
  thread. This is the privacy requirement behind "no CC/BCC" and it stands;
- the **internal** escalation legs CC exactly the roles the SOP lists.

**Do not build CC until this is confirmed.** Getting it wrong leaks customer
data to a dealer distribution list.

## 5. Testing

- Policy table: each step resolves the right TO/CC from a dealer record;
  missing roles skip with a log rather than raising.
- Working-hours arithmetic: a case escalated Friday afternoon does not fire its
  4-hour reminder on Saturday morning (subject to the Q9 answer); public
  holidays respected.
- Scheduler: one step per sweep; a 24-hour outage advances one step, not four.
- Idempotency: re-running a sweep sends nothing twice.
- Acknowledgement: a dealer reply halts the ladder; an auto-reply does not; an
  unknown sender does not.
- Privacy: the customer-facing message has **no** CC recipients — asserted
  explicitly, as a regression guard on §4.6.
- Every timer disabled → behaviour identical to today's EM-7.

## 6. Rollout

Ship behind `ESCALATION_POLICY_ENABLED`, default off, so EM-7 behaviour is
preserved. Enable per tenant only once that tenant's dealer contact matrix is
populated. Run in a **dry-run mode first** — log what would be sent, to whom,
at what time, without sending — for at least one full working week. A ladder
that emails dealer owners on wrong timers damages a real business relationship,
and that is not recoverable with a hotfix.

## 7. What blocks this

| Blocker | Needed from |
|---|---|
| Dealer contact matrix — 4 roles × every dealer | Proton (ask Q6) |
| PRO-NET Area & Regional Mgr + HOD, per region? | Proton (Q7) |
| The rule for detecting a dealer response | Proton (Q8) |
| Whether weekends count as working hours; holiday calendar | Proton (Q9) |
| Whether the CRM records non-compliance | Proton (Q11) |
| CC policy confirmation | Proton (C1) |

Sections 4.1-4.3 (data model, policy table, scheduler) can be built **before**
those answers arrive, since they are structure rather than content. Sections
4.4-4.6 cannot. Sequence accordingly.

## 8. Out of scope

- Placing the step-5 call automatically (Package C).
- Escalation on channels other than email — the SOP is email-specific.
- Dealer-side portal or acknowledgement UI. Dealers reply by email.
- Contract/SLA penalty calculation beyond recording the breach.

## 9. Definition of done

A case escalated on the `proton` tenant walks the five steps on the SOP's
timers in working hours, addressing the correct roles at each step, stopping
the moment the dealer genuinely responds, raising an agent task for the
telephone step, recording every step for Package E's G6 reporting — and a full
dry-run week has been reviewed with no misfires before it is switched on live.
