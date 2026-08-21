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
| Agent bot 1 `outgoing_url` | ✅ `/chatwoot/bot` — corrected 2026-08-21, verified persisted |
| Twilio Sender inbound URL | still AEON360 direct — **step 3** |

---

## 1. Correct the agent bot `outgoing_url` — CRM side, ours ✅ DONE 2026-08-21

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

SSH to the VM. **Do not use `--tunnel-through-iap`** — it fails with
`4033: 'not authorized'` (no `roles/iap.tunnelResourceAccessor` on this project).
The VM has an external IP (`34.50.103.151`), so connect directly:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a
```

**Read the current value first** (Rails boot takes ~30-60s):

```bash
docker exec aeon360-chatwoot-rails bundle exec rails runner \
  'AgentBot.all.each { |b| puts [b.id, b.name, b.outgoing_url].inspect }
   AgentBotInbox.all.each { |x| puts ["inbox", x.inbox_id, "bot", x.agent_bot_id].inspect }'
```

Observed 2026-08-21, before the fix:

```
[1, "AEON360 Assistant", "https://innovation.dev.aeon360.net/aeon360-customer-waba/webhooks/whatsapp"]
["inbox", 1, "bot", 1]
["inbox", 1, "AEON360 Whatsapp", "Channel::TwilioSms"]
```

Exactly as the spec described. If the bot id is not 1, use the real id below.

**Write:**

```bash
docker exec aeon360-chatwoot-rails bundle exec rails runner \
  'b = AgentBot.find(1)
   b.update!(outgoing_url: "https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot")
   puts b.reload.outgoing_url'
```

Expect the new URL echoed back; re-read in a fresh process to confirm it
persisted. No restart needed — `outgoing_url` is read per delivery.

Applied 2026-08-21 and verified: bot 1 now reads
`https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot`, and the
step 2 probe returns `401`.

This was safe to do ahead of the cutover window precisely because Twilio still
points at AEON360 direct — the CRM generates no agent-bot events at all, so
nothing reads `outgoing_url` until step 3 flips the Sender.

> Do **not** edit this through a tenant env file. It lives in the Chatwoot
> database, not in config.

---

## 2. Confirm the bot endpoint is reachable from the CRM's side of the network ✅ `401` 2026-08-21

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

## 3.1 Diagnosis 2026-08-21 — the bot accepts events and never replies

First live message after cutover (conversation 2, "hello") produced no AI reply,
and the conversation stayed `pending`. Chain verified link by link:

| Link | Result |
|---|---|
| Twilio → CRM | ✅ conversation created, `pending`, inbox 1 |
| CRM → `AgentBots::WebhookJob` | ✅ fired |
| `outgoing_url` | ✅ `/chatwoot/bot` |
| Our documented token + secret vs the live bot | ✅ SHA256 fingerprints identical |
| AEON360 endpoint, valid signature | ✅ `200` |
| AEON360 endpoint, bad signature | ✅ `401` (acceptance test 11 passes) |

So the CRM side is healthy and the event reaches them. The failure is **after
their 200 ack**, inside the background task — invisible to Chatwoot by design.

`aeon360-bot-probe.py` exposed it. Because the probe posts to conversation
`999999`, which does not exist, their reply attempt showed up in the CRM's own
request log:

```
REQ GET  /api/v1/accounts/1/conversations/999999          → 401 Unauthorized
REQ POST /api/v1/accounts/1/conversations/999999/messages → 401 Unauthorized
```

That proves their service receives the event, parses it, decides to answer, and
dies on the write back.

**Correction 2026-08-21 (second pass, with access to their GCP project).** The
first pass called this a *bad* token. It is not — the token is **absent**.
Chatwoot distinguishes the two, and the distinction is the whole diagnosis:

| What the client sends | Chatwoot's 401 body |
|---|---|
| an unknown `api_access_token` | `{"error":"Invalid Access Token"}` |
| **no** `api_access_token` header | `{"errors":["You need to sign in or sign up before continuing."]}` |

`api/base_controller.rb` is why:

```ruby
before_action :authenticate_access_token!, if:     :authenticate_by_access_token?
before_action :authenticate_user!,         unless: :authenticate_by_access_token?

