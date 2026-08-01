# Email Channel via Zendesk-Native Email — Design

**Date:** 2026-06-28
**Status:** Approved (brainstorm complete)
**Scope:** Add email as a customer channel, feeding the existing `OrchestratorService`,
using Zendesk's native email-to-ticket as the transport. Lean: reuses the handoff +
CSAT state machine already built for WhatsApp.

## Context & North Star

The POC already serves three channels — web text, voice (Gemini), and WhatsApp (Twilio
sandbox) — all converging on Zendesk as the support hub. Email is the next channel and is
the cheapest to add because **Zendesk is already an email system**: every account has a
built-in `support@<subdomain>.zendesk.com` address, inbound mail auto-creates a ticket,
replies thread onto the *same* ticket, and Zendesk handles all SMTP.

The eventual end goal (separate future project, **not** in this spec) is a cross-channel
**Bot Metrics dashboard** (volume, %transfer-to-agent vs %closed-by-bot, CSAT, NPS,
fallback rate, bounce rate, speed-of-response, per channel). To keep that future project
unblocked, this spec keeps CSAT on the **unified `record_csat` path** so email satisfaction
data lands in the same store as web/WhatsApp. No metrics/dashboard code is built here.

### Decisions locked during brainstorming

- **AI behavior:** auto-reply, gated by the existing detection gate, with email loop guards.
  Draft-assist (AI writes a *private* note, human sends) is a config switch, default off.
