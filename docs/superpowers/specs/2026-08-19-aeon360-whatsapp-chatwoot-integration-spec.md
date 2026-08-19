# AEON360 WhatsApp × CRM Integration Spec

**Date:** 2026-08-19
**Audience:** AEON360 backend engineer (`apac-aeon360-foundry-prototype`, Cloud Run `aeon360-backend`)
**Owner of CRM + Twilio changes:** Yuda (Devoteam)
**Status:** agreed design, ready to implement

---

## 1. Why we're doing this

Today the WhatsApp number `+1 682 399 3949` goes straight to the AEON360 Cloud Run
service, which answers every message with Gemini. There is no way for a human to
join the conversation. A customer asking *"can you connect me with a human agent?"*
just gets another AI reply, because the agent has no handoff capability and no
connection to the CRM.

We want three things:

1. **The CRM sees the full conversation** — every AI message and every customer
   message, live and historical, in one place.
2. **A human agent can interrupt** at any moment if the AI answers wrongly, and take
   over the conversation.
3. **The AEON360 AI keeps doing the thinking.** We are *not* moving your agent,
   personas, journey state, or catalogue grounding into the CRM.

---

## 2. The one constraint that shapes everything

A Twilio phone number has **exactly one inbound webhook**. Whoever holds it owns the
conversation. Right now that is Cloud Run
(`https://aeon360-backend-247165654737.asia-southeast1.run.app/webhooks/whatsapp`).

**We are moving that webhook to the CRM (Chatwoot).** Your service stays the brain,
but it stops talking to Twilio directly and talks to the CRM instead.

This is the key trade: you give up the Twilio transport, and in exchange you get
conversation history, human handoff, and agent interruption for free — all handled
by machinery the CRM already has. You write no message-mirroring code, no contact
management, and no phone-number bookkeeping.

---

## 3. Target architecture

```
                    ┌──────────────────────────────────────────┐
 WhatsApp           │  CRM (Chatwoot)                          │
    │               │                                          │
    ▼               │  • owns the Twilio channel               │
 Twilio ───────────►│  • creates contact + conversation        │
   POST /twilio/    │  • stores every message (history)        │
        callback    │  • human agents read & reply here        │
                    └────────────┬─────────────────────────────┘
                                 │  ① POST message_created  (HMAC-signed)
                                 ▼
                    ┌──────────────────────────────────────────┐
                    │  Cloud Run  aeon360-backend              │
                    │                                          │
                    │  • POST /chatwoot/bot   ← NEW            │
                    │  • Gemini agent, personas, journey state │
                    │    (UNCHANGED)                           │
                    └────────────┬─────────────────────────────┘
                                 │  ② POST .../messages     (reply)
                                 │  ③ POST .../toggle_status (handoff)
                                 ▼
                    back through Chatwoot → Twilio → WhatsApp
```

You never call Twilio again. Chatwoot sends every outbound message on your behalf.

---

## 4. Ownership model — one source of truth

The CRM's `conversation.status` decides who owns the conversation. **There is no
second flag, in your service or anywhere else.** This is deliberate: two flags
desync, one cannot.

| `status`   | Meaning                                            | Your bot |
|------------|----------------------------------------------------|----------|
| `pending`  | The AEON360 AI owns the conversation               | replies  |
| `open`     | A human agent owns it                              | **silent** |
| `resolved` | Conversation closed                                | silent   |

New conversations on a bot-enabled inbox are set to `pending` automatically by
Chatwoot, so this works from the very first message with no setup on your side.

`status` is included in every webhook payload — you do not need an extra API call to
read it (except for the race guard in §5.6).

---

## 5. What you need to build

### 5.1 A new inbound endpoint: `POST /chatwoot/bot`

This replaces `POST /webhooks/whatsapp` as your entry point. Chatwoot will POST
JSON events here.

**Verify the HMAC signature and reject anything that fails.** Chatwoot signs every
delivery:

| Header                 | Value |
|------------------------|-------|
| `X-Chatwoot-Signature` | `sha256=<hex>` where `<hex> = HMAC_SHA256(secret, "{timestamp}.{raw_body}")` |
| `X-Chatwoot-Timestamp` | unix seconds, used in the signature |
| `X-Chatwoot-Delivery`  | UUID, unique per delivery |

The `secret` is the agent bot's secret; Yuda will give it to you (§7). Compute the
HMAC over the **raw request body bytes**, not a re-serialised dict — re-serialising
changes key order and whitespace and the signature will never match. Compare with a
constant-time comparison. Reject with `401` on mismatch. We also recommend rejecting
timestamps skewed more than ~300 seconds to blunt replay.

