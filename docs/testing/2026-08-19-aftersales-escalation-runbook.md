# After Sales escalation — step-by-step runbook

**What this is.** One case, walked from a customer's email through every rung
of Proton's escalation policy, starting from the **After Sales** label. It says
what to do, what to watch, and what counts as a pass at each step.

**Who it is for.** An operator running the flow on the `proton` tenant, and
anyone verifying the 2026-08-19 escalation work before it goes in front of the
client.

**Time.** 25 minutes for sections A–D. The ladder (section E) runs on
working-hour timers, so either use the fast-clock config in §E.0 or leave the
case running and come back.

> **Everything here is off by default.** Section G lists the exact env changes,
> in the order they should be switched on. Until you make them, the case stops
> after section C, which is today's shipped behaviour.

---

## The shape of the flow

```
customer email
      │
      ▼  auto-acknowledgement (EMAIL_AUTOACK_ENABLED)
   CASE OPEN
      │  agent labels:  dept_aftersales + dealer_<slug> + escalate
      ▼
  ┌─ STEP 1 ─ three emails ───────────────────────────────────────┐
  │  customer  "Update on your case (#N)"     no CC, ever         │
  │  PIC       [Escalation] [CASE-N] …        + department CC     │
  │  dealer    [Escalation] [CASE-N] …        CRE + Sales/AS Mgr, │
  │                                            CC Principal +     │
  │                                            PRO-NET region     │
  └───────────────────────────────────────────────────────────────┘
      │
      │  ── dealer replies at any point ──► ladder STOPS
      │                                     private note + AI draft
      │                                     customer-update clock STARTS
      ▼  2 working hours, no reply
   STEP 2  acknowledgement window closes (nothing is sent)
      ▼  4 working hours
   STEP 3  1st reminder → Dealer Principal    CC Owner, S/AS Mgr, CRE, PRO-NET
      ▼  8 working hours
   STEP 4  2nd reminder → Dealer Owner        CC Principal, S/AS Mgr, CRE, PRO-NET
      ▼
   STEP 5  telephone task in My-Tasks, 1-hour response window
```

---

## A — Before you start

- [ ] **A1. The After Sales department has a PIC.** CRM → Escalation Routing →
      *Departments*. There must be a row whose department is `aftersales`, with
      a real mailbox in **PIC email**.
      *Pass:* the row exists and the address is one you can read.

- [ ] **A2. The dealer has its ladder contacts.** Same page → *Dealer groups*.
      Pick the dealer you will label (e.g. `kl_glenmarie`) and fill in:

      | Field | Who | Used by |
      |---|---|---|
      | Dealer CRE | the service desk | Step 1 |
      | Sales / Aftersales Mgr | | Step 1 |
      | Dealer Principal | | Step 3 — 1st reminder |
      | Dealer Owner | | Step 4 — 2nd reminder |
      | PRO-NET region | e.g. `central` | decides who is CC'd |

      Use four addresses **you can read** for a test run — the whole point of
      the exercise is watching who receives what.

      *Pass:* the Ladder contacts column shows all four names, not
      "None — reminders will be skipped".

      > A blank role does not fall back to somebody else. Its rung is skipped
      > and logged. That is deliberate: a "2ND REMINDER — immediate action
      > required" landing on the service desk that has been reading the thread
      > all along is worse than a skipped step.

- [ ] **A3. The PRO-NET region contacts exist** (optional; they are CC only).
      No admin page yet — set them through the API:

      ```bash
      curl -X PUT "$BACKEND/admin/escalation/pronet/central" \
        -H "Content-Type: application/json" \
        -H "x-chatwoot-access-token: $TOKEN" \
        -d '{"area_regional_mgr":"arm@example.com","hod":"hod@example.com"}'
      ```

      *Pass:* `GET /admin/escalation/pronet` lists the region.

- [ ] **A4. Watch the logs.** Keep this open in a second terminal:

      ```bash
      gcloud compute ssh crm-ticketing --zone=asia-southeast2-a \
        --command='sudo docker logs -f proton-backend 2>&1 | grep -E "escalation_ladder|customer_update"'
      ```

---

## B — The case arrives

- [ ] **B1. Send the email.** From a mailbox you control, to the tenant's
      Email inbox address. Write a real After Sales complaint — the subject
      matters later, so make the first line meaningful:

      > Hi, I bought an e.MAS 7 from Proton e.MAS Petaling Jaya last month,
      > plate VAB 3271. The home charger has stopped charging the car.

- [ ] **B2. Wait one IMAP poll (about 2 minutes).**
      *Pass:* a new conversation appears on the Email inbox.

- [ ] **B3. The acknowledgement.** With `EMAIL_AUTOACK_ENABLED=true`, the
      customer receives the SOP's auto-reply once.
      *Pass:* one acknowledgement, and **no second one** when you reply in the
      same thread.

---

## C — Escalate from the After Sales label

