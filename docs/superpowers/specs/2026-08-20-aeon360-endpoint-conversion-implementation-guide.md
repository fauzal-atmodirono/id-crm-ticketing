# Converting `POST /webhooks/whatsapp` from Twilio to Chatwoot

> **⚠️ Superseded 2026-08-21 — kept as a wire-format reference only.**
>
> This guide was written on the assumption that AEON360 would convert the existing
> `/webhooks/whatsapp` route in place. They did not: they built a **separate**
> `/chatwoot/bot` route (`src/api/chatwoot.py` in `my-aeon360-customer-waba`), and
> it is deployed and live. The CRM's `outgoing_url` was corrected to match on
> 2026-08-21.
>
> **Stale here:** §0's "you keep the URL" premise, and every route/`Content-Type`
> branching instruction that follows from it — there is no shared path and so no
> mixed-format window to branch on.
>
> **Still accurate and still worth reading:** §2's captured payload (including the
> two properties you only find by looking — `conversation.id` is the display_id the
> REST URLs want, and `sender.type` is absent entirely for contacts), the event
> list, the verified HMAC signature code, and §5's error-policy rules.

**Date:** 2026-08-20
**Audience:** AEON360 backend engineer
**Companion to:** `AEON360 WhatsApp × CRM Integration Spec` — that document explains
*what* the integration does and *why*. This one is the implementation layer for the
single endpoint you have to change. Where they overlap, the spec is authoritative on
behaviour; this document is authoritative on wire format.

---

## 0. Orientation — what actually changes

~~You keep the URL `https://innovation.dev.aeon360.net/aeon360-customer-waba/webhooks/whatsapp`.
AEON360 chose to reuse it rather than add a second route.~~ **Superseded** — the
handler lives at `/chatwoot/bot` on a separate route (see the banner above). Read
the rest of this section as describing that endpoint.

**Everything behind that URL changes.** Today it is a Twilio callback handler. After
this change it is a Chatwoot agent-bot handler. Those are different protocols that
share nothing:

| | Twilio (today) | Chatwoot (after) |
|---|---|---|
| Content-Type | `application/x-www-form-urlencoded` | `application/json` |
| Body | `From=…&Body=…&MessageSid=…` | JSON object, §2 |
| Signature header | `X-Twilio-Signature` | `X-Chatwoot-Signature` |
| Algorithm | HMAC-**SHA1** over URL + sorted params | HMAC-**SHA256** over `{timestamp}.{raw_body}` |
| Idempotency key | `MessageSid` | `X-Chatwoot-Delivery` |
| Reply mechanism | you call Twilio | you call the CRM (§6) |

**What does not change:** your Gemini agent, personas, journey state, catalogue
grounding, MCP quick-actions, `qr.py`, and deep links. This is transport plumbing.

### This has already been tested against your live endpoint

On 2026-08-20 a synthetic inbound WhatsApp message was pushed through the CRM. It
reached your service and your service answered:

```
Exception: Invalid webhook URL
  https://innovation.dev.aeon360.net/aeon360-customer-waba/webhooks/whatsapp
  : 403 Forbidden
```

That `403` is your existing Twilio signature check rejecting a request that carries
no `X-Twilio-Signature`. Nothing is wrong with DNS, TLS, or routing — the CRM can
reach you. Only the handler needs replacing. **Read §5 before you pick your error
codes**, because that `403` also caused the message to be permanently dropped.

---

## 1. Request envelope

Every delivery is a `POST` with these headers:

| Header | Example | Notes |
|---|---|---|
| `Content-Type` | `application/json` | always |
| `X-Chatwoot-Signature` | `sha256=9f2b…` | hex, lowercase, `sha256=` prefix |
| `X-Chatwoot-Timestamp` | `1787183313` | unix seconds, **part of the signed string** |
| `X-Chatwoot-Delivery` | `d8acfecf-be86-41e8-ab72-1a4fe63f02c4` | UUID, unique per delivery attempt |

The CRM's timeout is **5 seconds** (`WEBHOOK_TIMEOUT`). Exceeding it counts as a
failure — see §5.

---

## 2. The real `message_created` payload

This is a genuine capture from the 2026-08-20 test, not an illustration. Values are
abridged where they repeat, but every key shown is really present.

