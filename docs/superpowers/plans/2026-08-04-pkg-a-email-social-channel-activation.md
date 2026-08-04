# Package A — Email + Facebook/Instagram Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get Facebook and Instagram inboxes connectable on the `proton` tenant, and close the two remaining email verification gaps.

**Architecture:** This package is configuration and operations, not application code — Chatwoot owns both channels. The only repository changes are documentation and a one-line Caddy change. Because there is no code to test, tasks end in **verification commands with expected output** rather than unit tests; a task is complete only when its verification is observed, not when the change is made.

**Tech Stack:** Caddy 2 (`deploy/caddy/Caddyfile`), Docker Compose on the `crm-ticketing` GCE VM, Chatwoot v4.15.1 super-admin console, Meta developer apps.

**Spec:** `docs/superpowers/specs/2026-08-04-pkg-a-email-social-channel-activation-design.md`

## Global Constraints

- **Never print, echo, log, or commit a credential.** App passwords, app secrets and verify tokens are entered directly into a UI or an env file on the VM. If a command would display one, mask it (`length(x)>0` style checks only).
- Meta credentials go in the **Chatwoot super-admin console**, never in env vars: `/super_admin/app_config?config=facebook` and `?config=instagram`.
- Inbound email IMAP lives **per-inbox in the Chatwoot database**, not in env. Outbound SMTP lives in tenant env. Never conflate them.
- Any env change requires `docker compose ... up -d <service>` (recreate). A plain `docker restart` does **not** re-read the env file.
- Every new env var must appear in **both** `deploy/docker-compose.tenant.yml` and `deploy/tenants/example.env`.
- Tenant is `proton` unless stated. VM access: `gcloud compute ssh crm-ticketing --zone=asia-southeast2-a`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/caddy/Caddyfile` | Modify: remove `auto_https off` so tenant hostnames get TLS certificates |
| `docs/analysis/crm-channel-interaction-guide.md` | Modify: record the real, tested state of email and FB/IG |
| `docs/analysis/crm-channel-ui-testing-guide.md` | Modify: update SM-1 and the email rows from ❌/⚠️ to their verified state |
| `docs/superpowers/specs/2026-08-04-pkg-a-email-social-channel-activation-design.md` | Modify: tick verification items as they pass |

No first-party application code changes in this package.

---

### Task 1: Close the two open email SOP tests

The email channel works, but two rules from Proton's SOP (`CRM Process Flow (1).xlsx`, Email tab) have never been exercised. Do this first — it is five minutes and it de-risks the client meeting.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-04-pkg-a-email-social-channel-activation-design.md` (§3.5.2 findings)

**Interfaces:**
- Consumes: nothing.
- Produces: a verified answer to "does a reply into a resolved thread re-send the greeting?", which Package G's acknowledgement-detection design depends on.

- [ ] **Step 1: Capture the current message baseline**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo docker exec platform-infra-postgres-1 psql -U postgres -d chatwoot_proton -A -F"|" -c "select max(id) from messages where inbox_id=4;"'
```

Record the number. Call it `BASELINE`.

- [ ] **Step 2: Test the in-thread reply rule**

From `yuda.adi.pratama@devoteam.com`, reply to the existing "Halo Testing Email ACK 2" thread. Wait 2 minutes (one IMAP poll cycle), then:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo docker exec platform-infra-postgres-1 psql -U postgres -d chatwoot_proton -A -F"|" -c "select id,conversation_id,message_type from messages where inbox_id=4 and id>BASELINE order by id;"'
```

Expected: exactly one new row, `message_type=0` (incoming), on **conversation 38**. **No `message_type=3` row.** If a `type=3` appears, the SOP rule "customer replies to the same email thread → no additional auto-reply" is violated — stop and record it as a defect.

- [ ] **Step 3: Test the reply-after-resolve rule (the risky one)**

In Chatwoot, resolve conversation 38. Then reply again to that same email thread from the same address. Wait 2 minutes, then re-run the query from Step 2 with the new baseline.

