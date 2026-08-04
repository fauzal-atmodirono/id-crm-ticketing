# Email channel — questions & concerns for Proton

**Meeting:** 2026-08-05
**Prepared:** 2026-08-04
**Sources:** `docs/client-materials/CRM Process Flow (1).xlsx` → **Email** tab
(flow table + escalation-policy matrix image), the 2026-07-28 demo feedback
audit, and the live state of the `proton` tenant verified on 2026-08-04.

---

## 1. What the process flow already answers — don't spend meeting time on these

Read before the meeting. Several things we were about to ask are already
specified in Proton's own document.

| # | Answered | Detail |
|---|---|---|
| 1 | **One dedicated address, not several** | `e.mascentre@pronet.my`. So the design is a single Email inbox with routing done inside the CRM — not one inbox per department, and not (as far as the document says) aliases. |
| 2 | **Auto-acknowledgement rules** | One auto-reply per new email/new ticket; **not** repeated within the same thread; not triggered when the customer replies in-thread; not triggered by an agent reply; sent again for a genuinely new email/subject. |
| 3 | **The exact auto-reply wording** | Specified verbatim in the sheet — and it is **byte-identical** to what the platform already sends. Requirement already met. |
| 4 | **Assignment expectation** | "Ticket will be assigned to Agent & will be attended in next business hour", and "the case can be manually reassigned by Team Leader". |
| 5 | **Agent response SLA** | Agent must update the customer **within 4 working hours** of the email being received. |
| 6 | **CSAT** | "System will trigger Rating survey to customer to evaluate the Call Agent performance." |
| 7 | **Escalation is policy-driven** | "System will follow the Escalation email flow based on Escalation Policy" — the 5-step matrix embedded below the table. |

### 1.1 The escalation policy, as specified

| Step | Trigger | TO | CC | Required response |
|---|---|---|---|---|
| 1 | Call Centre creates case in CRM **within 10 min** | Dealer CRE, Dealer Sales/Aftersales Mgr | Dealer Principal, PRO-NET Area & Regional Mgr, PRO-NET HOD | — |
| 2 | Email acknowledgement | — | — | Dealer CRE acknowledges **within 2 working hours** |
| 3 | No response **4 working hours** after step 1 → 1st reminder | Dealer Principal | Dealer Owner, Dealer Sales/Aftersales Mgr, Dealer CRE, PRO-NET Area & Regional Mgr, PRO-NET HOD | Action taken + status update |
| 4 | Still no response **4 working hours** after step 3 → 2nd reminder | Dealer Owner | Dealer Principal, Dealer Sales/Aftersales Mgr, Dealer CRE, PRO-NET Area & Regional Mgr, PRO-NET HOD | Immediate action + resolution status |
| 5 | **New process** — no response after cumulative **8 working hours** | Dealer Principal by **telephone**, then Dealer Owner by telephone | Dealer Owner, Dealer Sales/Aftersales Mgr, Dealer CRE, PRO-NET Area & Regional Mgr, PRO-NET HOD | Respond **within 1 hour**; failure triggers non-compliance under the **Daily Complaint Clause** |

---

## 2. Questions we still need answered

Ordered by how much they block engineering.

### Mailbox and access

**Q1. Who hosts `e.mascentre@pronet.my`, and can we have IMAP/SMTP access?**
Google Workspace and Microsoft 365 behave very differently here — Microsoft has
largely disabled basic authentication, which means OAuth rather than a
password, and that is a different setup path. We need to know which, plus the
credentials (an app password, or an OAuth app registration).

**Q2. Is anything else delivered into that mailbox?**
Specifically: do departmental aliases (sales@, service@, rsa@ …) forward into
it? This matters because **Chatwoot's automation rules cannot read the To:
address** — verified in the running build. If aliases exist, alias-based
routing needs custom work on our side. If it is genuinely one address with one
purpose, no work is needed.

**Q3. Will that mailbox carry anything other than customer email?**
Newsletters, system notifications, vendor mail and receipts all become cases if
they land in the inbox. Is it a clean, dedicated mailbox?

### Routing and assignment

**Q4. What rule assigns a case to an agent?**
The flow says "assigned to Agent" but not how. Options: round-robin across all
agents; by division (Sales / After Sales / Apps / Charging / Product /
Marketing, per the monthly report taxonomy); by language; or by dealer region.
We can do any of them, but the answer changes what we build.

**Q5. Do agents specialise, or is any agent able to take any email case?**
Determines whether we need skill-based routing or simple availability-based
round-robin.

### Escalation policy — the biggest gap

**Q6. We need the full dealer contact matrix.** Per dealer, four distinct
addresses: **CRE**, **Sales/Aftersales Manager**, **Principal**, **Owner**.
Today the platform stores one email per dealer, so this is new data.

**Q7. We need the PRO-NET internal contacts:** **Area & Regional Manager** and
**HOD**. Is Area/Regional Manager per-region — i.e. does the correct CC depend
on which dealer the case went to?