- [ ] **C1. Open the case and apply three labels, in this order:**

      1. `dept_aftersales` — decides **which PIC** is emailed. The label must
         carry the `dept_` prefix; a bare `aftersales` label is not read as a
         department and the PIC leg silently sends nothing.
      2. `dealer_<slug>` — decides **which dealer**, and is what the ladder
         climbs. Without it there is no dealer leg and no ladder.
      3. `escalate` — fires everything.

- [ ] **C2. Within a few seconds, three emails go out.**

      | Recipient | Subject | CC |
      |---|---|---|
      | the customer | `Update on your case (#N)` | **nobody, ever** |
      | the After Sales PIC | `[Escalation] [CASE-N] Hi, I bought an e.MAS 7…` | the department's CC list |
      | the dealer | `[Escalation] [CASE-N] Hi, I bought an e.MAS 7…` | Principal + PRO-NET |

      *Pass, and worth checking deliberately:*
      - the customer's subject is **`Update on your case (#N)`** — not their
        own email quoted back at them, truncated mid-word. That was the
        2026-08-19 defect;
      - the customer's copy arrives **inside their own email thread**, not as
        a new message from "Support";
      - the customer's copy has **no CC recipients**. If you ever see one,
        stop and raise it — dealer and PRO-NET addresses must never reach a
        customer.

- [ ] **C3. Check the sidebar.** The conversation's custom attributes now show
      `escalation_notified_at`. That stamp is what the ladder measures from,
      and its absence is why a case that was never escalated can never be
      climbed.

---

## D — The dealer answers (the normal, happy path)

Do this section **or** section E, not both on the same case: a reply stops the
ladder, which is the point.

- [ ] **D1. Reply from the dealer CRE mailbox.** Keep the subject line intact
      (the `[CASE-N]` tag and the `+case<N>` Reply-To are what link it back)
      and leave the quoted trail alone.

- [ ] **D2. Within a poll cycle, on the ORIGINAL case:**
      - a private note: `Reply from … <cre@…>:` with just what they typed;
      - a second private note: `Suggested customer reply (draft — review
        before sending)`;
      - the label `escalation_replied`, and the attribute
        `escalation_replied_at`;
      - the throwaway conversation the reply landed in is labelled
        `escalation_reply` and resolved.

      *Pass:* all four. **No email goes to the customer** — that is by design.
      The dealer's words are internal correspondence and a human decides what
      the customer is told.

- [ ] **D3. The customer-update clock starts.** Open My-Tasks. The case now
      shows a **Customer update** countdown of 4 working hours.
      *Pass:* the countdown is there and is counting.

- [ ] **D4. Answer the customer.** Copy the draft into the reply composer,
      edit it, send it as a normal (public) reply.
      *Pass:* the Customer update column clears, and `customer_updated_at`
      appears in the sidebar.

      > A private note does **not** clear it. The two notes that arrive with
      > the dealer's reply are private, so a clock that cleared on any
      > activity would clear itself the instant it started.

- [ ] **D5. Leave one case unanswered on purpose** and come back after the
      window. *Pass:* My-Tasks turns the countdown red, raises a desktop
      notification, and the audit trail records `CUSTOMER_UPDATE_DUE`.

- [ ] **D6. The out-of-office case.** Reply from the dealer mailbox with the
      subject `Automatic reply: out of office`.
      *Pass:* a private note appears saying **"not counted as a response; the
      escalation clock is still running"**, and `escalation_replied_at` is
      **NOT** stamped. An away message must never satisfy the escalation
      policy.

---

## E — The ladder, when nobody answers

### E.0 Testing without waiting eight working hours

The ladder's timers are data. Override them for a test run, and put them back
afterwards — this is the whole reason the table is configurable:

```bash
# 2 minutes / 4 minutes / 8 minutes instead of 2h / 4h / 8h working hours
ESCALATION_POLICY_STEPS_JSON='[
  {"step_no":1,"delay_working_hours":0,"to_roles":["cre","sales_aftersales_mgr"],"cc_roles":["principal","area_regional_mgr","hod"]},
  {"step_no":2,"delay_working_hours":0.033,"to_roles":[],"cc_roles":[],"label":"ACKNOWLEDGEMENT DUE"},
  {"step_no":3,"delay_working_hours":0.066,"to_roles":["principal"],"cc_roles":["owner","sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"1ST REMINDER"},
  {"step_no":4,"delay_working_hours":0.133,"to_roles":["owner"],"cc_roles":["principal","sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"2ND REMINDER"},
  {"step_no":5,"delay_working_hours":0.133,"to_roles":["principal","owner"],"cc_roles":["sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"FINAL ESCALATION - TELEPHONE","channel":"phone"}
]'
ESCALATION_POLICY_SCAN_INTERVAL_SECONDS=60
```

Also set `SLA_WORKING_HOURS_ENABLED=false` for the test, or a run started
after 17:00 will correctly refuse to advance until morning.

### E.1 Dry run first — do not skip this

