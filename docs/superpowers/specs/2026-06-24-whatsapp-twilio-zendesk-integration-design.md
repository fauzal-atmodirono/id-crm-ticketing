# Multi-Channel (WhatsApp + Phone + Email) → AI → Zendesk Integration — Design

- **Date:** 2026-06-24 (updated 2026-06-25: added Email channel; phone → ConversationRelay)
- **Status:** Approved (design); ready for implementation planning
- **Demo milestone:** present to Proton by **2026-07-07** (drives the
  ConversationRelay choice for phone and a sandbox-first WhatsApp demo — see §6, §10)
- **Author:** AI pairing session
- **Related:** ADR [0001](../../decisions/0001-python-fastapi-gemini-adk-stack.md), ADR [0002](../../decisions/0002-monorepo-vue-frontend-gemini-audio.md)

## 1. Goal & Context

The demo was well received. The customer now wants to reach the existing AI
assistant through three real-world channels and have everything land in Zendesk:

- **WhatsApp Business** — inbound via Twilio webhook.
- **Phone calls** — inbound via Twilio (a Twilio **trial number** is already
  provisioned).
- **Email** — inbound via the Zendesk-native support address (Zendesk owns the
  inbox; the AI replies on top of the ticket it creates).
- **One number** serving both WhatsApp and phone.
- **AI integration** — all channels are answered by the existing
  `OrchestratorService` (Gemini/ADK) brain. No change to the agent itself.
- **Zendesk integration** — every conversation is captured in Zendesk with the AI
  as first responder, but a **detection gate** decides which conversations become
  **open, actionable tickets** versus **closed log records**. Human agents take
  over open tickets on escalation.

### Decisions locked during brainstorming (2026-06-24 / 2026-06-25)

| Decision | Choice | Rationale |
|---|---|---|
| Phone voice path | **Twilio ConversationRelay** | Twilio manages streaming STT+TTS; our backend exchanges **text** over a WebSocket and reuses `handle_turn`. Chosen for **speed to the 2026-07-07 demo**. Raw Media Streams + Google STT/TTS is the cheaper-per-minute future optimization (see §8). |
| WhatsApp transport | **Twilio WhatsApp Sender** | One vendor, one webhook style, and the **same Twilio number** can serve voice — satisfies the single-number goal. |
| Email ingestion | **Zendesk-native support address + trigger/webhook** | Reuses Zendesk's mature email pipeline (threading, spam, attachments); least new plumbing; fits "everything in Zendesk." |
| Email reply mode | **Hybrid by detection** | AI auto-sends + solves simple/high-confidence emails; drafts an internal note and leaves the ticket open for agent approval when sensitive/low-confidence/flagged — safer on a permanent, public channel. |
| Phasing | **Phase A WhatsApp → Phase B Email → Phase C Phone** | Ship the simplest text channel first; Email is low-complexity (Zendesk-native); Phone (realtime audio) is the most complex, last. |
| Zendesk relationship | **All conversations land in Zendesk; detection decides ticket vs. log** (AI = first responder) | Full audit trail with a clean active queue — actionable conversations become open tickets, the rest become closed log records. |
| Zendesk mechanism | **Zendesk Support tickets**, created **lazily** (one per conversation) | Simplest; reuses the existing `ZendeskAdapter`. (Sunshine-native was the considered alternative — see §8.) |
| Ticket-worthiness gate | **Hybrid: AI tool + safety-net rules** | Agent calls a tool when it judges the conversation actionable; deterministic fallbacks (handoff, strongly negative sentiment) catch what the model misses. |
| Non-actionable conversations | **Closed/solved log record in Zendesk** | Complete audit trail without polluting the agents' open queue. |

## 2. Guiding Principles

- **Additive only.** Each channel (WhatsApp, Email, Phone) is a *new inbound
  source* feeding the *existing* orchestrator. The agent (`agents.py`), prompts,
  and core `handle_turn` flow do not change. New code = channel adapters, new
  webhook routes, new config, and a thin conversation-logging seam on the Zendesk
  adapter.