```jsonc
{
  "event": "message_created",
  "id": 1,
  "content": "Test message from Claude — CRM receiving-half check",
  "message_type": "incoming",
  "content_type": "text",
  "content_attributes": {},
  "private": false,
  "source_id": "SMclaudetest20260820a",     // Twilio message SID
  "created_at": "2026-08-19T23:48:33.588Z",
  "inbox":   { "id": 1, "name": "AEON360 Whatsapp" },
  "account": { "id": 1, "name": "AEON360" },

  "sender": {
    "id": 1,
    "name": "Claude Test",
    "phone_number": "+15005550006",
    "email": null,
    "identifier": null,
    "thumbnail": "",
    "blocked": false,
    "additional_attributes": {},
    "custom_attributes": {}
    // NOTE: no "type" key. See the gotcha below.
  },

  "conversation": {
    "id": 1,                                 // ← display_id; use this in all API URLs
    "inbox_id": 1,
    "status": "pending",                     // ← the ownership flag, §4
    "channel": "Channel::TwilioSms",
    "can_reply": true,
    "labels": [],
    "custom_attributes": {},
    "unread_count": 1,
    "waiting_since": 1787183313,
    "first_reply_created_at": null,
    "priority": null,
    "snoozed_until": null,
    "contact_inbox": { "source_id": "whatsapp:+15005550006" },
    "meta": {
      "sender": { "id": 1, "name": "Claude Test", "phone_number": "+15005550006", "type": "contact" },
      "assignee": null,
      "assignee_type": null,
      "team": null,
      "hmac_verified": false
    },
    "messages": [ /* the same message, nested again */ ]
  }
}
```

### Three things that will cost you time if you miss them

1. **`conversation.id` is the `display_id`.** It is what every REST URL in §6 expects.
   There is no other id you need. Do not go hunting.

2. **`sender.type` is absent for customers.** It is `"user"` for a human agent and
   `"agent_bot"` for you, but `Contact#webhook_data` emits no `type` field at all.
   Identify the customer by `message_type == "incoming"`, **never** by `sender.type`.
   (`conversation.meta.sender.type` *does* say `"contact"`, but it describes the
   conversation's contact, not the message's author — do not use it for routing.)

3. **The customer's phone number carries a `whatsapp:` prefix in
   `contact_inbox.source_id`** but not in `sender.phone_number`. If you key journey
   state on phone number, normalise deliberately and pick one.

---

## 3. Which events arrive

The CRM's `AgentBotListener` fires on all of these:

```
message_created            ← the only one you must handle
message_updated
conversation_opened
conversation_resolved
conversation_status_changed
conversation_updated
webwidget_triggered
```

That is the complete list, read off `AgentBotListener` on the running CRM. (§5.2 of
the main spec also names `conversation_created`; it is not an agent-bot event and
never arrives. Ignoring it costs nothing, so this is a correction, not a defect.)

**Filter on the top-level `event` field and return `200` for everything you don't
handle.** A single inbound customer message produced **three** deliveries in the live
test — `message_created`, then `conversation_status_changed` and
`conversation_updated` as the status moved. If you treat every delivery as a message
to answer, you will reply three times.

---

## 4. Signature verification

Chatwoot builds the signature like this (from `lib/webhooks/trigger.rb`):

```ruby
body = @payload.to_json
ts   = Time.now.to_i.to_s
headers['X-Chatwoot-Timestamp'] = ts
headers['X-Chatwoot-Signature'] = "sha256=#{OpenSSL::HMAC.hexdigest('SHA256', @secret, "#{ts}.#{body}")}"
```

So the signed string is the **timestamp, a literal dot, then the raw body**.