- [ ] **E1. With `ESCALATION_POLICY_ENABLED=true` and
      `ESCALATION_POLICY_DRY_RUN=true`** (the default), escalate a case and
      leave it unanswered.

      *Pass:* the log shows one line per rung and **no mail is sent**:

      ```
      escalation_ladder_dry_run conv_id=42 step_no=3 label="1ST REMINDER"
        to=["dp@kl.my"] cc=["owner@kl.my", …] elapsed_working_hours=4.02
      ```

      Read the recipients. This is the moment to catch a Dealer Owner address
      that belongs to somebody who left, or a region with nobody in it.

      > On a live tenant, run in dry mode for a **full working week** and read
      > the output before turning it off. A ladder firing on wrong timers
      > mails a Dealer Owner about a case they were never shown, and that is
      > not recoverable with a hotfix.

### E.2 Live

- [ ] **E2. Set `ESCALATION_POLICY_DRY_RUN=false`** and recreate the backend.

- [ ] **E3. Step 2 — the acknowledgement window.** After 2 working hours with
      no reply, the sweep records the rung and sends nothing.
      *Pass:* `escalation_step2_sent_at` in the sidebar, no email.

- [ ] **E4. Step 3 — 1st reminder.** After 4 working hours.
      *Pass:* an email **to the Dealer Principal**, CC Owner / Sales-Aftersales
      Mgr / CRE / PRO-NET, subject `[1ST REMINDER] [CASE-N] …`, body naming
      how many working hours the case has gone unanswered and asking for the
      action taken and a status update.

- [ ] **E5. Step 4 — 2nd reminder.** After 8 working hours.
      *Pass:* **to the Dealer Owner**, subject `[2ND REMINDER] …`, body
      requiring immediate action and a resolution status.

- [ ] **E6. Step 5 — the telephone step.** No email at all.
      *Pass:* a private note on the case headed `☎️ FINAL ESCALATION —
      TELEPHONE REQUIRED`, naming who to call in order, the 1-hour response
      window and the Daily Complaint Clause; `follow_up_at` set to one hour
      out; the case visible in My-Tasks.

- [ ] **E7. One rung per sweep.** Stop the backend for an hour with a case
      mid-ladder, then start it.
      *Pass:* the next sweep advances **one** step. Steps 3, 4 and 5 must
      never fire in the same minute.

- [ ] **E8. Reply mid-ladder.** On another case, let step 3 go out, then reply
      from the Dealer Principal's mailbox.
      *Pass:* the reply links back onto the case (rung 3 mail carries the same
      `[CASE-N]` tag and Reply-To as rung 1), and **step 4 never fires**.

---

## F — Things that should NOT happen

Check these deliberately; each one is a real failure mode with a guard:

- [ ] The customer never receives an email with anyone in CC.
- [ ] The customer never receives the dealer's words automatically.
- [ ] A rung with a blank role is skipped and logged — never re-addressed to
      the CC list.
- [ ] A step already stamped never sends twice, however many sweeps run.
- [ ] An out-of-office never counts as a dealer response.
- [ ] A resolved case never advances.
- [ ] A reply from an address that is not in Escalation Routing is **not**
      linked; a private note names the address and points at the conversation
      it arrived in, without copying its text.

---

## G — Switching it on, in order

On the VM, in `deploy/tenants/proton.env`, then
`docker compose -p proton -f docker-compose.tenant.yml --env-file
tenants/proton.env up -d agent backend`.

**Recreate, do not restart** — `docker restart` does not re-read the env file.

```bash
# 1. The inbound acknowledgement (section B3). Already built, never enabled.
EMAIL_AUTOACK_ENABLED=true

# 2. SLA in working hours, not wall clock (B-EM-05's "4 WORKING hours").
SLA_WORKING_HOURS_ENABLED=true

# 3. The customer-update clock (section D). Read by BOTH services -- the
#    backend runs the clock, the agent writes the stamp that stops it.
ESCALATION_CUSTOMER_UPDATE_ENABLED=true
ESCALATION_CUSTOMER_UPDATE_HOURS=4

# 4. The ladder (section E). Leave dry run ON for a full working week.
ESCALATION_POLICY_ENABLED=true
ESCALATION_POLICY_DRY_RUN=true
ESCALATION_POLICY_SCAN_INTERVAL_SECONDS=300
```

Steps 1–3 are independently useful and safe to enable on their own. Step 4
depends on section A2 being complete for every dealer that could be labelled.

**Rollback** is the reverse: set the flag back to `false` and recreate. Nothing
here changes existing behaviour while its flag is off, and the attributes left
on conversations (`escalation_step*`, `customer_updated_at`) are inert.

---

## What this runbook does not cover

- **The real Proton mailbox.** Everything above runs through the Gmail test
  relay. `e.mascentre@pronet.my` has never been connected — it needs Proton's
  IMAP/SMTP credentials (Q1 in the email-channel questions doc).
- **One-click send of the AI draft.** Still copy-paste; see the design doc
  §3.3 for why the fork patch could not be built from this checkout.
- **A PRO-NET admin page.** Regional CC contacts are API-only for now (§A3).
- **Escalation on WhatsApp.** The code is there
  (`ESCALATION_ALL_CHANNELS_ENABLED`) and off; this runbook is the email flow.