**Dedupe on `X-Chatwoot-Delivery`.** Chatwoot retries failed deliveries (3 attempts,
3 seconds apart), so the same event can legitimately arrive twice. Keep a short-TTL
set of seen delivery IDs and skip repeats — otherwise a retry produces a duplicate
reply to the customer.

**Return `200` immediately** and do the Gemini work in the background. Your existing
`/webhooks/whatsapp` handler already follows this shape — keep it.

**Be precise about failure status codes.** Chatwoot only retries an agent-bot
delivery on **`429` or `500`** (`RETRYABLE_AGENT_BOT_STATUSES = [429, 500]`).
Anything else — including `502` and `503` — is *not* retried and the event is lost.
If you want a transient failure retried, return `500`, not `503`.

### 5.1.1 Fail-safe: what happens if your service is down

Worth knowing, because it means customers never get stranded. When an agent-bot
delivery for `message_created` / `message_updated` ultimately fails, Chatwoot moves
the `pending` conversation to `open` and posts an activity note on it
(`Webhooks::Trigger#update_conversation_status`). The conversation lands in the human
queue automatically.

Two consequences:

- A Cloud Run outage degrades to "a human handles it", not "the customer is ignored".
- After such a failure the conversation is `open`, so your bot stays silent even once
  it recovers — by design. An agent sets it back to `pending` to hand control back.

### 5.2 The event payload

Chatwoot sends several event types. **You only need `message_created`**; ignore the
rest (`conversation_created`, `conversation_status_changed`, `conversation_updated`,
`conversation_opened`, `conversation_resolved`, `message_updated`) unless noted.

```jsonc
{
  "event": "message_created",
  "id": 12345,
  "content": "can you connect me with a human agent?",
  "message_type": "incoming",       // "incoming" | "outgoing" | "template"
  "content_type": "text",
  "private": false,                  // true = internal agent note, never customer-visible
  "source_id": "SM0123...",          // Twilio message SID
  "created_at": "2026-08-19T06:00:00.000Z",
  "sender": {
    "id": 42,
    "name": "Yuda Adi",
    "type": "user"                   // see the table in 5.3 — NOTE: absent for contacts
  },
  "inbox":   { "id": 1, "name": "AEON360 Whatsapp" },
  "account": { "id": 1, "name": "AEON360" },
  "conversation": {
    "id": 7,                         // ← this is the display_id; use it in all API URLs
    "inbox_id": 1,
    "status": "pending",
    "channel": "Channel::TwilioSms",
    "can_reply": true,
    "labels": [],
    "custom_attributes": {},
    "meta": { "sender": { /* contact */ }, "assignee": null }
  }
}
```

**Two gotchas that will cost you an afternoon if missed:**

- `conversation.id` in the payload is Chatwoot's **`display_id`**, which is what all
  the REST URLs expect. Do not go looking for another id.
- `sender.type` is `"user"` for a human agent and `"agent_bot"` for you — but it is
  **absent for customers**. `Contact#webhook_data` does not emit a `type` field.
  Identify the customer by `message_type == "incoming"`, never by `sender.type`.

### 5.3 Decision table — the core logic

On every `message_created`:

| `message_type` | `sender.type` | `conversation.status` | `private` | Action |
|---|---|---|---|---|
| `incoming`  | *(absent)*  | `pending` | `false` | **Generate a reply** and post it (§5.4) |
| `incoming`  | *(absent)*  | `open`    | `false` | **Ignore** — a human is driving |
| `incoming`  | *(absent)*  | `resolved`| `false` | Ignore |
| `outgoing`  | `agent_bot` | any       | any     | Ignore — this is your own message echoing back |
| `outgoing`  | `user`      | `pending` | `false` | **INTERRUPT** (§5.5) |
| `outgoing`  | `user`      | `open`    | `false` | Ignore — human already owns it |
| any         | any         | any       | `true`  | Ignore — internal note, not customer-visible |
| `template`  | any         | any       | any     | Ignore |

### 5.4 Replying — via Chatwoot, not Twilio

Replace the Twilio send with a Chatwoot API call. **You already have the right
abstraction for this**: `platform/whatsapp/ports.py` defines a `WhatsAppGateway`
protocol with `send(to, body, media_url)`, implemented by `gateway_twilio.py` and
`gateway_mock.py`. Add a third implementation — `gateway_chatwoot.py` — and
`features/whatsapp/service.py::_send` barely changes.

