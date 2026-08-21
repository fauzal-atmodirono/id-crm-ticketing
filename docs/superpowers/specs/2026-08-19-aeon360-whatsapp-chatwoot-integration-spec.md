# AEON360 WhatsApp × CRM Integration Spec

**Date:** 2026-08-19 · **last updated:** 2026-08-20
**Audience:** AEON360 backend engineer (`apac-aeon360-foundry-prototype`, backend at `innovation.dev.aeon360.net`)
**Owner of CRM + Twilio changes:** Yuda (Devoteam)
**Status:** agreed design, ready to implement. **The CRM side is now ready** —
TLS is live and the agent bot is registered against your domain (§7). Two things
remain: you convert `POST /webhooks/whatsapp` to accept Chatwoot deliveries (§5.1),
then Yuda repoints the Twilio webhook (§8).

---

## 1. Why we're doing this

Today the WhatsApp number `+1 682 399 3949` goes straight to the AEON360 backend at
`innovation.dev.aeon360.net`, which answers every message with Gemini. There is no way for a human to
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
conversation. Right now that is the AEON360 backend
(`https://innovation.dev.aeon360.net/aeon360-customer-waba/webhooks/whatsapp`).

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
                    │  AEON360 backend                         │
                    │  innovation.dev.aeon360.net              │
                    │                                          │
                    │  • POST /webhooks/whatsapp  ← REUSED     │
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

### 5.1 Your inbound endpoint: `POST /chatwoot/bot`

**Built and live** — verified 2026-08-21 answering `401` to an unsigned POST at
`https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot`.

The 2026-08-20 plan was to reuse the existing `/webhooks/whatsapp` path. AEON360
built a **separate route** instead (`src/api/chatwoot.py`), which is the better
call — see the callout in §7. The CRM's registered `outgoing_url` was corrected to
match on 2026-08-21.

**Two routes, two protocols.** `/webhooks/whatsapp` stays Twilio-only and untouched;
`/chatwoot/bot` speaks Chatwoot and nothing else:

| | Twilio — `/webhooks/whatsapp` | Chatwoot — `/chatwoot/bot` |
|---|---|---|
| Content-Type | `application/x-www-form-urlencoded` | `application/json` |
| Body | `From=…&Body=…&MessageSid=…` | `{event, message_type, content, conversation:{status}}` |
| Signature | `X-Twilio-Signature`, HMAC-**SHA1** over URL + sorted params | `X-Chatwoot-Signature`, HMAC-**SHA256** over `{timestamp}.{raw_body}` |
| Idempotency key | `MessageSid` | `X-Chatwoot-Delivery` |

> **No `Content-Type` branch is needed** — that was only required by the shared-path
> plan. With two routes each verifier owns its own URL, and the two can never see
> each other's traffic.
>
> **But the two routes have deliberately opposite error policies, and that inversion
> is the trap.** `/webhooks/whatsapp` must never return 5xx, because Twilio retries
> 5xx and hammers it. `/chatwoot/bot` must return `500` on a transient internal
> failure, because Chatwoot retries **only** on `429`/`500` and drops `403`/`502`/`503`
> permanently — a message silently lost. Bad signature is `403` on the Twilio route
> and `401` on the Chatwoot one. Comment this at both call sites: a reader who knows
> the never-5xx rule will otherwise "fix" the `500` and start losing messages.

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

- A backend outage degrades to "a human handles it", not "the customer is ignored".
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

- The whole `POST /webhooks/whatsapp` route — form parsing, `X-Twilio-Signature`
  validation, and the direct Twilio send path behind it. With `/chatwoot/bot` on a
  separate URL the Twilio route is dead weight after cutover, not a shared handler
  to prune. **Keep it until production cutover is confirmed — it is the entire
  rollback path (§8), and with the sandbox retired it is the only one.**
- `gateway_twilio.py` as the production adapter (keep it for local testing if useful).
- `TWILIO_*` env vars on your backend, once cutover is confirmed.

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

The agent bot is **already created and attached** — `agent_bot id=1`, name
"AEON360 Assistant", active on inbox 1.

