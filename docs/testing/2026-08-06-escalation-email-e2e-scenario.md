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
| `EMAIL_ESCALATION_ENABLED` | agent | `true` |
| `EMAIL_ESCALATION_ACK_ENABLED` | backend | `true` |
| `ESCALATION_EMAIL_ENABLED` | backend | `true` |
| `ESCALATION_CC_PIC` | backend | unset → default `true` (CC list is sent) |
| `EMAIL_ESCALATION_ACK_TEMPLATE` | backend | unset → default: *"Your case has been escalated to a specialist team who will follow up shortly."* |
| `PROTON_BACKEND_URL` | agent | `http://proton-backend:8080` |
| SMTP | both | Gmail relay, sender `Support <devotech29@gmail.com>` |

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

**Steps**

1. On the TC-01 conversation, remove the `escalate` label, then re-apply it.

**Expected**

- The ack and PIC mails are sent **again** — this path has no
  once-per-conversation guard.
- `dealer_escalated_at`, if already stamped, is **not** overwritten (that stamp
  is idempotent).

**Pass criteria:** second pair of mails received; existing timestamp unchanged.

Worth knowing before a live demo: toggling the label in front of a client sends
another round of real email.

---

## 5. Log verification

Run after each case; every failure mode in this path is fail-open and silent.

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command='echo "=== agent ==="; sudo docker logs --tail 80 proton-agent 2>&1 | grep -i escal; \
             echo "=== backend ==="; sudo docker logs --tail 80 proton-backend 2>&1 | grep -i escal'
```

| Log line | Meaning |
|---|---|
| *(nothing in `proton-agent`)* | webhook did not fire, or the inbox is not Email-channel |
| `escalation_notifier_no_pic_for_dept` | the `dept_` label did not resolve to a PIC |
| `escalation_dealer_unmapped` | the `dealer_` slug has no Firestore record |
| `escalation_email_failed` | SMTP rejected the PIC mail (check the Gmail app password) |
| `escalation_customer_ack_failed` | ack leg only; the PIC mail may still have gone out |
| `escalation_dealer_forward_failed` | SMTP rejected the dealer mail |

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