```python
import hashlib
import hmac
import os
import time

BOT_SECRET = os.environ["CHATWOOT_BOT_SECRET"].encode()
MAX_SKEW_SECONDS = 300


def verify_signature(raw_body: bytes, signature: str | None, timestamp: str | None) -> bool:
    """Constant-time check of Chatwoot's HMAC over "{timestamp}.{raw_body}"."""
    if not signature or not timestamp:
        return False

    # Reject stale deliveries so a captured request cannot be replayed indefinitely.
    try:
        if abs(time.time() - int(timestamp)) > MAX_SKEW_SECONDS:
            return False
    except ValueError:
        return False

    expected = hmac.new(
        BOT_SECRET,
        timestamp.encode() + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

**Compute the HMAC over the raw request body bytes.** If you parse the JSON and
re-serialise it, key order and whitespace change and the signature will never match.
In FastAPI that means `await request.body()` *before* touching `request.json()`.

`CHATWOOT_BOT_SECRET` is in §7 of the main spec. Store it as a deployment secret.

---

## 5. Response codes — this one bites

Chatwoot retries an agent-bot delivery **only on `429` or `500`**:

```ruby
RETRYABLE_AGENT_BOT_STATUSES = [429, 500]
retry_on Webhooks::Trigger::RetryableError, wait: 3.seconds, attempts: 3
```

Anything else — `400`, **`403`**, `404`, `502`, `503` — is **not retried**. The
delivery is abandoned, and the CRM then moves the `pending` conversation to `open`
and posts an activity note on it:

> *Conversation was marked open by system due to an error with the agent bot.*

That is a deliberate fail-safe: a customer is never stranded talking to a dead bot, a
human picks it up instead. But it also means **the wrong status code silently loses
the message**, which is exactly what happened in the live test.

| Situation | Return |
|---|---|
| Handled, or an event you ignore | `200` |
| Bad or missing signature | `401` |
| Duplicate `X-Chatwoot-Delivery` | `200` (already handled — do not re-process) |
| Transient failure you want retried (Gemini timeout, DB blip) | **`500`** |
| Rate limited | `429` |

**Never return `403` or `503` for something you want retried.** Note also that after
a failure the conversation is `open`, so your bot stays silent even once it recovers
— by design. An agent moves it back to `pending` to hand control back.

---

## 6. Reference implementation

FastAPI, matching your existing structure. The key shape is **verify → dedupe →
return `200` immediately → do the slow work in the background**, which your current
Twilio handler already follows.

```python
import json
import logging
import time
from fastapi import APIRouter, BackgroundTasks, Request, Response

log = logging.getLogger(__name__)
router = APIRouter()

# Delivery IDs seen recently. Chatwoot retries 3x at 3s intervals, so a few minutes
# of memory is plenty. Use Redis if you run more than one instance -- an in-process
# set gives you no protection when the retry lands on a different pod.
_SEEN: dict[str, float] = {}
_SEEN_TTL = 600


def _claim_delivery(delivery_id: str | None) -> bool:
    """True if this delivery is new. No id -> cannot dedupe -> process it."""
    if not delivery_id:
        return True
    now = time.time()
    for key, seen_at in list(_SEEN.items()):
        if now - seen_at > _SEEN_TTL:
            del _SEEN[key]
    if delivery_id in _SEEN:
        return False
    _SEEN[delivery_id] = now
    return True


@router.post("/webhooks/whatsapp")
async def chatwoot_webhook(request: Request, background: BackgroundTasks) -> Response:
    raw = await request.body()

    if not verify_signature(
        raw,
        request.headers.get("X-Chatwoot-Signature"),
        request.headers.get("X-Chatwoot-Timestamp"),
    ):
        return Response(status_code=401)

    delivery_id = request.headers.get("X-Chatwoot-Delivery")
    if not _claim_delivery(delivery_id):
        log.info("duplicate delivery %s ignored", delivery_id)
        return Response(status_code=200)

    payload = json.loads(raw)

    # Only message_created drives the agent. Everything else is acknowledged and
    # dropped -- see section 3, one customer message fans out to three deliveries.
    if payload.get("event") != "message_created":
        return Response(status_code=200)

    if not _should_reply(payload):
        return Response(status_code=200)

    # Return 200 now; Gemini is far slower than the CRM's 5s timeout.
    background.add_task(handle_customer_message, payload)
    return Response(status_code=200)


def _should_reply(payload: dict) -> bool:
    """The section 5.3 decision table, in one place.

    Customers are identified by message_type, NOT sender.type -- Chatwoot omits
    `type` entirely for contacts, so `sender.get("type") == "contact"` is always
    False and would silence the bot completely.
    """
    if payload.get("private"):
        return False                                    # internal agent note
    if payload.get("message_type") != "incoming":
        return False                                    # our own echo, or a human's reply
    if payload.get("conversation", {}).get("status") != "pending":
        return False                                    # a human owns this conversation
    return True