| Item | Value |
|---|---|
| CRM base URL | `https://aeon360.crm.34-50-103-151.nip.io` *(TLS live since 2026-08-20 — Let's Encrypt, auto-renewing. Plain `http://` still answers as a fallback, but use HTTPS.)* |
| Account ID | `1` |
| Inbox ID | `1` ("AEON360 Whatsapp") |
| Agent bot ID | `1` |
| Registered `outgoing_url` | `https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot` *(corrected 2026-08-21 to match the route you actually built — see the note below)* |
| `AGENT_BOT_TOKEN` | «AGENT_BOT_TOKEN» |
| `CHATWOOT_BOT_SECRET` | «CHATWOOT_BOT_SECRET» |

`AGENT_BOT_TOKEN` is the `api_access_token` header value for every call in §5.
`CHATWOOT_BOT_SECRET` is the HMAC secret for verifying inbound deliveries (§5.1).
Store both as secrets in your deployment — do not commit them.

**TLS on the CRM domain is done** (2026-08-20), and the `outgoing_url` correction is
done (2026-08-21). The only CRM-side action still outstanding is repointing the
Twilio Sender at cutover (§8).

> **⚠️ The registered URL was wrong until 2026-08-21, and the failure would have been
> silent.** This spec originally registered `outgoing_url` against
> `/webhooks/whatsapp`; AEON360 built `/chatwoot/bot` instead and that path never
> grew a Chatwoot branch. Verified live 2026-08-21:
>
> ```
> POST /webhooks/whatsapp  (application/json)  → 403   ← Twilio verifier, wrong route
> POST /chatwoot/bot       (application/json)  → 401   ← Chatwoot verifier, correct
> ```
>
> Chatwoot drops `403` permanently. Every event would have been lost with no error
> visible on either side — the conversation would just fall to a human. The
> registration now points at `/chatwoot/bot`.
>
> **If either side moves an endpoint, tell the other before deploying.** The CRM
> must be re-registered or no events arrive, and nothing about that is visible from
> AEON360's side.

---

## 8. Rollout

The Twilio webhook switch is a one-way door — the moment it points at the CRM, the
current demo bot goes silent. So:

1. ~~You build a Chatwoot-speaking inbound endpoint.~~ **Done** — `/chatwoot/bot`
   is deployed with `crm_enabled: true` and verified answering `401` unsigned
   (2026-08-21).
2. ~~Yuda registers the agent bot and gives you the token + secret.~~ **Done
   2026-08-20**; `outgoing_url` corrected to `/chatwoot/bot` **2026-08-21** (§7).
3. **Repoint the production Twilio WhatsApp Sender `+1 682 399 3949`** at the CRM:

   ```
   https://aeon360.crm.34-50-103-151.nip.io/twilio/callback
   ```

   Verified live and answering `204` over HTTPS as of 2026-08-21.
4. Run the §9 acceptance tests against the production number.
5. Confirm stable, then delete the retired code in §5.9.

> **⚠️ There is no sandbox rehearsal.** The Twilio sandbox number
> `+1 415 523 8886` is retired and is not coming back, so step 3 *is* the test —
> the first Chatwoot-routed message is a real one from a real handset. Three
> consequences:
>
> - **Cut over in a low-traffic window**, with someone watching the CRM inbox and
>   the WABA logs live.
> - **Have the rollback command typed and ready before you flip**, not after.
> - **Test 12 (backend down) can no longer be rehearsed safely** — verify it by
>   pointing a local Chatwoot delivery at a stopped service instead of taking
>   production down.

Rollback is one command: point the Twilio **Sender** back at

```
https://innovation.dev.aeon360.net/aeon360-customer-waba/webhooks/whatsapp
```

**This works only while `/webhooks/whatsapp` is still present** — do not delete it
(§5.9) until production has run stably on the CRM. WhatsApp routing is governed by
the **Sender** (`/v2/Channels/Senders/{XE...}`), *not* the phone number's `SmsUrl`;
editing the wrong one looks successful and changes nothing.

---

## 9. Acceptance tests

Run all of these against the production number immediately after cutover — with the
sandbox retired there is nowhere else to run them, so treat the window as a live
test and keep the rollback to hand (§8):

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
| 12 | Your backend down, customer sends a message | Conversation auto-moves to `open` with an activity note; a human can pick it up (§5.1.1) |

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
  history. Now more urgent than it was: with no sandbox, reading the CRM is the
  only way AEON360 can see what a cutover message actually did.
- **In-memory bindings vs. production.** Bindings, thread ids and the dedupe set
  live in process memory under `--max-instances=1`, so a deploy or a scale-to-zero
  silently reassigns members to `default_member_key` on a fresh thread. Acceptable
  for a demo; flag before this carries real member traffic.
- ~~**TLS on the CRM domain**~~ — **done 2026-08-20.** `https://aeon360.crm.34-50-103-151.nip.io`
  serves a Let's Encrypt certificate (expires 2026-11-17, auto-renewing). The plain
  `http://` vhost was deliberately left in place as a fallback, so nothing that was
  already pointing at it broke.

---

## 11. Summary of your work

1. `POST /webhooks/whatsapp` accepts Chatwoot deliveries: HMAC-SHA256 verification,
   delivery dedupe, fast `200` — alongside the Twilio format until cutover.
2. Decision table in §5.3.
3. New `gateway_chatwoot.py` implementing the existing `WhatsAppGateway` port.
4. `talk_to_human` tool → `toggle_status` → `open`.
5. Interrupt detection on human outgoing messages → `toggle_status` → `open`.
6. Pre-send status re-check to kill in-flight replies.
7. Retire the Twilio branch of that handler after cutover.

Everything else in your service stays as it is.