One adjustment to the port: Chatwoot addresses replies by **conversation id**, not
phone number. Treat `to` as an opaque destination handle and pass the conversation
id through it. It is already a `str`, so no signature change is needed — just a
docstring update on the protocol saying the value is channel-specific.

```
POST {CRM_BASE_URL}/api/v1/accounts/1/conversations/{conversation_id}/messages
Header: api_access_token: <AGENT_BOT_TOKEN>
Body:   { "content": "…your reply…", "message_type": "outgoing" }
```

Chatwoot stores the message, shows it to the agents, and delivers it over Twilio.

**Message chunking:** your `gateway_twilio.chunk_body()` splits long replies for
Twilio's body limit. Keep chunking — send each chunk as its own Chatwoot message so
the WhatsApp delivery still respects the limit.

**Media (product photos):** today you pass `media_url` to Twilio directly. In
Chatwoot, upload the file via `multipart/form-data` on the same endpoint using the
`attachments[]` field (`Messages::MessageBuilder` reads `params[:attachments]`).
Chatwoot re-exposes it as a `media_url` to Twilio on send, so the customer experience
is identical. Note this is an **upload**, not a URL reference — if your photos are
currently public URLs you will need to fetch the bytes and forward them.

### 5.5 Interrupt — you must implement it yourself

**This is the part most likely to be got wrong, so read it carefully.**

Chatwoot has a built-in "a human replied, so hand the conversation off the bot"
behaviour — and **it will not fire for us.**
`Enterprise::Message#mark_pending_conversation_as_open_for_human_response` is gated
on `captain_pending_conversation?`, which requires `CaptainInbox.exists?(inbox_id:)`.
That only matches Chatwoot's own built-in "Captain" AI. Our inbox uses a generic
AgentBot, so it does not match.

**Consequence: if a human agent types a reply into a `pending` conversation, the
status stays `pending`, and a naive bot would keep answering over the top of them.**

So when you see `message_type: "outgoing"` with `sender.type: "user"` on a `pending`
conversation:

1. Abort any in-flight generation for that conversation.
2. Flip the conversation to `open`:

```
POST {CRM_BASE_URL}/api/v1/accounts/1/conversations/{conversation_id}/toggle_status
Header: api_access_token: <AGENT_BOT_TOKEN>
Body:   { "status": "open" }
```

From then on, every rule in §5.3 keeps you silent, because status is no longer
`pending`.

### 5.6 The race — and the guard

An agent hits *send* while Gemini is still generating. That window is several
seconds wide and it *will* happen in a live demo. The interrupt in §5.5 does not
help on its own, because your reply is already in flight.

**Guard: immediately before POSTing a reply, re-read the conversation and drop the
reply if it is no longer `pending`.**

```
GET {CRM_BASE_URL}/api/v1/accounts/1/conversations/{conversation_id}
Header: api_access_token: <AGENT_BOT_TOKEN>
→ { "status": "open", … }   →  discard the generated reply, do not send
```

This is not airtight — an agent can still send in the milliseconds between the check
and the POST — but it closes the multi-second window that actually occurs. The CRM's
own AI orchestrator uses this same pattern for the same reason.

### 5.7 Customer explicitly asks for a human

Add a `talk_to_human` tool to the agent alongside its existing tools. When the model
calls it:

1. Send a brief acknowledgement ("Let me get a colleague to help you — one moment.")
   via §5.4.
2. Call the same `toggle_status` → `open` as §5.5.

The conversation lands in the agents' queue in the CRM.

Chatwoot treats this specially and correctly: when the caller is an AgentBot and it
moves a `pending` conversation to `open`, Chatwoot runs `bot_handoff!`, which stamps
`waiting_since` and emits a `conversation.bot_handoff` event. This is the officially
supported handoff path, not a workaround.

### 5.8 Handing control back

When the agent is done they either resolve the conversation or set it back to
`pending` from the CRM UI. Because all your rules read `conversation.status` from the
payload, **you need no code for this** — the bot simply starts replying again once
status is `pending`.

### 5.9 What to retire

- The Twilio inbound route `POST /webhooks/whatsapp` and its signature validation.
  Keep the code until cutover is confirmed, then delete.
- `gateway_twilio.py` as the production adapter (keep it for local testing if useful).
- `TWILIO_*` env vars on Cloud Run, once cutover is confirmed.

**Keep unchanged:** the agent, personas, greeting logic, `journey_state.py`,
`channel_sessions.py`, catalogue grounding, MCP quick-actions, `qr.py`. Deep links
carrying a persona slug still work — the slug arrives as ordinary inbound message
text exactly as it does today.

---

## 6. What we are explicitly NOT building

Stated so nobody builds it by accident:

