# Runbook — AEON360 WhatsApp cutover to the CRM

**Date:** 2026-08-21
**Owner:** Yuda (CRM side) · AEON360 backend engineer (Twilio Sender side)
**Spec:** `docs/superpowers/specs/2026-08-19-aeon360-whatsapp-chatwoot-integration-spec.md`

Moves the production WhatsApp number `+1 682 399 3949` off the direct
AEON360 webhook and onto Chatwoot, so every conversation lands in the CRM with
history and human handoff.

> **There is no sandbox rehearsal.** The Twilio sandbox number `+1 415 523 8886`
> is retired. Step 3 is the first real test, on a live number, with a real
> handset. Do it in a low-traffic window with the rollback (§4) already typed.

---

## 0. Where things stand

| Piece | State |
|---|---|
| CRM `https://aeon360.crm.34-50-103-151.nip.io` | live, Let's Encrypt (expires 2026-11-17) |
| CRM `POST /twilio/callback` | live, `204` |
| Chatwoot inbox 1 "AEON360 Whatsapp" | created, agent bot 1 attached |
| WABA `/aeon360-customer-waba/chatwoot/bot` | live, `crm_enabled: true`, `401` unsigned |
| WABA `/aeon360-customer-waba/webhooks/whatsapp` | live, Twilio-only — **the rollback path** |
| Agent bot 1 `outgoing_url` | **step 1 below** |
| Twilio Sender inbound URL | still AEON360 direct — **step 3** |

---

## 1. Correct the agent bot `outgoing_url` — CRM side, ours

The spec originally registered the bot against `/webhooks/whatsapp`. AEON360 built
`/chatwoot/bot` instead, and `/webhooks/whatsapp` never grew a Chatwoot branch.
Verified 2026-08-21:

```
POST /webhooks/whatsapp  (application/json)  → 403   ← Twilio verifier
POST /chatwoot/bot       (application/json)  → 401   ← Chatwoot verifier
```

Chatwoot retries only on `429`/`500` and drops `403` permanently, so leaving this
wrong loses every message with **no error visible on either side** — conversations
just fall to a human.

SSH to the VM:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --tunnel-through-iap
```

**Read the current value first** (Rails boot takes ~30-60s):

```bash
docker exec aeon360-chatwoot-rails bundle exec rails runner \
  'AgentBot.all.each { |b| puts [b.id, b.name, b.outgoing_url].inspect }
   AgentBotInbox.all.each { |x| puts ["inbox", x.inbox_id, "bot", x.agent_bot_id].inspect }'
```

Expect `[1, "AEON360 Assistant", ".../aeon360-customer-waba/webhooks/whatsapp"]`
and `["inbox", 1, "bot", 1]`. If the bot id is not 1, use the real id below.

**Write:**

```bash
docker exec aeon360-chatwoot-rails bundle exec rails runner \
  'b = AgentBot.find(1)
   b.update!(outgoing_url: "https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot")
   puts b.reload.outgoing_url'
```

Expect the new URL echoed back. No restart needed — `outgoing_url` is read per
delivery.

> Do **not** edit this through a tenant env file. It lives in the Chatwoot
> database, not in config.

---

## 2. Confirm the bot endpoint is reachable from the CRM's side of the network

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' -d '{}' \
  https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot
```

`401` is the pass — the signature verifier is live and failing closed. A `403`
means the URL is still the Twilio route; a `404` means AEON360 moved it again.

---

## 3. Repoint the production Twilio Sender — AEON360 side

Point the inbound webhook for `+1 682 399 3949` at:

```
https://aeon360.crm.34-50-103-151.nip.io/twilio/callback
```

> **Edit the Sender, not the phone number.** WhatsApp routing is governed by
> `/v2/Channels/Senders/{XE...}`, *not* the number's `SmsUrl`. Editing `SmsUrl`
> looks successful and changes nothing — this has already bitten the WABA repo
> once (`docs/build-record.md` there).

Then run the spec §9 acceptance tests. Test 7 (a human replies while the AI is
still generating) is the one most likely to be silently broken — test it
deliberately.

Watch during the window:

```bash
docker logs -f aeon360-chatwoot-rails 2>&1 | grep -i 'twilio\|agent_bot'
```

---

## 4. Rollback

One change, AEON360 side. Point the Twilio **Sender** back at:

```
https://innovation.dev.aeon360.net/aeon360-customer-waba/webhooks/whatsapp
```

Works only while that route still exists. **Do not let AEON360 delete it** until
production has run stably on the CRM for an agreed period — with the sandbox
retired it is the only rollback there is.

Our `outgoing_url` change (step 1) does not need reverting: with Twilio pointed
away from the CRM, no Chatwoot events are generated at all.

---

## 5. After cutover is confirmed stable

- AEON360 deletes `/webhooks/whatsapp`, `gateway_twilio.py` as the production
  adapter, and their `TWILIO_*` env vars (spec §5.9).
- Yuda creates CRM accounts for the AEON360 team (spec §10) — with no sandbox,
  reading the CRM is the only way they can see what a message actually did.
- Revisit the in-memory binding store: bindings, thread ids and the MessageSid
  dedupe set live in process memory under `--max-instances=1`, so a deploy or a
  scale-to-zero silently reassigns members to `default_member_key` on a fresh
  thread. Fine for a demo, not for sustained member traffic.