- **Channels are interchangeable at the seam.** Every channel maps an inbound
  message to a `session_id` and calls `handle_turn`; outbound is a per-channel
  `ChatPort` (Twilio REST for WhatsApp, Zendesk API for email). The detection gate
  and Zendesk capture are channel-agnostic.
- **Respect the ports architecture.** Outbound messaging is a `ChatPort`
  implementation; Zendesk stays behind `TicketingPort`. Dependencies point
  inward (`.agents/rules/architectural-pattern.md`).
- **Verify every inbound request.** Twilio webhooks, the media-stream socket, and
  the Zendesk email trigger/webhook are attacker-reachable
  (`rules/security-mandate.md`, `rules/rugged-software-constitution.md`).
- **Reuse the existing handoff machinery.** `_escalate_handoff`, AI pause/unpause,
  and the SSE/agent-reply paths already work — every channel plugs into them.

## 3. High-Level Architecture

```
                         ┌───────────────────────────────────────────────┐
 WhatsApp ──▶ Twilio ──▶ │ POST /webhooks/twilio-whatsapp                 │
 (Sender)   (WABA)       │   verify signature → session_id=whatsapp-{E164}│
                         │   → orchestrator.handle_turn(session_id, text) │
 Phone   ──▶ Twilio ──▶  │ POST /webhooks/twilio-voice (TwiML            │
 (call)    (Conversation │   <ConversationRelay>) → WSS exchanges TEXT;  │
            Relay:        │   Twilio does STT+TTS → handle_turn (Phase C) │
            STT+TTS)     │                                               │
 Email  ──▶ Zendesk ──▶  │ POST /webhooks/zendesk-email  (Zendesk        │
 (support  (creates      │   trigger) → session_id=email-{ticketId}      │
  address)  ticket)      │   → handle_turn; reply via Zendesk API        │
                         └───────────────┬───────────────────────────────┘
                                         │
                  ┌──────────────────────┼───────────────────────────────┐
                  │                       │                               │
            reply OUT via per-     transcript ALWAYS in            on handoff:
          channel ChatPort         session store; detection        pause AI + assign
        (Twilio REST / Zendesk     gate → Zendesk:                 to human (open
         API for email)              • actionable → OPEN ticket    ticket already
                                      (+ full transcript)          exists)
                                     • else → CLOSED log record
                                       (at conversation end)
```

**Session ID conventions** (consistent with existing `chatwoot-conv-*` /
`zendesk-conv-*`):

- WhatsApp: `whatsapp-{E164}` (e.g. `whatsapp-+60123456789`).
- Phone: `phone-{CallSid}`.
- Email: `email-{ticketId}` (the Zendesk ticket id — for email the ticket exists
  *first*, so it is the natural key).

The `session_id` is the single key tying together: ADK session state (chat
history), the Zendesk conversation ticket, and the handoff/pause state.

## 4. Phase A — WhatsApp (ship first)

### 4.1 Inbound webhook

- **Route:** `POST /webhooks/twilio-whatsapp` (registered in
  `features/chat/router.py`, alongside the existing `/webhooks/*`).
- **Body:** Twilio sends `application/x-www-form-urlencoded`. Relevant fields:
  `From` (`whatsapp:+E164`), `To`, `Body`, `ProfileName`, `MessageSid`,
  `NumMedia`, `MediaUrl{N}`, `MediaContentType{N}`.
- **Signature verification (mandatory):** validate `X-Twilio-Signature`
  (HMAC-SHA1 over the full request URL + sorted POST params, keyed by
  `TWILIO_AUTH_TOKEN`). Reject with `403` on mismatch. Implemented as a pure,
  unit-testable helper.
- **Mapping:** `From` → `session_id = whatsapp-{E164}` (strip the `whatsapp:`
  prefix; keep E.164).
