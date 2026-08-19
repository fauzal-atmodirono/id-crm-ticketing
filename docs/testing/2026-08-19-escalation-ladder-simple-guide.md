# How to test the escalation ladder — plain guide

**What the ladder does.** When a case is escalated to a dealer and nobody
answers, the CRM chases them by itself: first a reminder to the Dealer
Principal, then a stronger one to the Dealer Owner, then a note telling an
agent to pick up the phone. It stops the moment the dealer replies.

**What you need.** The CRM, and two Gmail inboxes you can read:
`devotech29@gmail.com` (the CRM's own mailbox) and
`yudaadipratama2209@gmail.com` (playing the dealer).

Everything below is clicking and email. No terminal.

---

## Who is who

| Role in the test | Address | Where the mail lands |
|---|---|---|
| The customer | your Devoteam email | your Devoteam inbox |
| The CRM's mailbox | `devotech29@gmail.com` | — |
| After Sales PIC | `jacipsbusiness@gmail.com` | that inbox |
| Dealer CRE | `yudaadipratama2209@gmail.com` | that inbox |
| Dealer Principal | `yudaadipratama2209+dp@gmail.com` | **same** inbox |
| Dealer Owner | `yudaadipratama2209+owner@gmail.com` | **same** inbox |

The `+dp` and `+owner` addresses are the same mailbox wearing different hats,
so you can watch four "people" from one inbox. Look at the **To:** line of
each email to see which one it was meant for.

---

## Before you start: make the clock fast

The real ladder waits 2, 4 and 8 hours. You don't want to sit there all day,
so shorten the timers, run the test, then put them back at the end.

1. CRM → left menu → **Escalation Routing**
2. Scroll to the box at the top: **Escalation ladder**
3. Type these numbers over what is there:

| Box | Type this | Which means |
|---|---|---|
| Step 2 — acknowledgement due | `0.05` | 3 minutes |
| Step 3 — 1st reminder | `0.1` | 6 minutes |
| Step 4 — 2nd reminder | `0.15` | 9 minutes |
| Step 5 — telephone | `0.15` | 9 minutes |

4. **Enabled** ticked. **Dry run** NOT ticked.
5. Click **Save ladder settings**

You should see: *"In force now: enabled, sending for real, every 60s"*.

> **Dry run** means "write in the log what you would have sent, but send
> nothing". Useful for a rehearsal. For this test we want real emails, so
> leave it unticked.

---

## Step 1 — Pretend to be a customer

6. From **your Devoteam email**, send a normal email to
   **`devotech29@gmail.com`**.

   Something like:

   > **Subject:** Home charger not working — VAB 3271
   >
   > Hi, I bought an e.MAS 7 last month, plate VAB 3271. The home charger has
   > stopped charging the car. Please help.

7. **Wait about 2 minutes.** The CRM only checks for new mail every couple of
   minutes.

## Step 2 — Find it in the CRM

8. Go to **Conversations**. Your email is there as a new conversation.
   Open it.

## Step 3 — Escalate it

9. On the right-hand side find **Labels**, and add these **three**:

   - `dept_aftersales`
   - `dealer_petaling_jaya`
   - `escalate`

**Within a few seconds three emails go out:**

| Inbox | What arrives |
|---|---|
| your Devoteam mail | *"Update on your case (#…)"* — inside your original email thread |
| `jacipsbusiness@gmail.com` | *"[Escalation] [CASE-…] …"* |
| `yudaadipratama2209@gmail.com` | *"[Escalation] [CASE-…] …"* |

If those three arrive, the escalation worked.

## Step 4 — Now do nothing, and watch

10. Go back to **Escalation Routing** and look at the **In flight** table
    under the settings box. Your case is listed with the rung it is on and
    when the next one is due. **Refresh the page** to watch it move.

If you touch nothing:

| About | What happens |
|---|---|
| 3 minutes | the dealer's "please reply by now" window closes — **no email** |
| 6 minutes | **1st reminder** → `yudaadipratama2209+dp@gmail.com` (Dealer Principal) |
| 9 minutes | **2nd reminder** → `yudaadipratama2209+owner@gmail.com` (Dealer Owner) |
| a minute later | **no email** — a note appears inside the case: *"☎️ FINAL ESCALATION — TELEPHONE REQUIRED"* |

Notice each reminder goes to a **different person**, and the last step is a
phone call, not an email. That is the point of the whole thing.

## Step 5 — Make it stop (the most important part)

Do this on a **second** case, so you see both endings.

11. Repeat steps 6–9 with a new email.
12. Wait for the **1st reminder** to arrive.
13. **Hit Reply** on that email and write anything, e.g. *"We are checking it
    now."*

    **Do not change the subject line.** The `[CASE-…]` part is how the CRM
    knows which case your reply belongs to.

14. Open the case in the CRM. You will see:

    - a **private note** with what the dealer wrote;
    - a second note: *"Suggested customer reply (draft — review before
      sending)"*;
    - in the In flight table, the case now says **dealer replied — halted**.

15. **The 2nd reminder never comes.** The ladder stopped because the dealer
    answered.

16. The customer has been told **nothing** — on purpose. A dealer's reply is
    internal, and a person decides what the customer hears. Copy the draft
    into the reply box, edit it, and send it.

## Step 6 — Put the clock back

17. Escalation Routing → **Escalation ladder** → type the real numbers back:

    `2` , `4` , `8` , `8`

18. Click **Save ladder settings**.

Done.

---

## Extra test: the out-of-office trap

Worth doing once. An "I'm on leave" auto-reply must **not** count as the
dealer answering.

1. Escalate a third case.
2. Reply from `yudaadipratama2209@gmail.com` with the subject
   **`Automatic reply: out of office`**.
3. In the CRM you should see a note saying *"…not counted as a response; the
   escalation clock is still running"*, and the ladder **keeps going** to the
   next reminder.

---

## If something doesn't happen

| What you see | What it means |
|---|---|
| No emails at all after adding labels | A label is missing or misspelt. All **three** are needed. |
| In flight says **"no dealer — not climbed"** | The `dealer_petaling_jaya` label is missing. Without a dealer there is nobody to chase. |
| The case isn't in In flight at all | The `escalate` label is what starts everything. |
| A reminder is a minute late | Normal. The CRM checks once a minute. |
| Your reply didn't link to the case | The subject line was changed, or you replied from an address that isn't in Escalation Routing. |

---

## Two things to know

**"Working hours" currently means ordinary hours.** The boxes say working
hours, but on this tenant that setting is off, so 8 hours means 8 hours on the
clock — including overnight. Turning it on properly needs two things: the
Email inbox's Business Hours set to `Asia/Kuala_Lumpur` with real opening
times (it is currently set to `America/Los_Angeles`), and then the
working-hours switch enabled. Do the timezone first.

**Changing the timers takes effect on the next check** (within a minute).
The only field that needs a restart is the "check every N seconds" one.
