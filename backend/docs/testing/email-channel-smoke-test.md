# Email Channel — Live Smoke Test Plan

> **Goal:** verify the email channel end-to-end against a live Zendesk sandbox.
> The automated suite (167 tests) covers the backend logic but **mocks the Zendesk
> webhook boundary** — so the trigger wiring and the real Solve→CSAT loop can only
> be proven here. Run this *after* all other channel integrations are built.
>
> **Channel summary:** inbound email → Zendesk auto-creates a ticket → a trigger
> POSTs the ticket id to `POST /webhooks/zendesk-email` → AI replies as a PUBLIC
> ticket comment (Zendesk emails it). The ticket *is* the conversation
> (`session_id = email-<ticket_id>`). Handoff pauses the AI; Solve runs the CSAT
> survey over email.

---

## 0. Prerequisites

- [ ] Backend running and reachable on a **public URL** (cloudflared/ngrok tunnel),
      e.g. `https://<something>.trycloudflare.com`. Note: an ephemeral tunnel URL
      **changes on restart** — if it does, re-run provisioning (§1) or PATCH the
      webhook target, or inbound email silently stops.
- [ ] `apps/backend/.env` has:
  - `ZENDESK_SUPPORT_WEBHOOK_SECRET=<a non-empty secret>` (reused for the email webhook auth header `X-Proton-Webhook-Secret`)
  - Zendesk creds: `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`
  - `EMAIL_DRAFT_ASSIST=false` (auto-reply mode for the main run; flip to `true` only for §6)
  - the Firestore + Vertex settings already used by the other channels