- **Dispatch:** call `orchestrator.handle_turn(session_id, Body)`.

### 4.2 Outbound reply (async via REST, not TwiML)

AI generation + KB search can exceed Twilio's synchronous webhook timeout
(~10–15 s), and handoff/agent replies arrive *later* (asynchronously). Therefore:

- The webhook returns **HTTP 200 immediately** (empty / minimal TwiML).
- Replies are sent via the **Twilio REST API**
  (`POST /2010-04-01/Accounts/{SID}/Messages.json`).
- New `adapters/twilio.py` → **`TwilioChannelAdapter`** implements **`ChatPort`**:
  - `send_message(conversation_id, text)` where `conversation_id` is the
    `whatsapp:+E164` destination. Uses `httpx` with basic auth
    (`account_sid:auth_token`), runs non-blocking on the async loop.
- This is the *same* `ChatPort` seam the existing handoff/agent-reply path uses,
  so agent replies during a live handoff flow back to WhatsApp for free.

### 4.3 Zendesk capture with a detection gate

Every conversation is captured in Zendesk, but **detection decides the shape** of
that record. The full transcript is **always** tracked in the existing ADK /
Firestore session store throughout the conversation; the Zendesk write is
**lazy** (no empty stub tickets).

**The ticket-worthiness gate (hybrid):** a conversation becomes an *open,
actionable* ticket when **any** of these fire —

1. **AI tool (primary):** a new agent tool, `flag_for_ticket_tool(reason,
   category?)`, which the agent calls when it judges the conversation actionable
   (complaint, service/warranty request, sales lead, unresolved issue, explicit
   request for a human). Reuses the existing tool-as-state-flag pattern (mirrors
   `emit_handoff_tool` / `classify_ticket_tool`).
2. **Safety-net rule — handoff:** `emit_handoff_tool` / `_escalate_handoff` firing
   always implies a ticket.
3. **Safety-net rule — sentiment:** strongly negative sentiment (existing
   sentiment signal past a threshold).

**Outcomes:**

- **Gate fires → open ticket.** Create (or find) the ticket keyed by
  `external_id = session_id`, **back-fill the entire transcript so far** plus
  customer name/phone, `classify_ticket_tool` output (category / subcategory /
  priority / SLA), and sentiment. Subsequent turns are appended as public
  comments. On escalation the ticket **already exists**, so `_escalate_handoff`
  just **pauses the AI + assigns to a human + adds the AI summary as a private
  note** — no duplicate ticket.
- **Gate never fires → closed log record.** At **conversation end** the full
  transcript is written as a **single solved/closed ticket** (audit only). The
  AI's `classify_ticket_tool` output is included for analytics. This keeps the
  agents' open queue free of FAQ/smalltalk noise (they filter on open status).

**Conversation-end detection (for the closed-log flush):**

- **Phone (Phase C):** the Twilio media-stream `stop` event is a clean end signal.
- **Email:** each inbound email is a discrete turn; flush the closed log when the
  ticket is solved (or after an inactivity window) — see §5.
- **WhatsApp:** no explicit end — use an **idle timeout** (configurable, e.g.
  30 min of no new message). Mechanism options to settle at implementation time:
  (a) a lightweight background sweep of idle sessions; (b) lazy flush evaluated on
  the next inbound webhook for any session; (c) a scheduled job. Default
  recommendation: **(a) background sweep**, with **(b)** as a cheap backstop.

**New additive methods** on `ZendeskAdapter` (existing `create_ticket`,
`add_private_note`, pause/unpause, and Sunshine bridge paths are **reused, not
modified**):

- `ensure_conversation_ticket(session_id, customer_name, customer_phone,
  classification, status="open")` → find-or-create, returns `ticket_id`.
- `append_conversation_comment(ticket_id, author, text, public=True)`.
- `write_closed_log(session_id, transcript, classification, customer_*)` → create
  a solved/closed ticket in one shot (used by the end-of-conversation flush).

