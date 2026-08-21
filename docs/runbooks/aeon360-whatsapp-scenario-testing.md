# AEON360 — WhatsApp Scenario — Testing Guide

How to test the AEON360 assistant on WhatsApp end-to-end, now that it runs
**through the CRM**. Every message a customer sends and every answer the AI
gives is a row in Chatwoot, and a human agent can take the conversation over
mid-sentence.

> **This supersedes the persona deep links in
> `apac-aeon360-foundry-prototype/docs/whatsapp/whatsapp-scenario-testing.md`.**
> Same phone number, different destination: the Twilio Sender was repointed at
> Chatwoot on 2026-08-21, so `+16823993949` no longer reaches
> `aeon360-backend`. The `[sarah]` / `[uncle-tan]` slugs are not token-shaped
> (`src/identity.py` requires `v1.<b64url>.<b64url>`), so they are not even
> stripped — they reach the model as ordinary text and identify nobody.

## Live environment

| | |
|---|---|
| **Sender** | `+16823993949` (Twilio, production) |
| **Twilio Sender webhook** | `https://aeon360.crm.34-50-103-151.nip.io/twilio/callback` — Chatwoot's own inbound |
| **CRM** | Chatwoot, tenant `aeon360`, account 1, inbox 1 "AEON360 Whatsapp" (`Channel::TwilioSms`) |
| **Agent bot** | id 1, "AEON360 Assistant" → `https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot` |
| **Brain** | `aeon360-customer-waba` → `aeon360-customer-agent`, Cloud Run `asia-southeast1`, project `prj-dev-innovation-svc-8e` |
| **Reverse proxy** | Caddy **2.10.2, pinned** — 2.11.4 silently drops `api_access_token` and breaks every reply |
| **Turn latency** | 5–45 s. Do not judge a test at 10 seconds. |

Health checks:

```bash
curl -o /dev/null -w '%{http_code}\n' https://aeon360.crm.34-50-103-151.nip.io/api          # 200
curl -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{}' \
  https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot                     # 401 = alive, verifying
```

`401` on that second one is the healthy answer: the route exists and rejected an
unsigned delivery.

## The one thing that changed

There is **no reply path back through Twilio any more.** The AI posts to
`POST /api/v1/accounts/1/conversations/{id}/messages` and *Chatwoot* delivers it
over the Twilio channel it owns. One write produces both the CRM record and the
WhatsApp message.

You can see it on any AI reply — `source_id` is a Twilio SID:

```
sender=AgentBot#1  private=false  status=read  source_id="SMb61eba03444efbd6965b45cdc1d5eb6d"
```

## Quick start (30 seconds)

1. Make sure the conversation is **`pending`** in the CRM (that is the flag that
   says "the bot owns this"). A brand-new conversation starts `pending`.
2. Send any message from WhatsApp to `+16823993949`. **No keyword or prefix is
   needed** — the AI answers any message on a `pending` conversation.
3. Wait up to 45 s. The reply lands on your handset *and* in the CRM thread.