- [ ] You know the account's **inbound support address**: `support@<ZENDESK_SUBDOMAIN>.zendesk.com`
      (Zendesk's default; every account has it).
- [ ] Two email addresses you control to play "customer" (so you can send + read replies).
      Use a **normal personal address** — not a `no-reply@`/`do-not-reply@` style one
      (those are intentionally ignored, see §7).
- [ ] Health check passes: `GET <public-url>/` returns `{status, crm_provider, voice_provider, model}`.

---

## 1. Provision the Zendesk webhook + trigger (one-time)

From `apps/backend/`:

```bash
PUBLIC_BASE_URL=https://<your-tunnel>  .venv/bin/python scripts/provision_zendesk_email_webhook.py
```

Expected output (no secret is ever printed):

```
webhook created: id=<id>
trigger created: id=<id>
```

What it creates:
- a **webhook target** → `POST <PUBLIC_BASE_URL>/webhooks/zendesk-email`, header `X-Proton-Webhook-Secret: <secret>`
- a **trigger** "AI: customer email -> webhook" that fires on **create AND update** when
  the comment is **public** and arrives via **Mail** (`current_via_id = 4`); its body sends
  only `{"ticket_id":"{{ticket.id}}"}` (the backend fetches the comment text from Zendesk).

> ⚠️ **Verify `current_via_id = 4` for your account.** It is Zendesk's standard "Mail"
> channel id, but confirm in your account (Admin → Channels, or inspect a real email
> ticket's `via.channel`). If the provisioning call is rejected or the trigger never
> fires on real email, this is the first thing to check.

> ℹ️ The **Solve→CSAT** step reuses the **existing** account-wide "start CSAT on solved"
> handback trigger (the one already wired for WhatsApp), which sends the ticket's
> `external_id` as `session_id`. The backend stamps each email ticket with
> `external_id = email-<ticket_id>` on the first turn, so no separate handback trigger
> is needed. If CSAT never fires on Solve, confirm that handback trigger still exists
> and points at `/webhooks/zendesk-handback`.

---

## 2. Scenario A — Happy path: AI answers a simple question

1. [ ] From your customer address, email **support@<subdomain>.zendesk.com** with a
       subject like `Battery question` and a body the KB can answer, e.g.
       *"What is the battery warranty on the Proton e.MAS 7?"*
2. [ ] Within a few seconds, Zendesk creates a ticket and the trigger fires.

**Expected:**
- [ ] You receive an **email reply** from the AI answering the question.
- [ ] In Zendesk, the ticket has a **public** agent comment (the AI reply); ticket status is **Pending**.
- [ ] The ticket's **External ID** field = `email-<ticket_id>` (Admin/ticket detail).
- [ ] Backend logs show `zendesk_email_received` then a public reply (no `zendesk_email_empty_reply`).

> If you get **no reply**: check the tunnel URL is current, the webhook secret matches,
> and the trigger fired (Zendesk → the ticket's Events shows the webhook notification).

---

## 3. Scenario B — Multi-turn thread (proves create+update)

1. [ ] **Reply to the AI's email** (same thread) with a follow-up, e.g.
       *"And does that cover the 12V battery too?"*

**Expected:**
- [ ] The reply lands as a new comment on the **same** ticket (no new ticket).
- [ ] The AI answers again as a public comment; status stays **Pending**.

> This is the test the old "create-only" trigger would have failed — a follow-up is a
> ticket *update*. If the AI answers the first email but never the follow-up, the trigger
> is mis-scoped to creation only.

---

## 4. Scenario C — Handoff to a human

1. [ ] Reply in the thread asking for a human, e.g.
       *"This isn't working, I want to speak to a human agent."*

**Expected:**
- [ ] You receive the handoff acknowledgement email ("connecting you with a specialist…").
- [ ] In Zendesk the ticket has a private `[Handoff to human agent]` note and is **Open**;
      the AI is now **paused** for this session.
- [ ] Send one more customer email in the thread → the AI does **not** reply (paused no-op);
      the message simply appears on the ticket for the human to read.
2. [ ] As the **Zendesk agent**, post a **public** reply on the ticket.
- [ ] The customer receives it by email (native Zendesk email — not via the AI).

---

## 5. Scenario D — Solve → CSAT survey (the headline loop)

1. [ ] As the agent, set the ticket to **Solved**.

**Expected:**
- [ ] The customer receives the **CSAT survey email**: *"How would you rate your experience?
      Reply with a number from 1 to 5…"*
2. [ ] Reply to that email with **`5`**.

**Expected:**
- [ ] The customer receives the **thank-you** email; the ticket is **re-Solved**.
- [ ] In Zendesk the ticket has a `⭐ Customer satisfaction: 5/5 (via email)` comment and a
      `csat_5` **tag**.
- [ ] CSAT is recorded on the **unified path** (same place WhatsApp/web land), ready for the dashboard.

**Idempotency check (important):**
- [ ] Solving the ticket again, or any later update to the solved ticket, must **not**
      send a second survey email. (Survey fires only on a genuine `paused → solved`
      transition.)

**Invalid-rating sub-case:**
- [ ] In a fresh solved ticket, reply to the survey with `hello` instead of a number →
      you get **one** nudge ("reply with a number 1 to 5").
- [ ] Reply with a non-number again → the AI resumes and treats it as a normal question
      (no infinite survey trap).

---

## 6. Scenario E — Draft-assist mode (optional)

1. [ ] Set `EMAIL_DRAFT_ASSIST=true` in `.env`, restart the backend.
2. [ ] Email a new question as the customer.

**Expected:**
- [ ] The AI's answer is posted as a **private internal note** (`[AI draft] …`) on the
      ticket — **not** emailed to the customer. A human can review and send it.
3. [ ] Set it back to `false` and restart for the rest of testing.

---

## 7. Scenario F — Loop guards / safety

- [ ] Email from a `no-reply@…` (or `do-not-reply@`, `mailer-daemon@`, `postmaster@`)
      address → the AI **ignores** it (no reply). Backend log: `zendesk_email_webhook_no_reply_skipped`.
- [ ] Confirm the AI never replies to **its own** public comment and never re-triggers on
      an agent's reply (the trigger is scoped to end-user Mail comments; the AI's replies
      go via API, not via_id 4). I.e. no reply ping-pong on any ticket.
- [ ] (If you can) send an email whose body contains a `"` quote and a newline →
      the AI still replies correctly (the trigger sends only the ticket id; the backend
      fetches the comment, so odd characters can't corrupt the payload).

---

## 8. Teardown (when done testing)

- [ ] To remove the email wiring: delete the **inbound email trigger** and its **webhook
      target** by the IDs printed in §1 (Zendesk Admin → Webhooks / Triggers, or via API).
- [ ] Leave the shared "start CSAT on solved" handback trigger in place — it's also used
      by WhatsApp.
- [ ] Close out any test tickets.

---

## Quick troubleshooting

| Symptom | Likely cause |
|---|---|
| No AI reply to first email | tunnel URL stale, wrong webhook secret, trigger didn't fire (check ticket Events), or `current_via_id` ≠ 4 |
| First email answered, follow-ups ignored | inbound trigger scoped to *create* only (should be create **and** update) |
| Handoff never pauses | detection gate didn't classify it as a human request — try a clearer "I want a human agent" |
| No CSAT survey on Solve | ticket has no `external_id = email-<id>` (first turn didn't run / failed), or the handback trigger is missing/misrouted |
| Duplicate survey emails | handback firing on non-paused state — should be gated to `paused → solved` only |
| Customer reply `5` not recorded | the rating reply is a ticket *update*; confirm the inbound trigger fires on updates and the session is `awaiting_survey` |

---

*Backend code: `apps/backend/src/chatbot/features/chat/{router,service}.py`,
`adapters/zendesk.py`, `scripts/provision_zendesk_email_webhook.py`.
Design: `docs/superpowers/specs/2026-06-28-email-channel-zendesk-native-design.md`.*