**Q8. How does the system know the dealer responded?**
This decides whether the reminder clock stops. Is it: any reply into the same
email thread; a reply from a specific address; or an agent manually marking it
acknowledged? Automatic detection is much better for the SLA reporting, but it
needs a rule we can implement.

**Q9. What exactly counts as "working hours" for the 2h / 4h / 8h clocks?**
The auto-reply text quotes operating hours of Mon–Fri 08:30–17:30 **and**
Sat/Sun/PH 09:00–17:00. So do the escalation clocks run at weekends? And whose
public-holiday calendar — national, or per state (which differ in Malaysia)?

**Q10. Step 5 is a phone call. What does the CRM do?**
Prompt an agent to call and log the outcome, or something more? And should
"failure to respond within 1 hour" be recorded against the dealer
automatically?

**Q11. Does the CRM need to track and report non-compliance** under the Daily
Complaint Clause, or is that handled outside the system?

### SLA, CSAT and reporting

**Q12. Should the 4-working-hour agent SLA be enforced or merely measured?**
We have SLA policies that can warn an agent and escalate on breach — do they
want alerts, or only the number in the monthly report?

**Q13. Is the 10-minute case-creation KPI (step 1) something the CRM reports
on?** It is a call-centre KPI, but it is measurable if they want it.

**Q14. How is the CSAT survey delivered on email?** A link in the closing
email? What scale — the 1–5 used elsewhere, or something else? And is it per
case or per agent?

---

## 3. Concerns to raise

### C1. "No CC/BCC" from the demo contradicts the written policy — we need this resolved explicitly

In the 2026-07-28 demo the requirement was recorded as two separate emails with
**no CC/BCC**. The escalation policy in this workbook is built almost entirely
on CC lists — every step CCs the Dealer Principal, PRO-NET Area & Regional
Manager and HOD.

Our reading, to confirm with them: **the customer-facing acknowledgement never
CCs anyone** (that was the privacy point — the customer must not see dealer or
internal addresses, and the dealer must not see the customer thread), while
**the internal escalation legs CC the roles listed in the policy**. If that
reading is right, the current implementation needs to change to add CC support
on the internal leg. If it's wrong, the policy needs revising. Either way it
should not be left ambiguous, because it is a data-privacy decision.

### C2. What is built today is materially smaller than the policy

Worth being straight with them about scope:

| Policy requires | Built today |
|---|---|
| 4 dealer contact roles + 2 PRO-NET roles | One email address per dealer |
| Per-step TO and CC lists | Single recipient, no CC |
| Timed reminders at 2h / 4h / 4h / cumulative 8h in working hours | No timers, no reminders — escalation is one-shot |
| Automatic detection that the dealer replied | None |
| Telephone escalation step with 1-hour response tracking | Not modelled |
| Non-compliance recording | Not modelled |

This is a real build, not a configuration change. Setting that expectation
tomorrow is better than discovering it later.

### C3. Mailbox hygiene determines whether "every email reaches the CRM" is true

Verified from Chatwoot's fetch code on the running system: it reads the
**INBOX folder only**, searching mail from **the last day**, deduplicating by
message-id. Consequences worth stating to Proton:

- any server-side rule that files mail away from the inbox makes it invisible
  to the CRM — permanently;
- there is **no historical import**; only mail arriving after go-live appears;
- if the platform is down for more than a day, that window is never re-scanned;
- the spam folder is never read.

So the mailbox must deliver customer mail straight to the inbox, with no
filters. This is a request to their IT, and it is easy to get wrong.

### C4. Auto-acknowledgement is currently satisfied, but one edge case needs testing

The platform sends exactly the specified text, once per new conversation. The
untested case is: a conversation is resolved, and the customer then replies to
that same old thread. Depending on configuration Chatwoot may open a **new**
conversation, which would send a second auto-reply into the same thread — which
the policy explicitly forbids. We will test this before go-live; no need to
discuss it with them, but do not claim the rule is fully met yet.

### C5. Infrastructure prerequisites that block go-live

- **A real domain with HTTPS.** The demo tenant currently runs plain HTTP,
  which also blocks the Facebook/Instagram channels. A production domain solves
  both.
- **Mailbox credentials** per Q1.
- Where escalation mail is sent **from** — the same `e.mascentre@pronet.my`
  address, or a separate no-reply/escalation sender? Dealers replying to
  escalation mail should land somewhere sensible.

---

## 4. The ask list — what we need Proton to hand over

1. IMAP/SMTP (or OAuth) access to `e.mascentre@pronet.my`, and confirmation of
   the hosting platform.
2. The dealer contact matrix: CRE, Sales/Aftersales Manager, Principal and
   Owner email addresses for every dealer.
3. PRO-NET Area & Regional Manager and HOD contacts, mapped per region if
   regional.
4. The public-holiday calendar used for working-hour calculations.
5. A production domain (or subdomain) pointed at the platform, so HTTPS can be
   enabled.
6. Confirmation of the CC policy in C1.
7. The assignment rule in Q4.

Items 1, 2 and 5 are on the critical path — nothing about email escalation can
be tested end to end without them.