Expected (the rule holds): the message lands on conversation 38, which reopens, with **no new `message_type=3`**.
Failure mode to watch for: a **new conversation id** appears with its own `message_type=3` greeting — that re-sends the acknowledgement inside one email thread, which the SOP explicitly forbids.

- [ ] **Step 4: Record the outcome in the spec**

Replace the "Two SOP rules still untested" block in §3.5.2 with what was actually observed, including the conversation ids and message types. If either rule failed, add a "Defect" subsection stating the failing rule, the evidence, and that the fix belongs in Package G's scope.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-08-04-pkg-a-email-social-channel-activation-design.md
git commit -m "docs(spec): record in-thread and reply-after-resolve acknowledgement test results"
```

---

### Task 2: Decide and enable HTTPS on the tenant hostname

Nothing in the Facebook/Instagram half can start until this is done — Meta rejects `http://` for both the OAuth redirect and the webhook callback.

**Files:**
- Modify: `deploy/caddy/Caddyfile:1-5`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `https://proton.crm.<ip>.nip.io` (or a production domain), which Tasks 3-5 all require.

- [ ] **Step 1: Confirm ports 80 and 443 are reachable**

Let's Encrypt validates over both. Check the GCE firewall:

```bash
gcloud compute firewall-rules list --format='table(name,allowed[].map().firewall_rule().list(),sourceRanges.list())' | grep -E "80|443"
```

Expected: a rule allowing `tcp:80` and `tcp:443` from `0.0.0.0/0`. If absent, create one before continuing — ACME will fail silently-ish otherwise.

- [ ] **Step 2: Enable automatic HTTPS**

In `deploy/caddy/Caddyfile`, replace the global options block:

```caddyfile
{
	# nip.io hostnames with automatic Let's Encrypt certificates.
	# NOTE: nip.io is heavily used and LE rate limits are often already
	# exhausted for it. If issuance fails, the fallback is a real domain
	# (preferred long term) or a Cloudflare/ngrok tunnel for testing.
	email devotech29@gmail.com
}
```

Removing `auto_https off` is the actual change; the `email` directive gives ACME a contact and is required for production issuance.

- [ ] **Step 3: Deploy and watch certificate issuance**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='cd /opt/platform/deploy && sudo docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d caddy && sleep 30 && sudo docker logs platform-infra-caddy-1 --since 2m 2>&1 | grep -iE "certificate|obtain|error|rate" | tail -20'
```

Expected: `certificate obtained successfully`. If you see `too many certificates already issued` or `rateLimited`, **stop** — nip.io is exhausted. Revert the Caddyfile, and escalate the production-domain decision from spec §4.2; do not burn retries against the rate limit.

- [ ] **Step 4: Verify from outside the VM**

```bash
curl -sSI https://proton.crm.34-50-103-151.nip.io | head -3
```

Expected: `HTTP/2 200` (or a 302 to the login page) with **no** certificate warning.

- [ ] **Step 5: Sweep every URL that just changed**

Turning on TLS moves every registered URL. Check each and update to `https://`:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo grep -rhoE "http://[a-z0-9.-]+" /opt/platform/deploy/tenants/*.env | sort -u'
```

For every hit, decide whether it is internal (container-to-container, leave alone) or public (must become `https://`). Pay particular attention to `FRONTEND_URL`, the SPA's injected backend URL, and the Twilio webhook base — a missed Twilio URL breaks WhatsApp, which is the exact way this task causes a regression.

- [ ] **Step 6: Confirm WhatsApp still works**

Send a WhatsApp message to the proton number and confirm the bot replies. This is the regression gate for Step 5.

- [ ] **Step 7: Commit**

```bash
git add deploy/caddy/Caddyfile
git commit -m "feat(deploy): enable automatic HTTPS so Meta channels can be connected"
```

---

### Task 3: Create the Meta developer app

**Files:** none in the repository — this is work in Meta's console.

