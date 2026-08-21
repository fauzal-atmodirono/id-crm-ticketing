# AEON360 — WhatsApp Scenario — Testing Guide

How to test the AEON360 assistant on WhatsApp, step by step. You need a phone and
about 20 minutes. No technical knowledge required.

## What you are testing

A customer messages AEON360 on WhatsApp. An AI assistant answers. Everything the
customer says and everything the AI answers also appears in the CRM, where a
human agent can read along and take over at any moment.

So there are two things to check: **the customer gets good answers**, and **an
agent can see and take over the conversation**.

## Before you start

| | |
|---|---|
| **WhatsApp number** | `+1 682 399 3949` — save it to your phone as "AEON360 Test" |
| **CRM** | https://aeon360.crm.34-50-103-151.nip.io — ask Yuda for a login |
| **How long a reply takes** | Up to **90 seconds**. Be patient. |

> **The most important rule: after you send a WhatsApp message, do not click
> anything in the CRM until the AI has answered.** Clicking around while the AI
> is thinking can cancel its answer, and you will think it is broken when it is
> not.

---

## Part 1 — Your first conversation (5 minutes)

**Step 1.** Open WhatsApp on your phone and start a chat with `+1 682 399 3949`.

**Step 2.** Send this message:

> `mau beli mamypoko`

**Step 3.** Wait — up to 90 seconds. Do not send anything else, and do not open
the CRM yet.

**Step 4.** You should get a reply that **mentions something you actually bought
before** — a specific product, and roughly when you last bought it. For example:

> *"Untuk lampin MamyPoko, anda pernah beli MamyPoko Extra Dry Tape saiz XL
> (40 keping) sekali pada bulan Mac lepas. Adakah…"*

✅ **Pass** if the reply names a real product and a real time period.
❌ **Fail** if it is generic ("we sell many diapers"), or nothing arrives in 90
seconds.

**Step 5.** Reply naturally — `yes`, or `berapa harga?`. It should continue
sensibly and remember what you were talking about.

---

## Part 2 — Check it appears in the CRM (5 minutes)

**Step 6.** Open https://aeon360.crm.34-50-103-151.nip.io and log in.

**Step 7.** Click **Conversations** in the left sidebar. Find the conversation
from your phone number and open it.

**Step 8.** You should see the whole conversation:

- Your messages on one side
- The AI's answers on the other, labelled with the bot's name
- In the same order they happened on your phone

✅ **Pass** if everything matches your phone, in the right order.
❌ **Fail** if answers are missing, out of order, or shown as sent by the wrong
person.

**Step 9.** Look at the status at the top of the conversation. It should say
**Pending** — that means "the AI is handling this one".

---

## Part 3 — Take over as a human agent (5 minutes)

This is the most important test. It proves an agent can rescue a conversation
when the AI gets something wrong.

**Step 10.** From your phone, send a new question, for example:

> `saya nak tanya pasal harga`

**Step 11.** **Immediately** — within a second or two, while the AI is still
thinking — go to the CRM, type a reply in that conversation and send it:

> `Hi, saya Yuda, saya boleh tolong.`

**Step 12.** Check three things:

| Check | Expected |
|---|---|
| On your phone | You receive the agent's message |
| On your phone | **No AI answer arrives afterwards** |
| In the CRM | Status changed from **Pending** to **Open** |

✅ **Pass** if no AI message appears after the human's.
❌ **Fail** if the AI also answers — the customer would get two different
replies, which is exactly what this must prevent.

**Step 13.** Send another message from your phone. The AI should stay **silent**
— a human owns this conversation now. Only the agent should reply.

---

## Part 4 — Hand it back to the AI (2 minutes)

**Step 14.** In the CRM, change the status from **Open** back to **Pending**.

**Step 15.** Send a new message from your phone.

✅ **Pass** if the AI starts answering again.

---

## Part 5 — Things that should NOT stop the AI (5 minutes)

Some actions look like they should silence the AI but must not.

**Step 16 — Private note.** Send a message from your phone. While the AI is
thinking, add a **private note** in the CRM (the tab next to "Reply" — notes are
internal and the customer never sees them).

✅ **Pass** if the AI still answers the customer normally. Notes are for your
team, so they must not interrupt anything.

**Step 17 — Assigning.** Assign the conversation to yourself while it is still
**Pending**.

✅ **Pass** if the AI keeps answering. Only the **status** decides who is in
charge — not who it is assigned to.

---

## Part 6 — Asking for a human (2 minutes)