> Note: this intentionally extends the Zendesk integration. The prior
> "Zendesk off-limits" constraint applied only to the KB-scraper/carousel work;
> this feature *is* the Zendesk integration work, kept additive to avoid
> regressing existing escalation behavior.

### 4.4 WhatsApp media (optional within Phase A)

Inbound voice notes / images (`NumMedia > 0`) can be downloaded from `MediaUrl{N}`
(auth required) and, for audio, routed through the existing `handle_voice_turn`
(Gemini transcription + agent + GCP TTS). **Deferred** unless the customer needs
it for the WhatsApp launch — text is the Phase A priority.

### 4.5 WhatsApp platform constraints (operational)

- **24-hour customer-service window:** free-form replies are only allowed within
  24 h of the last customer message. The AI only ever *replies* to an active
  conversation, so this is satisfied. Business-*initiated* outreach (outside the
  window) would require pre-approved **message templates** — out of scope here.
- **Trial limits:** the Twilio trial number / WhatsApp **sandbox** can only
  message verified destinations and prefixes messages with sandbox text.
  Production requires **WABA / Meta business verification** via Twilio.

## 5. Phase B — Email (Zendesk-native)

Lower complexity than Twilio: **Zendesk owns the inbox**, so there is no inbound
mail plumbing, no SMTP, and no signature scheme of our own — Zendesk handles
receipt, threading, spam, and attachments. The AI acts as first responder *on top
of* the ticket Zendesk creates.

### 5.1 Inbound (Zendesk → AI)

- Customer emails the Zendesk **support address**; Zendesk **creates the ticket**
  and threads subsequent replies.
- A Zendesk **trigger** (or webhook) fires on new/updated end-user email comments
  and calls a new backend route: `POST /webhooks/zendesk-email`.
  - Authenticated via a shared secret header (e.g. `x-proton-webhook-secret`,
    same pattern as the existing `/webhooks/zendesk-support` route).
  - Payload provides the **ticket id**, requester (name/email), and the latest
    email body. The trigger is scoped to fire **only** on end-user comments where
    the AI is not paused (skip agent replies and handed-off tickets), to avoid
    loops.
- **Mapping:** `session_id = email-{ticketId}` → `orchestrator.handle_turn(...)`
  with the email body as the turn text.

### 5.2 Outbound reply (Zendesk API) — hybrid mode

A new **`ZendeskEmailChannelAdapter`** implements **`ChatPort.send_message`** by
posting a comment to the ticket via the Zendesk Support API (`PUT
/api/v2/tickets/{id}.json`). The **detection gate (§4.3) drives the reply mode**:

- **Simple / high-confidence (gate did NOT flag, positive/neutral sentiment):**
  post a **public** comment (the AI's reply is emailed to the customer by Zendesk)
  and **set status to solved** — AI auto-resolves.
- **Sensitive / low-confidence / gate flagged / handoff / negative sentiment:**
  post the AI's suggested reply as a **private internal note** (a draft for the
  agent), keep the ticket **open**, and assign per the handoff path. A human
  reviews, edits, and sends.