**Interfaces:**
- Consumes: the HTTPS hostname from Task 2.
- Produces: a Facebook App ID, App Secret, Instagram App ID, Instagram App Secret, and a self-chosen verify token, used by Task 4.

- [ ] **Step 1: Create the app**

At developers.facebook.com create a Business-type app. Add the **Messenger** product and the **Instagram** product.

- [ ] **Step 2: Attach the assets**

Connect a Facebook Page you administer, and an Instagram professional account linked to that Page. In Development mode the app can only exchange messages with Pages and users you administer — that is sufficient for testing and needs no Business Verification.

- [ ] **Step 3: Add testers**

Add Proton's accounts as testers on the app. **Do this before any demo** — in Development mode, a message from a non-tester account will silently never arrive, which will look like a platform failure during a live demo.

- [ ] **Step 4: Record where the credentials are, not the credentials**

Note in your own password manager which app the credentials belong to. Do not put them in the repository, a ticket, or chat.

---

### Task 4: Configure Meta credentials in the Chatwoot super-admin console

**Files:** none in the repository — runtime configuration stored in the Chatwoot database.

**Interfaces:**
- Consumes: credentials from Task 3, HTTPS from Task 2.
- Produces: enabled Facebook and Instagram cards in the inbox channel grid, required by Task 5.

- [ ] **Step 1: Fill the Messenger form**

Open `https://proton.crm.<ip>.nip.io/super_admin/app_config?config=facebook` and set **Facebook App ID**, **Facebook App Secret**, **Facebook Verify Token**, **Instagram Verify Token**. Leave **Enable human agent** as `False` — it needs additional Meta app approval and is irrelevant for testing. Submit.

- [ ] **Step 2: Raise the Facebook API version**

The same form defaults to `v18.0`, which dates from late 2023 and is very likely past Meta's deprecation window. Set it to a currently supported version (the Instagram form already defaults to `v22.0`, which is a reasonable reference point). **This is the single most likely cause of an unexplained failure in Task 5.**

- [ ] **Step 3: Fill the Instagram form**

Open `?config=instagram` and set **Instagram App ID**, **Instagram App Secret**, **Instagram Verify Token**. Submit.

- [ ] **Step 4: Register the callbacks on the Meta side**

In the Meta app, set the OAuth redirect URI and the webhook callback URL against the **HTTPS** tenant hostname, using the verify token entered in Step 1. Subscribe the Page to message events.

- [ ] **Step 5: Verify the channel cards are enabled**

Open `Settings → Inboxes → Add Inbox`. Expected: the **Facebook** and **Instagram** cards are no longer greyed out.

If they are still grey, the installation config is cached — recreate the tenant's Chatwoot services and re-check:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='cd /opt/platform/deploy && sudo docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d chatwoot-rails chatwoot-sidekiq'
```

---

### Task 5: Connect the inboxes and verify message flow both ways

**Files:**
- Modify: `docs/analysis/crm-channel-ui-testing-guide.md` (row SM-1)
- Modify: `docs/analysis/crm-channel-interaction-guide.md` (the Facebook/Instagram section)

**Interfaces:**
- Consumes: enabled channel cards from Task 4.
- Produces: verified social inboxes; updates the two guides that other people rely on.

- [ ] **Step 1: Connect the Facebook inbox**

`Settings → Inboxes → Add Inbox → Facebook`, authorize the Page, name it clearly (`Proton Facebook`, not `fb test` — vague inbox names were an explicit source of confusion in the 2026-07-28 demo). Add the demo agents.

- [ ] **Step 2: Verify inbound**

From a tester account, message the Page. Expected: a conversation appears in the Facebook inbox within seconds. Confirm in the database rather than trusting the UI:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo docker exec platform-infra-postgres-1 psql -U postgres -d chatwoot_proton -A -F"|" -c "select id,name,channel_type from inboxes order by id;"'
```

Expected: a new row with `channel_type = Channel::FacebookPage`.

- [ ] **Step 3: Verify outbound**

