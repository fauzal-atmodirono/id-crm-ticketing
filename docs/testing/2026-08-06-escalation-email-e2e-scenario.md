# Escalation Email — End-to-End Test Scenario

**Tenant:** `proton` **CRM:** http://proton.crm.34-50-103-151.nip.io
**Date drafted:** 2026-08-06
**Scope:** the EM-7 two-thread email escalation (agent applies the `escalate`
label) and the dealer-forward leg. Executed entirely from the CRM UI plus a
mail client.

---

## 1. What is under test

| Leg | Trigger | Code path |
|---|---|---|
| Customer acknowledgment | `escalate` label on an **Email-channel** conversation | `agent/app/services/sync.py::maybe_escalate` → backend `POST /escalation/notify` → `EscalationNotifier._send_customer_ack` |
| PIC notification | same, plus a `dept_<slug>` label | `EscalationNotifier._send_email` |
| Dealer forward | same, plus a `dealer_<slug>` label | `EscalationNotifier._send_dealer_forward` |
| Dealer timestamp | any `dealer_<slug>` label, **any** channel | `sync.py::maybe_stamp_dealer_escalation` |

Not under test here: the AI-driven escalation (`EscalationNotifier.notify`),
which fires autonomously when the bot classifies a turn as a complaint and
does **not** use the `escalate` label. See §7.

---

## 2. Configuration as deployed (verified 2026-08-06)