**Step 18.** From your phone, send:

> `saya nak cakap dengan manusia`

**Step 19.** Check:

| Check | Expected |
|---|---|
| On your phone | A short message saying a colleague will help |
| In the CRM | Status changes to **Open** |
| Afterwards | The AI stops answering |

✅ **Pass** if all three happen. The conversation is now waiting for a human.

---

## Part 7 — Does it admit when it does not know? (5 minutes)

An assistant that invents answers is worse than one that says "I don't know".

**Step 20.** Ask about something AEON360 does not sell, or a made-up brand:

> `ada jual Brand Z formula?`

✅ **Pass** if it honestly says it does not have that information.
❌ **Fail** if it invents a product, a price, or a description.

**Step 21.** Ask something completely unrelated:

> `cuaca hari ini macam mana?`

✅ **Pass** if it politely steers back to what it can help with.

---

## Testing as a specific customer (optional)

Normally the assistant works out who you are from your phone number. To test as a
**different** customer, you need a special link. Ask Yuda to run the link
generator and send you one — each link opens WhatsApp with a message already
typed, and identifies you as that customer when you send it.

Six test customers are set up, each checking something different:

| Test customer | What it is for |
|---|---|
| Heavy baby-needs buyer | Buys nappies every 10 days and is overdue — should name the product **and** the timing |
| Customer with a huge history | 70 items due. Does it pick **one** sensible thing, or overwhelm you with a list? |
| Cold drinks buyer | A simple, clear restock case |
| Fresh vegetables buyer | Short shelf life — it should not suggest buying in bulk |
| Unusual account number | Checks a different customer-number format still works |
| **Customer with nothing due** | Nothing needs restocking for 118 days. It must **not** invent one. The most valuable test here. |

> ⚠️ These links work like a password — each one lets whoever holds it act as
> that customer for 3 days. Do not forward them, post them in a group chat, or
> put them in a shared document.

---

## If something goes wrong

**Nothing arrives after 90 seconds.**
Wait the full 90 seconds first — replies genuinely take that long sometimes. Then
check the status in the CRM. If it says **Open** or **Resolved**, the AI is not
supposed to answer; set it back to **Pending**. If it says **Pending** and there
is still nothing, report it.

**The answer arrived but was generic.**
It did not recognise you. Note which phone number you used and report it.

**The AI stopped answering and never came back.**
Set the conversation to **Open**, then back to **Pending**. That usually wakes it
up. If not, report it.

**The AI answered *after* an agent had already replied.**
The most serious failure — report it immediately with the time. The customer got
two conflicting answers.

**Nothing appears in the CRM at all, even though WhatsApp works.**
Report it — messages are reaching the phone but not the CRM.

**When reporting anything**, include the time, the phone number you used, what
you sent, and what you got back. A screenshot is ideal.

---

## Results checklist

Copy this and tick as you go.

| # | Test | Pass | Notes |
|---|---|---|---|
| 1 | First message gets a personalised reply | ☐ | |
| 2 | Follow-up keeps the context | ☐ | |
| 3 | Whole conversation visible in the CRM, right order | ☐ | |
| 4 | Status shows **Pending** while the AI is handling it | ☐ | |
| 5 | Agent reply mid-answer stops the AI | ☐ | |
| 6 | AI stays silent while status is **Open** | ☐ | |
| 7 | Setting back to **Pending** wakes the AI | ☐ | |
| 8 | Private note does **not** interrupt | ☐ | |
| 9 | Assigning does **not** interrupt | ☐ | |
| 10 | "Cakap dengan manusia" hands over properly | ☐ | |
| 11 | Admits when it does not know | ☐ | |
| 12 | Handles off-topic questions gracefully | ☐ | |

---

## For engineers

The technical side — architecture, log queries, the signed-delivery probe, HMAC
and duplicate-delivery checks, and the full 2026-08-21 cutover diagnosis — is in
[`aeon360-whatsapp-cutover.md`](./aeon360-whatsapp-cutover.md), especially
§3.1–§3.2c. Automated coverage lives in the WABA repo (`uv run pytest`), and
`deploy/scripts/aeon360-mint-entry-links.sh` generates the test-customer links.

> For anyone holding the older prototype guide
> (`apac-aeon360-foundry-prototype/docs/whatsapp/whatsapp-scenario-testing.md`):
> its 14 persona links no longer work on this number. The Twilio Sender was
> repointed at the CRM on 2026-08-21, and the old `[sarah]`-style tags now
> identify nobody — they are read as ordinary text.