- **No message mirroring.** You never copy messages into the CRM. Chatwoot records
  every message because it owns the channel.
- **No contact or conversation creation.** Chatwoot creates them from the inbound
  Twilio callback with the correct phone-number addressing.
- **No second "who owns this conversation" flag.** `conversation.status` is it.
- **No CRM-side AI.** The CRM has its own AI agent-bot for other tenants; it will
  not be enabled on this inbox. Your service is the only brain.

---

## 7. What Yuda provides before you start

| Item | Value |
|---|---|
| CRM base URL | `http://aeon360.crm.34-50-103-151.nip.io` *(HTTPS being added — use the HTTPS URL once issued)* |
| Account ID | `1` |
| Inbox ID | `1` ("AEON360 Whatsapp") |
| `AGENT_BOT_TOKEN` | the agent bot's `access_token` — sent separately, never in git |
| `CHATWOOT_BOT_SECRET` | the agent bot's HMAC secret for §5.1 — sent separately |

Yuda also: registers the agent bot pointing at your `/chatwoot/bot` URL, attaches it
to inbox 1, enables TLS, and repoints the Twilio webhook.

**You need to give Yuda:** the public URL of your new `/chatwoot/bot` endpoint.

---

## 8. Rollout

The Twilio webhook switch is a one-way door — the moment it points at the CRM, the
current demo bot goes silent. So:

1. You deploy `/chatwoot/bot` to Cloud Run. Old `/webhooks/whatsapp` still live.
2. Yuda registers the agent bot and gives you the token + secret.
3. **Test on the Twilio sandbox number `+1 415 523 8886` first** — your existing
   go-live runbook already documents this path. Point the *sandbox* inbound webhook
   at the CRM and verify the whole loop end to end.
4. Once green, repoint the production number `+1 682 399 3949`.
5. Confirm, then delete the retired code in §5.9.

Rollback at any point is: point the Twilio webhook back at Cloud Run.

---

## 9. Acceptance tests

Run all of these against the sandbox before production cutover:

| # | Scenario | Expected |
|---|---|---|
| 1 | Customer sends first message | Conversation appears in CRM as `pending`; AI replies; both messages visible in CRM |
| 2 | Multi-turn conversation | Full history visible in CRM, correct order, correct sender attribution |
| 3 | Customer sends a long reply-triggering message | Reply chunked, all parts delivered, all parts in CRM |
| 4 | AI sends a product photo | Image delivered on WhatsApp and visible in CRM |
| 5 | Customer says "connect me with a human agent" | AI acknowledges, status → `open`, conversation appears in agent queue, **AI stops replying** |
| 6 | Agent types a reply into a `pending` conversation | Status flips to `open`, customer receives it, **AI goes silent** |
| 7 | Agent replies *while the AI is generating* | The in-flight AI reply is discarded — the customer does **not** receive an AI message after the human's |
| 8 | Agent sets status back to `pending` | AI resumes on the next customer message |
| 9 | Agent writes a **private note** | Customer receives nothing; AI does not react |
| 10 | Same delivery replayed (duplicate `X-Chatwoot-Delivery`) | Exactly one reply sent |
| 11 | Request with a bad HMAC signature | `401`, nothing processed |
| 12 | Cloud Run down, customer sends a message | Conversation auto-moves to `open` with an activity note; a human can pick it up (§5.1.1) |

Test 7 is the one that matters most for the "AI is answering wrongly" use case, and
it is the one most likely to be silently broken. Please test it deliberately rather
than assuming.

---

## 10. Open items

- **Media via Chatwoot attachments** — confirm the multipart upload works with your
  product-photo pipeline; this is the only genuinely new plumbing on your side.
- **Proactive / outbound-initiated messages.** If AEON360 ever sends first (campaign
  or template push), those must also go through the Chatwoot API or they will not
  appear in the CRM. Not in scope now — flag it if you have such a flow.
- **AEON360 team CRM access** — Yuda to create accounts so the team can read
  history.
- **TLS on the CRM domain** — in progress; must land before production cutover.

---

## 11. Summary of your work

1. New `POST /chatwoot/bot` endpoint: HMAC verification, delivery dedupe, fast `200`.
2. Decision table in §5.3.
3. New `gateway_chatwoot.py` implementing the existing `WhatsAppGateway` port.
4. `talk_to_human` tool → `toggle_status` → `open`.
5. Interrupt detection on human outgoing messages → `toggle_status` → `open`.
6. Pre-send status re-check to kill in-flight replies.
7. Retire the Twilio inbound path after cutover.

Everything else in your service stays as it is.