| Setting | Service | Value |
|---|---|---|
| `EMAIL_ESCALATION_ENABLED` | agent | `true` — **the master switch for this whole document** (default `false`). With it off, applying `escalate` sends nothing and every case here silently fails. Not the same setting as `ESCALATION_EMAIL_ENABLED` two rows down: one word apart, different service, different flow. |
| `EMAIL_ESCALATION_ACK_ENABLED` | backend | `true` |
| `ESCALATION_EMAIL_ENABLED` | backend | `true` — the **backend's** AI complaint-detection escalation path, not the agent's EM-7 label flow above |
| `ESCALATION_CC_PIC` | backend | unset → default `true` (CC list is sent) |
| `EMAIL_ESCALATION_ACK_TEMPLATE` | backend | unset → default: *"Your case has been escalated to a specialist team who will follow up shortly."* |
| `PROTON_BACKEND_URL` | agent | `http://proton-backend:8080` |
| SMTP | both | Gmail relay, sender `Support <devotech29@gmail.com>` |
| `ESCALATION_REPLY_LINKING_ENABLED` | agent | `true` (TC-08, TC-09) |
| `ESCALATION_REPLY_DRAFT_ENABLED` | agent | `true` (TC-08 only — TC-09's customer-thread path never posts a draft) |
| `ESCALATION_REPLY_TO_TEMPLATE` | backend | `devotech29+case{conv_id}@gmail.com` (TC-08, TC-09 — Gmail's plus-addressing still lands in the same IMAP-polled mailbox; empty is the default and disables the reply loop entirely, dropping the `[CASE-n]` subject tag and the `Reply-To` header) |
| `EMAIL_AUTOACK_ENABLED` | agent | `true` (TC-08, TC-09, TC-10 — side effect noted in each case below) |
| `SLA_ENGINE_ENABLED` | backend | `true` (TC-10 only) |
| `SLA_INBOX_IDS` | backend | `<main inbox id>,4` — this **replaces** the default scope rather than extending it, so listing the Email inbox (`4`) alone stops the SLA engine scanning `CHATWOOT_INBOX_ID`. List both. Also widens `/tasks/mine`. |
| `SLA_ALERT_EMAIL_ENABLED` | backend | `true` (TC-10) |
| `SLA_ALERT_NOTE_ENABLED` | backend | `true` (TC-10) |

Two more things TC-08/09/10 need that are **not** env vars:

1. The Chatwoot account webhook (Settings → Integrations → Webhooks) must have
   **`message_created`** added to its subscribed events, or the agent never
   receives the dealer/customer reply at all — TC-08/09 fail silently
   without it. (TC-10's SLA note is posted directly by the backend's scan
   job, not through this webhook, so it's unaffected.)
2. This repo has no Alembic; a deployed database needs the SLA policy
   table's two new columns added by hand before the SLA Policies admin page
   can save `tier2_hours` / `reminder_warning_minutes`:
   ```sql
   ALTER TABLE sla_policies ADD COLUMN IF NOT EXISTS tier2_hours DOUBLE PRECISION;
   ALTER TABLE sla_policies ADD COLUMN IF NOT EXISTS reminder_warning_minutes DOUBLE PRECISION;
   ```

**Routing table** (Firestore `lv-playground-genai` / `proton-db`, editable at
CRM → sidebar → **Escalation Routing**):

- PIC `sales` → Aduy, `yuda.adi.pratama@devoteam.com`, CC `fauzal.atmodirono@devoteam.com`
- Dealer `komang_motor` → `komang.mertayasa@devoteam.com`
- No record for `aftersales`, `cs`, `technical` — deliberately exercised by TC-05.

**Inboxes:** id 4 `Email` (`Channel::Email`, IMAP `devotech29@gmail.com`) is the
only inbox EM-7 acts on. Ids 1 `Proton API`, 2 `Website Demo`, 3 `Twilio Proton`
are out of scope by design and are used as the negative case in TC-04.

**Labels present:** `escalate`, `escalated_l2`, `dept_aftersales`, `dept_cs`,
`dept_sales`, `dept_technical`. No `dealer_*` label exists yet — TC-02 requires
creating one.

---

## 3. Preconditions

1. You can log in to the CRM with an account that can edit conversation labels.
2. You have a mailbox to act as "the customer" that is **not**
   `devotech29@gmail.com` (that address is the inbox itself; mailing it from
   itself confuses the thread).
3. You can read `yuda.adi.pratama@devoteam.com` and, for TC-02,
   `komang.mertayasa@devoteam.com` — or accept a colleague confirming receipt.
4. SSH access to the VM for the log checks in §5 (optional but recommended —
   every failure mode in this path is silent by design).

---

## 4. Test cases

Expected email formats, common to all cases:

| Mail | Subject | Body |
|---|---|---|
| Customer ack | `Update on your case: <title>` | the ack template |
| PIC | `[Escalation] <title>` | last 10 public messages + `Reference: Chatwoot conversation #<id>` |
| Dealer | `[Escalation - Dealer Forward] <title>` | as above |

where `<title>` = the first **incoming** message of the conversation, truncated
to 100 characters.

---

### TC-01 — Happy path: customer ack + PIC notification

**Steps**

1. From your test mailbox, send an email to **devotech29@gmail.com**. Use a
   distinctive first line, e.g. `Test escalation TC-01 — my car will not start`.
2. Wait for IMAP to poll (1–2 min). In the CRM, open Conversations → Email
   inbox → the new conversation.
3. Confirm the right-hand contact panel shows your sender address. (No email on
   the contact → the ack is skipped; that is TC-06, not this case.)
4. Apply the label **`dept_sales`** first.
5. Apply the label **`escalate`** second.

**Expected**

- Your test mailbox receives `Update on your case: Test escalation TC-01 — my car will not start`.
- `yuda.adi.pratama@devoteam.com` receives `[Escalation] Test escalation TC-01 …`,
  with `fauzal.atmodirono@devoteam.com` on CC, and the body containing your
  message text and the conversation number.
- Both mails arrive within ~1 minute of applying `escalate`.

**Pass criteria:** both mails received, subject lines match, CC present.

> Label order is significant. The handler reads the labels present on the
> `conversation_updated` payload at the moment `escalate` appears; applying
> `escalate` first means no department is on the payload for that fire — see
> TC-03.

---

### TC-02 — Dealer forward

Requires creating the label first: Settings → Labels → new label named exactly
**`dealer_komang_motor`** (must match the Firestore key `komang_motor`; a label
with a space will not resolve).

**Steps**

1. Repeat TC-01 steps 1–3 with a new email, first line
   `Test escalation TC-02 — dealer forward`.
2. Apply `dept_sales`, then `dealer_komang_motor`, then `escalate`.

**Expected**

- All of TC-01's expected mails, **plus** `komang.mertayasa@devoteam.com`
  receives `[Escalation - Dealer Forward] Test escalation TC-02 — dealer forward`.
- The conversation gains a `dealer_escalated_at` custom attribute stamped with
  the current UTC time (visible via the conversation API; in the sidebar only if
  a matching attribute definition exists).

**Pass criteria:** third mail received; `dealer_escalated_at` present and
non-empty.

---

### TC-03 — Negative: `escalate` applied before the department

**Steps**

1. New email, first line `Test escalation TC-03 — label order`.
2. Apply **`escalate` first**, then `dept_sales`.

**Expected**

- Customer ack **is** sent (it does not depend on the department).
- **No** PIC mail for the `escalate` fire.
- Log line `escalation_notifier_no_pic_for_dept` in `proton-backend`.

**Pass criteria:** ack received, no `[Escalation]` mail, log line present.

This documents a real operating constraint, not a bug to file — it is why the
agent-facing instruction is "department first, then escalate".

---

### TC-04 — Negative: non-Email channel

**Steps**

1. Start a conversation on the **Website Demo** inbox (id 2) via the web widget.
2. Apply `dept_sales`, then `escalate`.

**Expected**

- **No** mail of any kind.
- No escalation lines in `proton-backend`; `proton-agent` returns early on the
  channel check.

**Pass criteria:** no mail sent, conversation otherwise unaffected.

---

### TC-05 — Unmapped department

**Steps**

1. New email to the Email inbox, first line `Test escalation TC-05 — unmapped dept`.
2. Apply `dept_cs` (no PIC record exists), then `escalate`.

**Expected**

- Customer ack sent.
- **No** PIC mail.
- Log line `escalation_notifier_no_pic_for_dept` with `department=cs`.

**Pass criteria:** ack received, no PIC mail, log line names `cs`.

Then, to close the loop: add a PIC for `cs` at CRM → Escalation Routing, repeat
the case, and confirm the PIC mail now arrives with no service restart. This
verifies the store-first lookup (`PicRegistry.lookup`) is live-editable.

---

### TC-06 — Contact without an email address

**Steps**

1. On the Email inbox, open any conversation and clear the contact's email in
   the contact panel (or use a conversation whose contact has none).
2. Apply `dept_sales`, then `escalate`.

**Expected**

- **No** customer ack (nothing to send it to).
- PIC mail **is** still sent.

**Pass criteria:** PIC mail received, no ack, no error surfaced in the UI.

---

### TC-07 — Re-trigger

The escalation fan-out is **edge-triggered**: it fires once per escalation and
is re-armed only when the `escalate` label is removed. This case checks both
halves.

**Steps**

1. On the TC-01 conversation, without touching the `escalate` label, change
   something else that updates the conversation — add any other label, or edit
   a custom attribute.
2. Then remove the `escalate` label, and re-apply it.

**Expected**

- After step 1: **no** second ack and **no** second PIC mail. The conversation
  carries an `escalation_notified_at` custom attribute from the first
  escalation, and the fan-out skips while it is set. This is what stops the
  reply loop (TC-08/TC-09) mailing the customer again on every reply — the
  linker's own writes back onto the case are exactly this kind of update.
- After step 2: the ack and PIC mails are sent **again**. Removing the label
  clears `escalation_notified_at`, so re-applying it is a genuinely new
  escalation. This is deliberate: a case escalated, worked, and escalated again
  weeks later must still reach the PIC.
- `dealer_escalated_at`, if already stamped, is **not** overwritten (that stamp
  is separate and idempotent for all time).

**Pass criteria:** nothing sent for step 1; a second pair of mails for step 2;
existing `dealer_escalated_at` unchanged.

Worth knowing before a live demo: toggling the label off and on in front of a
client still sends another round of real email — only updates that leave
`escalate` in place are suppressed.

---

### TC-08 — Dealer reply links back onto the case

**Preconditions:** `ESCALATION_REPLY_TO_TEMPLATE` set, `ESCALATION_REPLY_LINKING_ENABLED=true`,
`ESCALATION_REPLY_DRAFT_ENABLED=true`, and `message_created` subscribed on the
account webhook (Settings → Integrations → Webhooks) — without it the agent
never sees the reply at all.

**Steps**

1. Run TC-02 to produce a dealer forward. Note the conversation number, #N.
   With `ESCALATION_REPLY_TO_TEMPLATE` set, the dealer mail's subject now
   carries a `[CASE-N]` tag ahead of the title, and the mail has an
   invisible `Reply-To` on it — you don't need to see either to run this case.
2. From the dealer mailbox (`komang.mertayasa@devoteam.com` or a colleague
   confirming receipt), reply to that mail without editing the subject or
   the To address your reply lands on.
3. Wait for the IMAP poll (1–2 min).

**Expected**

- The reply is filed by Chatwoot as a brand-new conversation on the Email
  inbox (call it #M) — Chatwoot has no way to thread it onto #N on its own.
- Conversation #N gains a private note that starts with `Reply from ` and
  ends with `<komang.mertayasa@devoteam.com>:`, followed by the dealer's
  reply text on its own, with **no** quoted trail from the mail client
  (no "On ... wrote:" block, no `>` lines).
- Conversation #N gains a second private note titled exactly
  `Suggested customer reply (draft — review before sending):`, with an
  AI-drafted reply text beneath it.
- Conversation #N gains the `escalation_replied` label and an
  `escalation_replied_at` custom attribute (current UTC time). Note the name:
  a `dealer_`-prefixed marker would be read as a real dealer slug by the
  BigQuery mapping and by the `dealer_escalated_at` stamper.
- Conversation #M gains the `escalation_reply` label and is resolved.
- The dealer receives **nothing** from the CRM in response to their reply:
  - **no** auto-acknowledgement. #M is a brand-new Email-channel conversation
    and would otherwise get the tenant's customer-facing "Dear Customer" SOP
    reply (business hours, the call-centre number). The `[CASE-N]`
    correlation token on the incoming mail is what suppresses it.
  - **no** agent-rating survey. Resolving #M would otherwise post the public
    "rate our support agent from 1 to 5" message — asking an external dealer
    to rate a Proton agent. The linker closes #M's lifecycle before resolving
    it, which lands in the existing terminal-state guard.
  - The only mail the dealer should ever see from this step is their own
    sent reply. **A "Dear Customer" email or a rating request arriving in the
    dealer's mailbox is a failure of this case, not expected output.**
- Conversation #N is **not** re-escalated: no second `Update on your case:`
  mail to the customer, no second `[Escalation]` to the PIC, no second dealer
  forward. The linker's writes to #N are ordinary conversation updates and
  #N still carries `escalate`; `escalation_notified_at` is what suppresses
  the re-fire (see TC-07).

**Pass criteria:** both notes present on #N in that order, `escalation_replied`
label and `escalation_replied_at` present on #N, #M labelled
`escalation_reply` and resolved, and **no** new mail in the customer, PIC or
dealer mailboxes.

> A second reply from the same dealer address after `escalation_replied_at` is
> already stamped is silently not linked (the stamp gates the internal-reply
> path so a second note never piles on) — worth knowing before re-running
> this case on the same conversation.

---

### TC-09 — Customer reply to the acknowledgement rejoins their own case

**Preconditions:** same as TC-08 — `ESCALATION_REPLY_TO_TEMPLATE` set (the
ack email carries the correlation `Reply-To` invisibly; its subject is never
tagged, so the customer thread stays clean), `ESCALATION_REPLY_LINKING_ENABLED=true`,
and `message_created` subscribed on the account webhook (Settings →
Integrations → Webhooks) — without it the agent never sees the customer's
reply either, and the failure is silent: no note, no reopen, no error.

**Steps**

1. Run TC-01. From your test (customer) mailbox, reply to the
   `Update on your case: …` mail without editing the subject.
2. Wait for the IMAP poll.

**Expected**

- The reply is filed by Chatwoot as a brand-new conversation on the Email
  inbox (call it #M), same as TC-08.
- The reply appears on conversation #N as a normal **incoming customer
  message** (not a private note) — visible in the main thread exactly like
  a message the customer sent to start the case.
- #N reopens as a result (a real inbound message from the contact does this
  natively).
- #N gains **no** `escalation_replied_at` attribute and **no**
  `escalation_replied` label — those are internal-reply-only.
- #M still gains the `escalation_reply` label and is resolved, same as the
  dealer path. The **survey** suppression is reliable here exactly as in
  TC-08 — no "rate our support agent" message goes to the customer.
- The **auto-ack** suppression is *not* reliable on this branch, unlike
  TC-08's dealer/PIC path. The customer's original acknowledgement (`Update
  on your case: …`) is deliberately sent with no `[CASE-n]` subject tag —
  the customer's thread has to stay clean — so only the invisible `Reply-To`
  carries the correlation token, and that token is only visible via the
  webhook payload's `messages` array, which can legitimately be empty by the
  time this event fires (see `agent/app/services/lifecycle.py`'s
  `_is_escalation_reply` docstring and the test
  `test_autoack_still_sent_when_the_payload_carries_no_messages`). **The
  customer may therefore receive one more "Dear Customer" auto-acknowledgement
  email on #M. That is expected behaviour, not a defect** — do not fail this
  case on that basis alone.
- The reopen of #N does **not** re-fire the escalation: no second ack, PIC or
  dealer mail (see TC-07).

**Pass criteria:**

- the customer's reply is visible on conversation #N as an **incoming**
  message
- #N is **reopened**
- **no** `escalation_replied` label and **no** `escalation_replied_at`
  attribute on #N (those are the internal-sender path only)
- the throwaway conversation #M is **resolved** and labelled
  `escalation_reply`
- **no survey** email is sent
- **no** second escalation fan-out (no duplicate `Update on your case:`,
  `[Escalation]`, or dealer forward)

A single "Dear Customer" auto-acknowledgement landing in the customer's
mailbox on #M is **not** grounds to fail this case — see Expected above. Fail
it only if one of the six criteria above does not hold.

---

### TC-10 — SLA breach reaches the department PIC group

**Preconditions:** `SLA_ENGINE_ENABLED=true`, `SLA_ALERT_EMAIL_ENABLED=true`,
`SLA_ALERT_NOTE_ENABLED=true`, the Email inbox id (`4`) listed in
`SLA_INBOX_IDS` **alongside the main inbox id** (the value replaces the
default scope, it does not extend it — see §2), and a short
**Response window (hours)** set for the Email
inbox at CRM → SLA Policies (e.g. `0.05` ≈ 3 min) so the case breaches
during the test — that is the field's exact on-screen label; it maps to the
`response_hours` column named elsewhere in this doc and in §2. (The SLA
Policies page itself needs `RBAC_ENABLED=true` — if it isn't in the sidebar,
that's why.) Requires the `sla_policies` table migration in §2 above to
already be applied, or saving the policy 500s.

**Steps**

1. Send a new email to the Email inbox, first line
   `Test escalation TC-10 — SLA breach`. If `EMAIL_AUTOACK_ENABLED=true`
   (left on from TC-08/09), this new conversation triggers the tenant's
   normal auto-ack reply the moment it's created — an extra email in your
   test mailbox at this step is that, not part of the SLA alert this case
   is testing. This one **is** expected: it is a genuine first-contact
   customer email with no `[CASE-n]` correlation token, so the suppression
   that silences the auto-ack in TC-08/TC-09 correctly does not apply.
2. Apply `dept_sales` only. Do **not** reply to it and do **not** apply
   `escalate`.
3. Wait past the Response SLA threshold, then wait for one SLA scan
   interval (`SLA_SCAN_INTERVAL_MINUTES`, 15 min by default) so the scan runs.

**Expected**

- `yuda.adi.pratama@devoteam.com` receives an email subject
  `[SLA] SLA_BREACH_NO_RESPONSE on case <N>`, with
  `fauzal.atmodirono@devoteam.com` on CC (same PIC/CC pair as TC-01 — the
  SLA alert reuses the `dept_sales` routing record), body naming the age
  and threshold and `Reference: Chatwoot conversation #<N>`.
- Conversation #N gains a private note starting `⚠️ SLA breach
  (SLA_BREACH_NO_RESPONSE) on case <N>.` with the same remark text.
- Trigger a second scan (wait another interval, or ask an operator to
  re-run it) without replying in between: **no** second email, **no**
  second note — the audit trail already has a `SLA_BREACH_NO_RESPONSE`
  entry for this conversation and the scan skips it.

**Pass criteria:** one email received with the right subject/CC, one
private note on #N, no duplicates on the rescan.

> Reset the inbox's Response SLA afterwards — a 3-minute threshold left in
> place will breach every real email on this inbox.

---

## 5. Log verification

Run after each case; every failure mode in this path is fail-open and silent.

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command='echo "=== agent ==="; sudo docker logs --tail 80 proton-agent 2>&1 | grep -i escal; \
             echo "=== backend ==="; sudo docker logs --tail 80 proton-backend 2>&1 | grep -i escal'
```

For TC-10, `grep -i escal` on `proton-backend` will **not** catch the SLA alert's own
log lines — they're prefixed `sla_`, not `escal`. Use:

```bash
sudo docker logs --tail 200 proton-backend 2>&1 | grep -i sla_
```

| Log line | Meaning |
|---|---|
| *(nothing in `proton-agent`)* | webhook did not fire, or the inbox is not Email-channel |
| `escalation_notifier_no_pic_for_dept` | the `dept_` label did not resolve to a PIC |
| `escalation_dealer_unmapped` | the `dealer_` slug has no Firestore record |
| `escalation_email_failed` | SMTP rejected the PIC mail (check the Gmail app password) |
| `escalation_customer_ack_failed` | ack leg only; the PIC mail may still have gone out |
| `escalation_dealer_forward_failed` | SMTP rejected the dealer mail |
| `escalation_replies: sender … is not an escalation contact, skipping` (agent) | TC-08 reply came from an address not in the PIC/dealer allowlist |
| `escalation_replies: contact allowlist unavailable` (agent) | TC-08 backend was unreachable when checking the allowlist — reply left unlinked |
| `sla_breach_recorded` | the scan fired a new breach (TC-10) |
| `sla_alert_email_failed` / `sla_alert_note_failed` | the email/note leg of the SLA alert failed after the breach was recorded |
| `sla_alert_no_pic_for_dept` | TC-10's `dept_sales` label didn't resolve to a PIC — check the routing table |

---

## 6. Results

| Case | Date | Tester | Result | Notes |
|---|---|---|---|---|
| TC-01 Happy path | | | ☐ Pass ☐ Fail | |
| TC-02 Dealer forward | | | ☐ Pass ☐ Fail | |
| TC-03 Label order | | | ☐ Pass ☐ Fail | |
| TC-04 Non-Email channel | | | ☐ Pass ☐ Fail | |
| TC-05 Unmapped dept | | | ☐ Pass ☐ Fail | |
| TC-06 No contact email | | | ☐ Pass ☐ Fail | |
| TC-07 Re-trigger | | | ☐ Pass ☐ Fail | |
| TC-08 Dealer reply links back | | | ☐ Pass ☐ Fail | |
| TC-09 Customer reply rejoins case | | | ☐ Pass ☐ Fail | |
| TC-10 SLA breach reaches PIC | | | ☐ Pass ☐ Fail | |

---

## 7. Out of scope / follow-ups

- **AI-driven escalation** (`EscalationNotifier.notify`) is now also live
  (`ESCALATION_EMAIL_ENABLED=true`). It fires on a bot-classified complaint,
  sends the PIC email + a WhatsApp alert to the PIC's number, and writes
  `case_state=WIP`. It cannot be triggered by applying a label, so it needs its
  own scenario against the WhatsApp/chat path. Note the `sales` PIC has an empty
  `pic_whatsapp`, so the WhatsApp leg has no recipient today.
- **`case_state=WIP`** is written only by the AI path — do **not** expect it to
  change on any case above.
- **Missing PIC records** for `aftersales`, `cs`, `technical`.
- **Sender identity** is still the `devotech29@gmail.com` test relay, not a
  Proton-branded address — worth resolving before any client-facing run.

## 8. Cleanup

Resolve or delete the test conversations, and delete the test contacts if you do
not want them in Contacts. The `dealer_komang_motor` label can stay — it is
required for the dealer leg to be usable in production at all.
