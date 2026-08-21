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

**FINAL DIAGNOSIS 2026-08-21 — Caddy strips request headers containing
underscores. The bug is ours, not AEON360's.**

Two earlier readings of this 401 were wrong and are recorded here because each
was wrong in an instructive way:

1. *"Stale or mistyped token."* Wrong — their `bot_token` fingerprints to
   `ae48f0823de7`, byte-identical to `AgentBot.find(1).access_token.token`.
2. *"Token absent from their config."* Wrong — it is present in Secret Manager
   version 2 (created `2026-08-20T00:40:04`, i.e. **before** the live revision
   `aeon360-customer-waba-00003-dxm` was created at `00:42:27`, so the running
   process has it). Their `CrmSettings._require_credentials_when_enabled`
   validator refuses to boot with an empty token, and the service boots and
   serves `/chatwoot/bot` — which alone disproves the theory.

The header is correct in their code too (`src/crm/client.py:61` returns
`{"api_access_token": self._settings.bot_token}` — exactly what Chatwoot's API
docs specify). It never arrives.

**Proof.** Same request, two spellings, through the production URL:

```
api_access_token:  → 401 {"errors":["You need to sign in or sign up before continuing."]}
api-access-token:  → 200 {"meta":{"sender":{...}}}
```

Direct to Rails on `localhost:3000`, bypassing Caddy, **both** spellings return
`200`. So Rack normalises them identically and Chatwoot is indifferent; the loss
happens in the proxy. Confirmed on the wire with `tcpdump` on the Caddy→Rails
bridge, using neutral throwaway headers so the result cannot be Chatwoot-specific:

| Sent by client | Reached Rails |
|---|---|
| `api_access_token: …` | ❌ dropped |
| `api-access-token: …` | ✅ as `Api-Access-Token` |
| `x_custom_probe: …` | ❌ dropped |
| `x-custom-probe: …` | ✅ as `X-Custom-Probe` |

Caddy `v2.11.4` (`caddy:2-alpine`). No `header` directive in
`/etc/caddy/tenants/aeon360.caddy` does this — it is Caddy's own strict header
handling, and it applies to **every tenant and every integration behind this
proxy**, not just AEON360.

This also explains the Devise error body rather than Chatwoot's own. With the
header gone, `api/base_controller.rb`'s `authenticate_by_access_token?` fails
`.present?`, skips the access-token branch entirely and falls through to
`authenticate_user!`:

```ruby
before_action :authenticate_access_token!, if:     :authenticate_by_access_token?
before_action :authenticate_user!,         unless: :authenticate_by_access_token?
```

An unknown token yields `{"error":"Invalid Access Token"}`; a *missing* one
yields Devise's `"You need to sign in"`. AEON360's logs carry the second. That
distinction is what finally separated "wrong credential" from "no credential",
and it is worth remembering — the two failures look identical in a status code.

Their agent was never at fault: the Cloud Run logs show `agent turn ok`
immediately before each failed write. The answer is composed and then discarded.

**Verified fix.** Posting as the agent bot through Caddy with the dashed header,
private note so nothing reaches the customer:

```
POST /api/v1/accounts/1/conversations/3/messages
  api-access-token → 200 {"id":31,...,"status":"sent"}
  api_access_token → 401 {"errors":["You need to sign in..."]}
```

Two ways to fix, in preference order:

- **Ours (durable).** Stop Caddy discarding underscore headers, so every current
  and future integration that follows Chatwoot's documented contract works. This
  is the real fix; nothing else here is.
- **Theirs (one character, deployable today).** `src/crm/client.py:61` →
  `{"api-access-token": self._settings.bot_token}`. Standards-correct: HTTP
  header names conventionally use hyphens, and Rack maps both to
  `HTTP_API_ACCESS_TOKEN`.

Do the second to unblock the cutover, then the first so the trap is gone.

**Independent second defect, unchanged.** `crm.base_url` in their config is
`http://aeon360.crm.34-50-103-151.nip.io`. Caddy serves port 80 without
redirecting, so the token will cross the public internet in cleartext on every
reply once it is being sent. Their own `client.py:31-34` already carries a
warning about exactly this. Switch to `https://` in the same change.

**Consequence for their `docs/chatwoot-mapping.md` D2.** They concluded from a
401 on `GET /conversations/{1,7}` that "agent bots are not documented as having
read access to conversations at all", and narrowed the §5.6 race guard to local
status only. False — `conversations#show` is on Chatwoot's
`BOT_ACCESSIBLE_ENDPOINTS` allowlist and returns 200 once the header survives
the proxy. Verified with the real bot token:

```
GET /conversations/3       real token, dashed header → 200
GET /conversations/999999  real token                → 404 Resource could not be found
GET /conversations/3       bogus token               → 401 Invalid Access Token
```

The full §5.6 guard is available to them and the narrowing can be reverted.

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

## 3.2 RESOLVED 2026-08-21 09:05 UTC — Caddy pinned to 2.10

`platform-infra-caddy-1` now runs `caddy:2.10-alpine`, pinned in
`deploy/docker-compose.infra.yml` (and on the VM). `caddy:2-alpine` and
`caddy:latest` both still resolve to the broken 2.11.4, so the pin is the only
fix available today; upstream has milestoned it for v2.11.5.

Verified in three independent ways:

1. **Isolated version matrix.** A throwaway Caddy on a spare port, with a
   neutral control header so the result could not be Chatwoot-specific:

   ```
   caddy:2.8-alpine  (v2.8.4)  -> underscore=[UVAL] dashed=[DVAL]
   caddy:2.9-alpine  (v2.9.1)  -> underscore=[UVAL] dashed=[DVAL]
   caddy:2.10-alpine (v2.10.2) -> underscore=[UVAL] dashed=[DVAL]
   caddy:2.11-alpine (v2.11.4) -> underscore=[]     dashed=[DVAL]
   ```

2. **Production URL, before vs after.** The body is the tell, not the code:

   ```
   before:  api_access_token -> 401 {"errors":["You need to sign in or sign up before continuing."]}
   after:   api_access_token -> 401 {"error":"Invalid Access Token"}
   ```

   Still a 401 because the probe sent a placeholder value — but it is now
   *Chatwoot's* 401, which means the header arrived and was evaluated.

3. **AEON360's own service, unchanged.** A signed probe on a nonexistent
   conversation, read from their Cloud Run logs:

   ```
   before:  chatwoot post_message failed: 401 '{"errors":["You need to sign in..."]}'
   after:   chatwoot post_message failed: 404 '{"error":"Resource could not be found"}'
   ```

   **401 → 404 is the proof.** The bot authenticates; Chatwoot then correctly
   reports that conversation `777777` does not exist. Against a real
   conversation this is a `200`.

**AEON360 needs no code change for this.** Their `api_access_token` spelling was
correct all along. The only item still outstanding on their side is switching
`crm.base_url` from `http://` to `https://` — Caddy serves port 80 without
redirecting, so the bot token now crosses the public internet in cleartext on
every reply. Their own `client.py:31-34` already warns about it.

**Regression check after the pin.** All tenant vhosts still serve
(`aeon360.crm` 200, `proton.crm` 200 over TLS; `default.crm` and `*.agent` fail
over TLS but never had certificates — only `aeon360.crm` and `proton.crm` appear
in `/data/caddy/certificates`, so that predates this change).

**Trade-off, deliberately accepted.** Allowing underscore header names back
re-opens header smuggling against Rack: Rack folds `-` and `_` into the same CGI
variable, so a client-supplied `X_Forwarded_For` reaches `HTTP_X_FORWARDED_FOR`
beside the one Caddy sets and can win on ordering. This is not a new exposure —
the floating tag sat on 2.10.x for the platform's whole life until 2026-08-19 —
but since 2.10 is now a deliberate choice, a `strip_underscore_forwarding`
snippet is defined in `caddy/Caddyfile` (live on the VM, `caddy validate` passes)
and wired into `add-tenant.sh` for new tenants.

**DONE 2026-08-21 09:34.** The snippet is imported into all six site blocks
that front Chatwoot (`aeon360`, `proton`, `default` — http and TLS vhosts),
config validated, Caddy reloaded live.

One trap worth recording: the snippet must be defined **before**
`import tenants/*.caddy` in `caddy/Caddyfile`. Caddy resolves snippets in file
order, so appending the definition at the end makes every tenant import fail to
adapt with `File to import not found: strip_underscore_forwarding` — it reads as
a missing *file*, not a mis-ordered snippet. Also: `caddy/Caddyfile` is a
**single-file** bind mount, so rewrite it with `cp` (truncates, keeps the inode)
rather than `sed -i`, which swaps the inode and never reaches the container.

Verified on the wire — spoofed forwarding headers dropped, benign underscore
headers still delivered, and Rails sees only Caddy's own value:

```
X_Forwarded_For: 9.9.9.9      reached rails: 0
X_Real_IP: 8.8.8.8            reached rails: 0
x_custom_probe: SHOULDARRIVE  reached rails: 1
rails saw -> X-Forwarded-For: 114.10.76.111   (the real client)
             X-Forwarded-Proto: https
```