def authenticate_by_access_token?
  request.headers[:api_access_token].present? || request.headers[:HTTP_API_ACCESS_TOKEN].present?
end
```

A missing **or empty** header fails `.present?`, skips the access-token branch
entirely, and falls through to Devise — which is why the message is Devise's,
not Chatwoot's. AEON360's own Cloud Run logs show exactly that body:

```
INFO  src.agent.client: agent turn ok thread_id=939476e9-…
INFO  httpx: GET  http://…/api/v1/accounts/1/conversations/999999          "401 Unauthorized"
WARN  src.crm.client: chatwoot get_status non-2xx for conversation 999999: 401
INFO  httpx: POST http://…/api/v1/accounts/1/conversations/999999/messages "401 Unauthorized"
ERROR src.crm.service: chatwoot_send_failed conversation_id=999999
src.exceptions.ChatwootApiError: chatwoot post_message failed: 401
  '{"errors":["You need to sign in or sign up before continuing."]}'
```

Reproduced against the live CRM with no credential at all — byte-identical:

```bash
curl -sS https://aeon360.crm.34-50-103-151.nip.io/api/v1/accounts/1/conversations/2
{"errors":["You need to sign in or sign up before continuing."]}
```

Note the first log line: **the AI answer is generated successfully**
(`agent turn ok`). Nothing is wrong with their model, prompt, member scoping or
BigQuery marts. The reply is composed and then thrown away on the write back.

**Where the value has to be set.** `aeon360-customer-waba` takes its whole
config from `CONFIG=/secrets/config.yaml`, mounted from Secret Manager secret
`aeon360-customer-waba-config` (project `prj-dev-innovation-svc-8e`, region
`asia-southeast1`, live revision `aeon360-customer-waba-00003-dxm`). The CRM bot
token is empty or unset in that secret. Check it without printing the value:

```bash
gcloud secrets versions access latest \
  --secret=aeon360-customer-waba-config \
  --project=prj-dev-innovation-svc-8e \
  | grep -iE 'token|secret' | awk -F': *' '{printf "%s len=%d\n", $1, length($2)}'
```

A `len=0` on the CRM bot token line confirms it. The correct value is in spec
§7; its fingerprint matches `AgentBot.find(1).access_token.token` exactly.
Deploying a new secret version requires a revision restart to pick up the mount.

**Second, independent defect: they call the CRM over plain `http://`.** Caddy
serves the API on port 80 without redirecting:

```
http  → 401   (no redirect)
https → 401
```

Today that leaks nothing because no token is being sent. The moment the token is
set, it crosses the public internet in cleartext on every reply. Their CRM base
URL must be `https://aeon360.crm.34-50-103-151.nip.io`, fixed in the same
change.

**Consequence for their `docs/chatwoot-mapping.md` D2.** They concluded from a
401 on `GET /conversations/{1,7}` that "agent bots are not documented as having
read access to conversations at all", and narrowed the §5.6 race guard to local
status only. That premise is false twice over: `conversations#show` is on
Chatwoot's `BOT_ACCESSIBLE_ENDPOINTS` allowlist, and the 401 they saw was the
missing header, not a permissions model. Confirmed against the live CRM with the
real bot token read from the database:

```
GET /conversations/2       real token   → 200
GET /conversations/999999  real token   → 404  Resource could not be found
GET /conversations/2       bogus token  → 401  Invalid Access Token
```

Once the header is sent, the **full** §5.6 guard is available and the narrowing
can be reverted.

Reproduce any of this with:

```bash
export CHATWOOT_BOT_SECRET=...
python3 deploy/scripts/aeon360-bot-probe.py \
    --url https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot
```

Then read the result from *their* side — the probe's conversation id is
deliberately nonexistent, so their failed write is easy to find:

```bash
gcloud logging read 'resource.type="cloud_run_revision"
  AND resource.labels.service_name="aeon360-customer-waba"' \
  --project prj-dev-innovation-svc-8e --limit 25 --format='value(textPayload)'
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