- **CSAT:** custom unified 1–5 via `record_csat` (not Zendesk's native survey), so all
  channels share one dataset.
- **Transport:** Approach A — Zendesk-native email (rejected: external provider /
  SendGrid/Mailgun, and IMAP/SMTP polling — both duplicate what Zendesk gives free).
- **Scope:** email channel only; unified metrics layer is a later, separate project.
- **State helpers (§ "State machine"):** add channel-neutral aliases (recommended option a).
- **Ticket status after AI reply:** `pending` ("we answered, waiting on you"); CSAT fires
  only on human **Solve**, not auto-solve per reply.

## Data Flow

```
Customer emails support@<subdomain>.zendesk.com
  └─ Zendesk creates ticket (status: new), requester = customer
       └─ Trigger "AI: customer email" fires → POST /webhooks/zendesk-email
            { ticket_id, text (latest public comment), requester_name?, requester_email? }
              └─ session_id = "email-<ticket_id>"        (ticket == conversation, 1:1)
                   └─ route by conversation state:
                        active          → run AI turn (see Handoff)
                        paused          → no-op (human owns the ticket natively)
                        awaiting_survey → parse CSAT reply
```

**Key simplification vs WhatsApp:** the ticket *is* the conversation. Every customer email
and every AI reply is already a public comment on it, so there is **no separate capture
ticket** and **no message forwarding** (WhatsApp needed both because it lives outside
Zendesk). `session_id = email-<ticket_id>` maps the whole state machine onto the ticket.

## Components & Changes

| File | Change |
|---|---|
| `features/chat/schemas.py` | New `ZendeskEmailWebhookPayload { ticket_id: str, text: str, requester_name: str \| None = None, requester_email: str \| None = None }` |
| `features/chat/router.py` | New `zendesk_email_webhook` handler; route `POST /webhooks/zendesk-email`; secret-verified via `X-Proton-Webhook-Secret` (same pattern as `zendesk_support_webhook`) |
| `features/chat/adapters/zendesk.py` | New `post_public_reply(ticket_id, text, status=None)` — like `append_conversation_comment` but `public: True` |
| `features/chat/ports.py` | Add `post_public_reply` to `ConversationLogPort` |
| `features/chat/service.py` | Channel-neutral aliases (see State machine); `_EMAIL_*` messages; email reply/handoff/CSAT wiring reusing existing helpers |
| `platform/config.py` | `email_draft_assist: bool = False`; reuse `zendesk_support_webhook_secret` (no new secret) |
| Zendesk (external, via API) | One trigger + one webhook target |

The genuinely new code is **one webhook handler, one adapter method, one schema, and the
Zendesk trigger**. Everything else is reuse.

## Behavior

### Inbound customer email (state = active)
1. `handle_turn(session_id="email-<ticket_id>", text=body)`.
2. Detection gate decides:
   - **No handoff:** post AI reply as a **public** comment, set ticket → `pending`
     (waiting on customer). If `email_draft_assist` is on, post a **private** note instead
     and do not change status.
   - **Handoff:** `pause_ai_for_session(session_id)`, post one public
     "connecting you to a specialist" email, leave ticket open for a human.
3. No separate capture step — the comments already live on the ticket.

### While paused (human handling)
- Customer follow-up emails arrive as public comments the human reads natively in Zendesk.
- Our webhook fires but sees `is_ai_paused` → **no-op** (no AI reply, nothing to forward).
- Human replies in Zendesk → emailed to the customer natively; our trigger does **not**
  fire (agent comment, not end-user).

### CSAT (state = awaiting_survey)
- Human clicks **Solve** → existing `/webhooks/zendesk-handback` fires. For `email-*`
  sessions, set state `awaiting_survey` and post the "rate 1–5" survey as a **public** email.
- Customer replies with a number → reopens ticket → trigger fires → handler sees
  `awaiting_survey` → `parse_csat` → `record_csat(channel="email")` (unified path). Then
  thank-you email, resume AI.
- Invalid reply → one nudge, then resume AI and process normally (identical to
  `_handle_whatsapp_survey`).

## State machine (channel-neutral aliases — chosen option a)

Already channel-agnostic and reused as-is: `begin_survey`, `resume_ai`, `record_csat`,
`parse_csat`, `consume_survey_nudge`, `capture_conversation`.

WhatsApp-named but generic — add neutral aliases that both channels call (keeps the verified
WhatsApp flow working, stops email inheriting a misleading name):

| Existing (WhatsApp-named) | New neutral alias |
|---|---|
| `whatsapp_state(session_id)` | `conversation_state(session_id)` |
| `needs_whatsapp_handoff(session_id)` | `needs_handoff(session_id)` |
| `begin_whatsapp_handoff(...)` | `begin_handoff(...)` |

The `whatsapp_*` names remain as thin wrappers delegating to the neutral methods, so no
WhatsApp behavior changes. `forward_whatsapp_to_agent` stays WhatsApp-only (email never
forwards).

## Loop guards (email auto-reply hazard)

Three layers prevent reply loops and runaway auto-responses:
1. **Trigger condition:** fires only when *Comment is public* **AND** *Current user is
   (end user)* — never fires on AI/agent comments (kills self-reply at the source).
2. **Pause gate:** paused sessions short-circuit (human owns the ticket).
3. **Defense-in-depth:** Zendesk's native mail-loop suppression handles out-of-office /
   `no-reply@` bounces; the handler additionally skips requesters whose email matches a
   small no-reply pattern (e.g. `no-?reply`, `mailer-daemon`, `postmaster`).

## Zendesk Trigger & Webhook (external config, via API)

- **Webhook target:** `POST <public-base>/webhooks/zendesk-email`, header
  `X-Proton-Webhook-Secret: <secret>` (auth: api_key, add_position: header — same as the
  existing support/handback webhooks).
- **Trigger "AI: customer email"** — conditions: *Ticket is Created OR Updated*,
  *Comment is present*, *Comment is public*, *Current user is (end user)*. Action: notify
  the webhook target with JSON body:
  ```json
  {
    "ticket_id": "{{ticket.id}}",
    "text": "{{ticket.latest_public_comment}}",
    "requester_name": "{{ticket.requester.name}}",
    "requester_email": "{{ticket.requester.email}}"
  }
  ```
- The existing handback trigger already covers the Solve → CSAT path; it routes `email-*`
  sessions in `zendesk_handback_webhook` (extend the existing `whatsapp-` branch with an
  `email-` branch that posts the survey via `post_public_reply` instead of Twilio).

## Error Handling

- Adapter calls wrapped in try/except + structured logging (matches existing Zendesk
  adapter), returning gracefully so a Zendesk API hiccup never 500s the webhook.
- Missing/blank `text` or `ticket_id` → `{"status": "ignored"}`, HTTP 200 (don't make
  Zendesk retry).
- Invalid secret → 401.

## Testing (co-located, pytest-asyncio)

`features/chat/test_email_channel.py` (+ split files as needed):
- Webhook routes by state: active → AI reply public + status pending; paused → no-op;
  awaiting_survey → CSAT parse.
- Handoff: detection gate trips → `pause_ai_for_session` called, public ack posted, no
  status=solved.
- Loop guard: simulated agent/AI-authored comment (or no-reply requester) → ignored.
- CSAT: valid number → `record_csat(channel="email")`; invalid → one nudge then resume.
- Draft-assist flag on → reply posted as **private** note, status unchanged.
- Secret: wrong/missing `X-Proton-Webhook-Secret` → 401.
- Channel-neutral aliases: `whatsapp_*` wrappers still delegate correctly (no WhatsApp
  regression).

Quality gates: `ruff format`, `ruff check --fix`, `mypy --strict`, `pytest`.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `email_draft_assist` | `false` | Auto-reply (public) vs draft-assist (private note for human to send) |
| `zendesk_support_webhook_secret` | `""` | Reused to verify the email webhook (no new secret) |

## Out of Scope (explicitly)

- Unified metrics event schema, store, and the Bot Metrics dashboard — separate project.
- NPS capture (the dashboard wants it; defer to the metrics project).
- Retrofitting web/WhatsApp to emit standardized metric events.
- Custom email domain / branded sender — POC uses the default Zendesk support address.
- Accuracy/Quality QA metrics (manual, not auto-derived).