`api_access_token` still authenticates through the proxy after the reload
(`{"error":"Invalid Access Token"}` on a placeholder = Chatwoot's own answer,
not Devise's), and `aeon360.crm` / `proton.crm` both still serve 200.

Rollback: `/opt/platform/deploy/docker-compose.infra.yml.bak-caddypin-20260821`,
`/opt/platform/deploy/caddy/Caddyfile.bak-underscore-20260821`, and
`/tmp/tenants.bak-import-20260821`.

---

## 3.2b CUTOVER WORKING END TO END — 2026-08-21 10:02 UTC

First real customer message to produce an AI reply through the CRM:

```
[44] incoming  Contact:3    10:01:25  "mau beli mamupokok"
[45] outgoing  AgentBot:1   10:02:01  "Untuk lampin MamyPoko, anda pernah beli
                                       MamyPoko Extra Dry Tape saiz XL (40 keping)
                                       sekali pada bulan Mac lepas. Adak…"
[46] incoming  Contact:3    10:03:19  "yes"
```

Message 45's metadata is the whole architecture in one row:

```
sender=AgentBot#1  private=false  status=read
source_id="SMb61eba03444efbd6965b45cdc1d5eb6d"
```

`source_id` is a **Twilio message SID**. The bot wrote once to
`POST /conversations/3/messages`; Chatwoot stored it *and* delivered it over the
Twilio channel it owns. One write, both outcomes — no mirroring, no second call
to Twilio, exactly as §3 of the spec says.

`status=read` means the customer received and opened it. 36 seconds end to end,
consistent with the 30–45s agent turn.

**Member identity survives the Chatwoot path.** This was the last unverified
risk: bindings and thread ids live in process memory under `--max-instances=1`,
and revision `00004-pwh` had started cold four minutes earlier, so nothing was
cached. The reply still names a specific prior purchase (MamyPoko Extra Dry Tape
XL, 40-pack, March), which means the member ladder re-resolved from
`conversation.meta.sender.phone_number`. No degradation to a generic answer.

Note the conversation was `pending` **and assigned to a human** (Default Policy
re-assigned it at 09:31:08) and the bot still answered — assignment does not gate
the bot, only `status`. Their `decide()` treats a human *outgoing message* as the
takeover signal, nothing else.

---

## 3.3 Handover to AEON360 — one item left (https is DONE 2026-08-21 09:57)

**1. ✅ DONE 2026-08-21 09:57** — secret version 3, revision `aeon360-customer-waba-00004-pwh` at 100%. Their CRM calls now log `https://aeon360.crm...` where they logged `http://` an hour earlier. Original instructions kept for reference. Their config lives in Secret Manager secret
`aeon360-customer-waba-config` (project `prj-dev-innovation-svc-8e`), mounted at
`CONFIG=/secrets/config.yaml`. Line 29 is `base_url: "http://aeon360.crm..."`.
Now that the token is actually being transmitted, it crosses the public internet
in cleartext on every reply — Caddy answers port 80 without redirecting. Editing
this is classifier-blocked from here; run it directly:

```bash
P=prj-dev-innovation-svc-8e
gcloud secrets versions access latest --secret=aeon360-customer-waba-config --project=$P > /tmp/cfg.yaml
sed -i '' 's|http://aeon360\.crm|https://aeon360.crm|' /tmp/cfg.yaml
gcloud secrets versions add aeon360-customer-waba-config --data-file=/tmp/cfg.yaml --project=$P
# the mount resolves `latest` at instance start, so force a new revision:
gcloud run services update aeon360-customer-waba --region=asia-southeast1 --project=$P \
  --update-labels=cfg-rev=https-$(date +%s)
rm -P /tmp/cfg.yaml
```

The restart is a bonus: it also clears the in-memory status cache and bindings,
which is the fallback if a conversation is still latched.

**2. The §5.6 race-guard fix.** Implemented and tested in the local clone at
`~/Archive/aeon360-customer/my-aeon360-customer-waba`, branch
`fix/crm-race-guard-remote-authoritative` (commit `0651cb3`, **not pushed** —
it's their repo and their CI). 344 tests pass. Patch exported for handover.

It makes the CRM authoritative for the guard, keeping the local cache only for
the unconfirmed-interrupt window, and retracts D2 in their
`docs/chatwoot-mapping.md`. It also fixes the clobber that silenced the bot:
`handle()` writes the status from every delivery including mid-turn ones, so a
reopen's `open` payload could overwrite the cache a running turn's guard then
read.

Until they deploy it, a conversation can still be silenced by UI churn —
reopening or resolving a conversation right before the customer writes. Leaving
it in `pending` and not touching it avoids the race entirely.

---


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