"Confidence" signals reuse what already exists: whether `flag_for_ticket_tool`
fired, the sentiment value, and whether `search_kb` returned `NO_MATCHES` (an
empty-handed answer is treated as low-confidence → draft, don't auto-send).

### 5.3 Email ↔ Zendesk capture

The detection-gate model of §4.3 still applies, but **inverted**: for email the
**ticket already exists** (Zendesk created it on receipt), so the gate decides
*status* rather than *creation*:

- **Gate fires / draft mode →** ticket stays **open**, classified, and (on
  handoff) assigned to a human; AI summary added as a private note.
- **Gate does not fire / auto-send mode →** AI posts the public reply and **solves**
  the ticket. The "closed log record" of §4.3 is simply this solved ticket — no
  separate write needed, since the email ticket is the record.

`classify_ticket_tool` output (category / priority / SLA) is written to the
ticket either way, for routing and analytics.

### 5.4 Email specifics

- **Loop prevention:** never let an AI/agent comment re-trigger the webhook — scope
  the Zendesk trigger to end-user comments only, and honor the AI-paused flag.
- **Threading & quoting:** Zendesk preserves the email thread; the AI reply text is
  what gets emailed — keep replies clean (no need to re-quote).
- **Attachments:** inbound attachments live on the Zendesk ticket; out of scope for
  the AI to parse in this POC (text body only).
- **No Twilio:** email needs none of the Twilio config; it only needs Zendesk API
  credentials, which already exist.

## 6. Phase C — Phone (Twilio ConversationRelay)

Built after Phases A and B. **ConversationRelay** is chosen over raw Media Streams
to hit the **2026-07-07 demo**: Twilio manages streaming **speech-to-text and
text-to-speech**, so our backend never touches raw audio — it exchanges **text**
over a WebSocket. This lets the phone channel reuse the existing turn-based
`handle_turn` almost directly, the same way WhatsApp/Email do.

### 6.1 Call setup

- **Route:** `POST /webhooks/twilio-voice` returns TwiML with a
  **`<Connect><ConversationRelay url="wss://{TWILIO_WEBHOOK_BASE_URL}/conversation-relay" .../>`**
  element (configure TTS voice/provider and the transcription language, e.g.
  `en-US` / `ms-MY` — **confirm voice + language support**).
- Twilio handles the audio leg and opens a **WebSocket** to our endpoint.

### 6.2 Text exchange loop (new WSS endpoint `/conversation-relay`)

```
Caller speaks ─▶ Twilio (STT) ─"prompt" text─▶ backend ─▶ handle_turn ─"text" token(s)─▶ Twilio (TTS) ─▶ Caller
```

- Twilio sends JSON events: `setup` (includes `callSid`, custom params),
  `prompt` (the caller's transcribed utterance), `interrupt` (barge-in — caller
  spoke over playback), `dtmf`, `error`.
- The backend maps `session_id = phone-{CallSid}`, calls `handle_turn` with the
  transcribed text, and returns the reply as `text` token messages, which Twilio
  speaks. **Endpointing, barge-in handling, and audio codecs are Twilio's job** —
  not ours.
- Optional: stream the reply in chunks for lower perceived latency; handle
  `interrupt` by stopping further tokens.

This removes the μ-law codec / resampling / VAD work that raw Media Streams would
have required — the main saving that makes the deadline feasible.

### 6.3 Phone → Zendesk

Same detection-gated model as §4.3, keyed by `phone-{CallSid}`. The call end (the
relay socket closing / `stop`) is the natural conversation-end signal: if the gate
fired during the call → open ticket with full transcript; otherwise → closed log
record written at end. Optional call-recording link attached. Escalation reuses
the handoff machinery.

### 6.4 Cost note

ConversationRelay is **~$0.07/min** on top of voice minutes (Twilio does STT+TTS),
vs ~$0.03–0.05/min for Media Streams + Google. We accept the higher per-minute
cost for the POC in exchange for speed and simplicity; revisit Media Streams as a
cost optimization if call volume grows (see the cost breakdown doc).

## 7. Cross-Cutting Concerns

### 7.1 Configuration (`platform/config.py`, additive)

New `Settings` fields + `.env.example` entries:

```
# Twilio (WhatsApp + Phone)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=      # whatsapp:+E164 (the shared number)
TWILIO_PHONE_NUMBER=         # +E164 (same underlying number for voice)
TWILIO_WEBHOOK_BASE_URL=     # public https/wss base (ngrok in dev)

# Email (Zendesk-native) — reuses existing ZENDESK_* credentials
ZENDESK_EMAIL_WEBHOOK_SECRET=  # shared secret for the Zendesk email trigger
```

Add per-channel flags so routes register only when configured (mirrors how
`crm_provider` / `voice_provider` / Sunshine bridge are wired conditionally in
`main.py`). **Email needs no new vendor** — it reuses the existing `ZENDESK_*`
API credentials plus the webhook secret. Twilio channels need a public **HTTPS +
WSS tunnel** (ngrok in dev) so Twilio can reach the webhook and media stream; the
email trigger also needs the backend reachable from Zendesk.

### 7.2 Wiring (`main.py`)

- Instantiate `TwilioChannelAdapter(settings)` when Twilio credentials are
  present; expose it as the `ChatPort` for Twilio sessions.
- Instantiate `ZendeskEmailChannelAdapter(settings)` (reusing the Zendesk client)
  as the `ChatPort` for `email-*` sessions. Select the outbound adapter **per
  channel prefix** of the `session_id` (`whatsapp-*` / `phone-*` / `email-*`).
- Register the new routes — Phase A: `/webhooks/twilio-whatsapp`;
  Phase B: `/webhooks/zendesk-email`; Phase C: `/webhooks/twilio-voice` +
  the `/conversation-relay` WSS endpoint.

### 7.3 Security (`rules/security-mandate.md`, `rugged-software-constitution.md`)

- Verify `X-Twilio-Signature` on **every** Twilio webhook; reject on mismatch.
- Verify the **shared-secret header** on `/webhooks/zendesk-email` (same pattern
  as the existing `/webhooks/zendesk-support` route); reject on mismatch.
- **Email loop prevention:** scope the Zendesk trigger to end-user comments only
  and honor the AI-paused flag, so AI/agent replies never re-trigger the webhook.
- Validate **ConversationRelay parameters** on the `/conversation-relay` WSS
  endpoint (pass a signed token as a custom param in the TwiML); reject unknown
  `callSid`.
- Secrets only in env/`.env` (never logged). Redact tokens in logs.
- Be explicit that **PII** (phone numbers, emails, names, message bodies,
  transcripts) flows into Zendesk — keep to the designed comment sinks only.
- Consider basic **rate limiting** / abuse protection on public webhooks.

### 7.4 Testing (`.agents/` test-first expectations)

- **Unit:** signature/secret verification (valid/invalid) for each webhook,
  webhook payload parsing, `session_id` mapping per channel,
  `TwilioChannelAdapter.send_message` and `ZendeskEmailChannelAdapter.send_message`
  (httpx mocked), Zendesk `ensure_conversation_ticket` /
  `append_conversation_comment` / `write_closed_log` (httpx mocked), and the
  **detection gate** (AI-tool flag, handoff, sentiment-threshold → open ticket;
  none → closed log / auto-solve). Email: auto-send-vs-draft mode selection, and
  the loop-prevention guard. Phase C: ConversationRelay event handling
  (`setup` / `prompt` / `interrupt`) → `handle_turn` → `text` reply tokens.
- **Integration:** each inbound route → `handle_turn` with a mock orchestrator;
  assert auth + outbound call shape; assert the gate routes an actionable
  conversation to an open ticket and a trivial one to a closed log / solved
  ticket; email hybrid mode posts public-reply+solve vs private-note+open
  correctly; handoff path reuses the already-open ticket.
- **Manual e2e:** Twilio **WhatsApp sandbox** + trial number over ngrok, and a
  test email to the Zendesk support address → real round-trip → Zendesk ticket
  appears, updates, and (for email) auto-solves or drafts as designed.
- Keep co-located `test_*.py` next to the new code, run with
  `.venv/bin/pytest src/`; `ruff` + `mypy --strict` must stay clean.

## 8. Alternatives Considered

- **Meta WhatsApp Cloud API direct** (instead of Twilio Sender): more control /
  lower per-message cost, but two vendors and a WhatsApp number separate from the
  Twilio voice number — **rejected** (breaks the single-number goal).
- **Synchronous TwiML reply** (instead of async REST): simpler, but breaks on
  slow AI turns and can't deliver later agent replies — **rejected**.
- **Direct email ingestion (IMAP / SendGrid / Mailgun / Postmark inbound parse)**
  (instead of Zendesk-native): more control over the raw email, but rebuilds
  plumbing Zendesk already provides (threading, spam, attachments) and duplicates
  the inbox — **rejected**; Zendesk owns the inbox.
- **Email auto-send everything** or **draft-only** (instead of hybrid): auto-send
  is fastest but ungated on a permanent channel; draft-only is safest but needs a
  human for every email — **rejected** in favor of detection-driven hybrid.
- **Zendesk Sunshine-native conversations** (instead of Support tickets): richer
  Agent-Workspace experience and you already have `SunshineConversationsAdapter`,
  but requires custom-channel setup and more wiring — **deferred** as a possible
  upgrade; Support tickets chosen for the first cut.
- **Raw Twilio Media Streams + Google STT/TTS** (instead of ConversationRelay):
  cheaper per-minute (~$0.03–0.05 vs ~$0.07) and more control, but requires
  building the realtime audio loop (μ-law codecs, endpointing, barge-in) — too
  much for the 2026-07-07 demo — **deferred** as a cost optimization for higher
  call volumes.
- **Gather/Say or Record turn-based phone**: simpler but less natural — **rejected**
  in favor of ConversationRelay's managed realtime experience.

## 9. Out of Scope (this design)

- Business-initiated WhatsApp template campaigns / outbound marketing.
- WhatsApp interactive components (buttons/lists), rich media galleries.
- Email attachment parsing by the AI (attachments stay on the Zendesk ticket).
- Frontend changes (these channels are server-side; the Vue app is unaffected).
- Raw Media Streams + self-hosted STT/TTS (deferred cost optimization — §8).

## 10. Prerequisites & Additional Scope

These are non-code blockers and cross-functional work items that must be in place
(or budgeted) for the implementation to land. **Lead times matter** — start the
long-pole items (WABA verification) early, in parallel with Phase A coding.

### 10.1 Accounts, approvals & access

| Item | Needed for | Notes / lead time |
|---|---|---|
| Twilio account upgraded **trial → paid** | WhatsApp + Phone (prod) | Trial can only reach verified numbers and injects sandbox text. Dev/sandbox is fine on trial. |
| Twilio **phone number** (voice-capable, +60 MY) | Phone, WhatsApp sender | Monthly rental; some countries need regulatory bundle / local address. |
| **WhatsApp Business Account (WABA)** + **Meta Business verification** | WhatsApp (prod) | **Critical path / long pole** — days-to-weeks. Use the WhatsApp **sandbox** for dev while this clears. |
| Twilio **WhatsApp Sender** + display-name approval | WhatsApp (prod) | Links the WABA to the Twilio number. |
| Zendesk **admin access** | Email + all ticketing | Configure support address, API token, triggers/webhooks; enough **agent seats** for handoff. |
| Zendesk **support address** + email **trigger/webhook** | Email | The trigger calls `/webhooks/zendesk-email` with the shared secret. |
| Google Cloud project (existing) with **Gemini**, and **Speech-to-Text / TTS** enabled | AI + Phone | Billing + quotas; phone realtime adds STT/TTS usage. |
| **Public HTTPS + WSS endpoint** | Twilio webhooks + media stream + Zendesk trigger | ngrok in dev; a real TLS domain in prod. |
| Secrets management | All | Twilio + Zendesk tokens, webhook secrets — env/secret store, never in repo. |

### 10.2 Cost model

> **The full cost model is maintained in a dedicated doc — the single source of
> truth:** [`docs/proposals/2026-06-25-omnichannel-poc-cost-breakdown.md`](../../proposals/2026-06-25-omnichannel-poc-cost-breakdown.md).
> It has the Twilio, Meta (Malaysia), and Google rate tables, the formula, and
> three worked monthly scenarios. Summary below.

**Confirmed key rates (MY +60):**

| Driver | Rate |
|---|---|
| Phone number (shared WA + voice) | **$4/mo local**, $25/mo toll-free |
| WhatsApp message | **$0.005** (Twilio); **Meta = $0** (AI replies are "service" category) |
| Phone — voice + **ConversationRelay** | $0.0085 + **$0.07** /min ≈ **$0.078/min** |
| Phone — Media Streams + Google (deferred optimization) | ≈ $0.03–0.05/min |
| Email | $0 (Zendesk-native) |

**Indicative monthly total:** ≈ **$28/mo** (low volume) to ≈ **$87/mo** (medium:
500 WA conversations + 200 calls). **Phone minutes are the dominant cost**;
WhatsApp + Email are nearly free because the AI only *replies* (Meta service
category). Still to confirm: Google STT/TTS exact rates, and any number
regulatory/address requirement.

### 10.3 Additional (non-channel) scope

- **Provisioning runbook:** step-by-step for account upgrade, number purchase,
  WABA/Meta verification, Twilio Sender, Zendesk trigger setup.
- **Cost monitoring & alerts:** track per-channel message/minute volume; set
  Twilio usage triggers / budget alerts to avoid bill shock (esp. phone).
- **Compliance / PII:** transcripts + phone/email land in Zendesk — confirm data
  residency and retention expectations with the customer.
- **Resilience:** define fallback behavior when Twilio or Zendesk APIs are
  unavailable (queue/retry, graceful "we'll get back to you").
- **Load/latency testing:** required for the Phase C realtime phone path.

## 11. Implementation Plan (phased task outline)

> A detailed step-by-step plan (with file-level tasks and tests) will be produced
> via the writing-plans workflow. High-level order:

**Phase A — WhatsApp**
1. Config: add Twilio settings + `.env.example`; conditional wiring scaffold.
2. `TwilioChannelAdapter` (`ChatPort.send_message` via Twilio REST) + tests.
3. Signature-verification helper + tests.
4. `POST /webhooks/twilio-whatsapp` route → `handle_turn` → async reply + tests.
5. Detection gate: `flag_for_ticket_tool` agent tool + safety-net rules (handoff,
   negative sentiment) + tests.
6. Zendesk additive methods (`ensure_conversation_ticket`,
   `append_conversation_comment`, `write_closed_log`) + tests.
7. Conversation-end flush for closed log records (WhatsApp idle sweep) + tests.
8. Handoff adjustment for Twilio sessions (reuse the already-open ticket) + tests.
9. Manual e2e via WhatsApp sandbox + ngrok; verify both open-ticket and
   closed-log paths in Zendesk.
10. WABA / Meta business verification for the production number.

**Phase B — Email (after A; low complexity, reuses the gate)**
1. Config: add `ZENDESK_EMAIL_WEBHOOK_SECRET`; per-channel adapter selection.
2. `ZendeskEmailChannelAdapter` (`ChatPort.send_message` via Zendesk API,
   public-reply vs private-note) + tests.
3. `POST /webhooks/zendesk-email` route (secret-verified, loop-guarded) →
   `handle_turn` with `session_id = email-{ticketId}` + tests.
4. Hybrid reply-mode selection wired to the detection gate (auto-send+solve vs
   draft+open) + tests.
5. Configure the Zendesk **trigger** (end-user comments only) to call the webhook;
   document the setup.
6. Manual e2e: send a test email → AI auto-solves a simple one and drafts a
   sensitive one.

**Phase C — Phone (Twilio ConversationRelay; after A & B)**
1. `POST /webhooks/twilio-voice` returning `<Connect><ConversationRelay>` TwiML
   (configure TTS voice + transcription language; confirm EN/MS support).
2. `/conversation-relay` WSS endpoint: handle `setup` / `prompt` / `interrupt`
   events → `handle_turn(phone-{CallSid}, text)` → reply as `text` tokens.
3. Phone → Zendesk transcript ticket via the detection gate; reuse handoff.
4. Manual e2e over the trial number; tune voice, latency, and barge-in.