**Do not click around in the CRM while a turn is running** — see
[Troubleshooting](#troubleshooting).

## Entry links (the "pre-text")

Two kinds, and the difference is a security boundary.

### Personalised — identifies the member

`POST /entry-link` mints a `wa.me` link whose pre-filled text carries a signed
entry token, so the AI knows who is writing before they type anything:

```bash
curl -X POST https://innovation.dev.aeon360.net/aeon360-customer-waba/entry-link \
  -H "Authorization: Bearer $NUDGE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"member_key":"M-1042","sku_code":"MPK-XL-40","reason":"restock"}'
```

```json
{ "url": "https://wa.me/16823993949?text=Hi%21%20...%20%5Bv1.eyJ...%5D",
  "expires_in_hours": 72 }
```

The tag is stripped by `parse_entry_token` before the model sees it — only the
human sentence reaches the agent.

> **This URL is a credential.** For 72 hours it authenticates its holder as that
> member, with no second factor. It is only safe on a surface addressed to one
> known person: an email, an SMS, a push, a button inside a logged-in account.
> **Never on a QR code, a poster, packaging, or a shared page** — every scanner
> would be served that member's order history.

### Generic — no token

For public surfaces, use a plain link with no tag:

```
https://wa.me/16823993949?text=Hi%2C%20saya%20nak%20tanya%20pasal%20barang%20saya
```

The customer is then identified the normal way — the phone-number ladder in
`resolve_member` (bound session → `member.directory` → `default_member_key`).

## Scenario scripts

The assistant is a live agent, so exact wording varies — verify the **behaviour**.

### A. Cold start, no link (the baseline)

| You send | What to verify |
|---|---|
| `mau beli mamypoko` | A reply within ~45 s that is **member-aware** — it should reference real purchase history, e.g. *"anda pernah beli MamyPoko Extra Dry Tape saiz XL (40 keping) … bulan Mac lepas"*. Not a generic catalogue answer. |
| `yes` | Continues the thread coherently — the thread id is held per conversation, so context carries. |

Proves: identity resolves from the phone number alone on the Chatwoot path, and
the reply reaches both the handset and the CRM.

### B. Personalised entry link

| You send | What to verify |
|---|---|
| a `/entry-link` URL, then **Send** the pre-filled text | Greeting reflects **that** member, not the phone's default. The `[v1...]` tag must **not** appear in the CRM message body shown to the agent's model — it is stripped. |
| any follow-up | Stays bound to the same member for the session. |

Also try an **expired or tampered** token (change one character): it must fall
through silently to the phone-number ladder and still answer — never an error to
the customer, never a hint that the token was rejected.

### C. Human takeover (the one most likely to break)

| Step | What to verify |
|---|---|
| Customer sends a question | Conversation is `pending`, AI starts generating |
| **While it is thinking**, an agent types a public reply in the CRM | AI is cancelled. Conversation flips to `open`. The customer gets the human's message and **no AI message afterwards**. |
| Customer sends another message | AI stays silent — `open` means a human owns it. |

This is spec §9 acceptance test 7. Note the narrow trigger: only a **public
outgoing message from a user** interrupts.

### D. Things that must *not* interrupt

| Action | Expected |
|---|---|
| Agent writes a **private note** | AI keeps going and still replies. Deliberate — notes are for internal context. |
| Conversation is **assigned** to an agent | No effect. Verified live: conversation 3 was assigned to a human and the bot still answered. |
| A label is added | No effect. |

### E. Handoff on request

| You send | What to verify |
|---|---|
| `saya nak cakap dengan manusia` | Customer gets *"Let me get a colleague to help you — one moment."*, conversation flips to `open`, AI stops. |
| `what's the weather?` | Graceful out-of-scope redirect. Conversation stays `pending`. |

The trigger list is deliberately narrow (`HANDOFF_PHRASES`) — a false positive
silences the bot, which is worse than a miss.

### F. Hand-back (§5.8)

| Step | What to verify |
|---|---|
| After a takeover, agent sets the conversation back to **Pending** | The next customer message is answered by the AI again. |

If it stays silent, the bot is latched — see Troubleshooting.

## What to check in the CRM

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='
sudo docker exec aeon360-chatwoot-rails bundle exec rails runner "
c = Conversation.find_by(display_id: 3)
puts %(status=#{c.status} assignee=#{c.assignee&.name.inspect})
c.messages.order(:id).last(8).each { |m|
  puts %([#{m.id}] #{m.message_type} #{m.sender_type}##{m.sender_id} priv=#{m.private} #{m.content.to_s[0,70]})
}"'
```

Read it as: `message_type` (incoming = customer, outgoing = us), `sender_type`
(`AgentBot` = the AI, `User` = a human agent), `private` (a note, invisible to
the customer), and `status` on the conversation — the ownership flag.

## Offline testing (no phone, no CRM)

The whole decision table is covered by the suite in the WABA repo:

```bash
cd ~/Archive/aeon360-customer/my-aeon360-customer-waba
uv run pytest -q
```

Most relevant: `tests/crm/test_service.py::TestAcceptanceTest7` (the interrupt
race), `tests/crm/test_events.py` (the §5.3 decision table),
`tests/api/test_entry_link.py` (deep-link round trip),
`tests/agent/test_entry_token.py` (wire-format pin against the agent repo).

To exercise the deployed bot without messaging a real customer, fire a signed
delivery at a **nonexistent** conversation id and read the result in their logs:

```bash
export CHATWOOT_BOT_SECRET=...   # §7 of the credentials spec
python3 deploy/scripts/aeon360-bot-probe.py \
  --url https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot \
  --conversation-id 999999 --content "probe"

gcloud logging read 'resource.type="cloud_run_revision"
  AND resource.labels.service_name="aeon360-customer-waba"' \
  --project prj-dev-innovation-svc-8e --limit 25 --format='value(textPayload)'
```

A healthy result is `post_message failed: 404 {"error":"Resource could not be
found"}` — the bot authenticated, and Chatwoot correctly reported the fake
conversation missing. A `401` means authentication is broken again.

## Troubleshooting

- **No AI reply, and you clicked in the CRM just before sending.** Most likely
  cause today. Reopening or resolving a conversation makes Chatwoot emit several
  deliveries in one second, passing through `open` before settling on `pending`;
  a sibling delivery overwrites the bot's status cache mid-turn and the guard
  drops a reply that was ours to send. Look for
  `reply_dropped conversation_id=N local_status=open` in their logs. Fixed by the
  race-guard patch (branch `fix/crm-race-guard-remote-authoritative`); until it
  ships, **leave the conversation in `pending` and don't touch it**.

- **AI goes quiet ~10 minutes after the customer stops typing.** That was our
  own lifecycle scanner: its idle warning posts as an outgoing `User` message,
  which their decision table reads as a human takeover. Fixed by
  `LIFECYCLE_SKIP_PENDING=true` on the aeon360 tenant (live since 2026-08-21) —
  the scanner now sweeps only `open`. If it recurs, check the flag survived a
  redeploy: `docker exec aeon360-agent python -c "from app.config import
  get_settings; print(get_settings().lifecycle_skip_pending)"`.

- **Bot permanently silent on one conversation.** It is latched: a
  `toggle_status` failed, so the conversation is marked an unconfirmed interrupt
  and every `pending` payload is ignored by design. Clear it with the §5.8
  hand-back — set the conversation to **Open**, then back to **Pending**. A
  revision restart clears all of them.

- **`401` from Chatwoot with body `{"errors":["You need to sign in..."]}`.**
  That is Devise's, not Chatwoot's — it means **no** `api_access_token` header
  arrived, not a bad token. Check Caddy is still on 2.10.x
  (`sudo docker exec platform-infra-caddy-1 caddy version`); 2.11.4 silently
  drops underscore headers. A genuinely wrong token gives
  `{"error":"Invalid Access Token"}` instead.

- **Reply reaches the CRM but not the handset.** A Chatwoot/Twilio delivery
  problem, not an AI one. Check the message's `status` and `source_id`: no
  `source_id` means Chatwoot never handed it to Twilio.

- **Generic answer instead of a member-aware one.** Bindings live in process
  memory under `--max-instances=1`, so a restart drops them. The ladder should
  re-resolve from `conversation.meta.sender.phone_number` — if it does not, check
  the number's format against `member.directory` (`whatsapp:+E164`).

- **Nothing in the CRM at all.** Twilio is not reaching Chatwoot. Verify the
  Sender webhook is `https://aeon360.crm.34-50-103-151.nip.io/twilio/callback` —
  note this is governed by the **Sender**, not the number's `SmsUrl`.

## Related docs

- [`aeon360-whatsapp-cutover.md`](./aeon360-whatsapp-cutover.md) — the cutover
  runbook, the full 2026-08-21 diagnosis, and rollback
- `docs/superpowers/specs/2026-08-19-aeon360-whatsapp-chatwoot-integration-spec.md`
  — the integration spec, incl. §9 acceptance tests
- `my-aeon360-customer-waba/docs/chatwoot-mapping.md` — their decision log (D2 is
  retracted; see the race-guard patch)
- `apac-aeon360-foundry-prototype/docs/whatsapp/whatsapp-scenario-testing.md` —
  the pre-CRM prototype guide, superseded for this number