```

`handle_customer_message` then runs your existing agent and posts the reply back.
The three CRM calls it needs are specified in the main spec:

- **Reply** — `POST {CRM}/api/v1/accounts/1/conversations/{id}/messages` (§5.4)
- **Handoff / interrupt** — `POST …/toggle_status` with `{"status": "open"}` (§5.5)
- **Race guard** — re-read the conversation immediately before sending and discard
  the reply if it is no longer `pending` (§5.6)

All three take the header `api_access_token: <AGENT_BOT_TOKEN>`.

**Do not skip §5.5 and §5.6.** Chatwoot's built-in "a human replied, stand down"
behaviour is gated on its own Captain AI and will *not* fire for a generic agent bot.
If you don't implement the interrupt yourself, your bot will talk over your human
agents, and that is the single most visible way this integration fails in a demo.

---

## 7. Testing before the CRM is involved

You do not need the CRM to develop against. Sign a request yourself:

```python
import hashlib, hmac, json, time, requests

SECRET = "…CHATWOOT_BOT_SECRET…"
URL = "http://localhost:8080/aeon360-customer-waba/webhooks/whatsapp"

payload = {
    "event": "message_created",
    "id": 1,
    "content": "hi, do you have the Aeon X in blue?",
    "message_type": "incoming",
    "content_type": "text",
    "private": False,
    "source_id": "SMtest001",
    "sender": {"id": 1, "name": "Test Customer", "phone_number": "+60123456789"},
    "inbox": {"id": 1, "name": "AEON360 Whatsapp"},
    "account": {"id": 1, "name": "AEON360"},
    "conversation": {"id": 1, "inbox_id": 1, "status": "pending",
                     "channel": "Channel::TwilioSms", "can_reply": True},
}

body = json.dumps(payload).encode()
ts = str(int(time.time()))
sig = hmac.new(SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()

print(requests.post(URL, data=body, headers={
    "Content-Type": "application/json",
    "X-Chatwoot-Signature": f"sha256={sig}",
    "X-Chatwoot-Timestamp": ts,
    "X-Chatwoot-Delivery": "test-delivery-001",
}).status_code)
```

Worth asserting in your own tests:

- Correct signature → `200`
- Tampered body, same signature → `401`
- Timestamp 10 minutes old → `401`
- Same `X-Chatwoot-Delivery` twice → `200` both times, **one** reply generated
- `"status": "open"` → `200`, **no** reply generated
- `"private": true` → `200`, no reply
- `"message_type": "outgoing"` with `sender.type: "user"` on a `pending`
  conversation → conversation flipped to `open` (§5.5)

---

## 8. What to delete, and when

- The Twilio form parsing and `X-Twilio-Signature` validation in this handler.
- `gateway_twilio.py` as the production adapter — keep it for local testing if useful.
- `TWILIO_*` environment variables.

**Timing:** the demo number `+16823993949` is disposable, so you can replace the
Twilio path outright rather than running both in parallel. If you would rather keep
a fallback during the first day, branch on `Content-Type` — JSON to the new handler,
form-encoded to the old one — and delete the old branch once the acceptance tests in
§9 of the main spec pass.

---

## 9. Definition of done

- [ ] Valid signature accepted; bad, missing, and stale ones rejected with `401`
- [ ] HMAC computed over raw bytes, never a re-serialised dict
- [ ] Duplicate `X-Chatwoot-Delivery` produces exactly one reply
- [ ] Non-`message_created` events acknowledged with `200` and ignored
- [ ] `200` returned well inside 5 seconds; generation happens in the background
- [ ] Transient failures return `500`, never `403`/`502`/`503`
- [ ] Customer detected by `message_type == "incoming"`, not `sender.type`
- [ ] Bot silent whenever `conversation.status != "pending"`
- [ ] Human reply on a `pending` conversation flips it to `open` (§5.5)
- [ ] Reply discarded if status changed during generation (§5.6)
- [ ] Replies posted to the CRM, not Twilio — you never call Twilio again

Then run the 12 acceptance tests in §9 of the main spec. Test 7 — an agent replying
while the AI is mid-generation — is the one most likely to be silently broken, so
test it deliberately rather than assuming.