Reply from Chatwoot. Expected: the message arrives in Messenger.

- [ ] **Step 4: Repeat for Instagram**

Same three checks against an Instagram DM.

- [ ] **Step 5: Decide the bot behaviour deliberately**

Choose, per inbox, whether the AI agent-bot answers social messages or stays silent, and configure it. Do not leave this accidental — an unconfigured inbox silently defaults, and nobody will know which behaviour was intended.

- [ ] **Step 6: Update both guides with the tested state**

In `crm-channel-ui-testing-guide.md`, change row SM-1 from "❌ Blocked" to its verified state. In `crm-channel-interaction-guide.md`, replace the "blocked on Meta Business verification" claim with the accurate position: **testing works in Development mode; production still requires App Review and Business Verification.** Include the tester-account caveat from Task 3 Step 3.

- [ ] **Step 7: Commit**

```bash
git add docs/analysis/crm-channel-ui-testing-guide.md docs/analysis/crm-channel-interaction-guide.md
git commit -m "docs: record verified Facebook/Instagram channel state after activation"
```

---

### Task 6: Give the agent service its own bot identity

Found while debugging email (spec §3.5.4): the `agent` service authenticates as user 1 ("Yuda Adi"), so every automated message on every channel carries a real person's name — 59 such messages already exist on the WhatsApp inbox.

**Files:**
- Modify: `deploy/tenants/proton.env` (on the VM — the access token value only)
- Modify: `deploy/tenants/example.env` (documentation comment)

**Interfaces:**
- Consumes: nothing.
- Produces: automated Chatwoot messages attributed to a system identity rather than a person.

- [ ] **Step 1: Create the bot user in Chatwoot**

`Settings → Agents → Add Agent`, name `Proton e.MAS Centre`, role Administrator (the agent service needs account-level API access). Use a mailbox you control for the account.

- [ ] **Step 2: Generate that user's access token**

Log in as the new user, open Profile Settings, and copy the access token. **Do not print it anywhere.**

- [ ] **Step 3: Point the agent service at it**

On the VM, back up first, then replace the Chatwoot API token value in `tenants/proton.env`:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='cd /opt/platform/deploy && sudo cp tenants/proton.env tenants/proton.env.bak-$(date +%Y%m%d-%H%M%S)'
```

Edit the token with an editor on the VM. Then recreate (not restart) the service:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='cd /opt/platform/deploy && sudo docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d agent'
```

- [ ] **Step 4: Verify new automated messages carry the new identity**

Send a WhatsApp message that triggers a lifecycle reply, then:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo docker exec platform-infra-postgres-1 psql -U postgres -d chatwoot_proton -A -F"|" -c "select m.id,coalesce(u.name,chr(45)) as sender from messages m left join users u on u.id=m.sender_id where m.message_type=1 order by m.id desc limit 5;"'
```

Expected: the newest rows show `Proton e.MAS Centre`, not `Yuda Adi`.

- [ ] **Step 5: Document the requirement**

In `deploy/tenants/example.env`, add a comment above the Chatwoot API token variable stating that it should belong to a **dedicated bot user**, not a human agent, because the token owner's name appears on every automated customer-facing message.

- [ ] **Step 6: Commit**

```bash
git add deploy/tenants/example.env
git commit -m "docs(deploy): require a dedicated bot user for the agent Chatwoot token"
```

---

## Blocked — not planned here

**Production email (`e.mascentre@pronet.my`)** cannot be configured until Proton supplies the mailbox and its hosting platform (Q1 in `docs/analysis/2026-08-05-email-channel-questions-for-proton.md`). Google Workspace and Microsoft 365 need materially different setups — Microsoft has largely disabled basic authentication, so it needs OAuth rather than an app password. Writing those steps now would be invention. Re-plan once the answer arrives.

**Meta App Review and Business Verification** is a Proton-side business process with weeks of lead time. Tasks 3-5 deliver testing capability only, and that limitation must be stated to the client rather than implied away.
