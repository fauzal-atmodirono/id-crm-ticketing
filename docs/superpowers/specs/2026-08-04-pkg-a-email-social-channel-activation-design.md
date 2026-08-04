# Package A — Live inbound email + Facebook/Instagram activation

**Date:** 2026-08-04 · **Updated:** 2026-08-04 (email half now done and verified)
**Covers demo-feedback items:** #4 (live inbound email), #5 (FB/IG connection)
**Type:** configuration + runbook. **No first-party code changes.**
**Effort:** hours, not days.
**Related:** `docs/analysis/2026-08-05-email-channel-questions-for-proton.md`
(open questions for the client), and Package G for the escalation-policy build
that email go-live ultimately depends on.

> **Status 2026-08-04.** The **email half is DONE on `proton`** — inbound mail
> arrives, conversations are created, webhooks reach the agent, exactly one
> acknowledgement is sent. §3.5 records what was actually done and found. The
> **FB/IG half is still blocked**, now on HTTPS (§4.2) rather than on Meta
> Business Verification as previously believed.

---

## 1. Goal

Get two dead channels to the point where they can be demoed and tested end to
end on the `proton` tenant:

1. **Inbound email** working against `devotech29@gmail.com` as the demo
   mailbox, so a mail sent to that address becomes a Chatwoot conversation and
   an agent reply goes back out as a real email. Proton's production address is
   **`e.mascentre@pronet.my`** (named in their `CRM Process Flow (1).xlsx`
   Email tab), so nothing may hard-code the Gmail account.
2. **Facebook and Instagram** inboxes connectable from Chatwoot's inbox
   settings, at least in a developer/test capacity, rather than showing as
   unavailable.

## 2. Why this is a config task and not an engineering one

`agent/` and `backend/` never touch either channel directly. Chatwoot owns both:

- Email is configured in **two separate places**, and conflating them is what
  cost us time (see §3.5):
  - **Outbound** — account-wide SMTP from tenant env
    (`deploy/docker-compose.tenant.yml:32-39`, defaulting to the shared Mailpit
    catcher; `deploy/tenants/example.env:52-65`).
  - **Inbound** — **per-inbox IMAP, stored in the Chatwoot database**, not in
    env. Chatwoot reads no IMAP environment variables at all. Set it at
    `Settings → Inboxes → [Email inbox] → IMAP`; a Sidekiq job polls it.
- Facebook/Instagram credentials are set at **runtime in the Chatwoot
  super-admin console** (`/super_admin/app_config?config=facebook` and
  `?config=instagram`), not via environment variables — verified on the running
  v4.15.1 instance. See §4.3.

So the deliverable here is a **verified runbook**, not a patch, and mostly not
even an env change.

## 3. Design — inbound email

### 3.1 Two possible mailbox shapes

| Option | How it works | Verdict |
|---|---|---|
| **A. Gmail IMAP/SMTP** (recommended for the demo) | Chatwoot polls `imap.gmail.com:993` for new mail and sends via `smtp.gmail.com:587`. Needs 2FA on the account plus a 16-character **App Password** (entered with no spaces). | Use this. Matches what Proton will hand us later (they'll give a mailbox, not a domain). |
| **B. Forward-to address** | Chatwoot generates a `<inbox>@<domain>` address and you forward real mail to it. Needs inbound-mail infrastructure (MX records) we don't run. | Rejected. We have no inbound MX for the nip.io demo domain. |

### 3.2 Configuration steps

1. On `devotech29@gmail.com`: enable 2-Step Verification, then create an App
   Password. **The password is a secret — it goes straight into the tenant env
   file on the VM and is never pasted into chat, logs, commit messages, or a
   spec.**
2. In `deploy/tenants/proton.env`, set the outbound leg so agent replies and
   the EM-7 escalation mails actually deliver instead of landing in Mailpit:
   ```
   SMTP_ADDRESS=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=devotech29@gmail.com
   SMTP_PASSWORD=<app password, no spaces>
   SMTP_AUTHENTICATION=login
   SMTP_DOMAIN=gmail.com
   SMTP_ENABLE_STARTTLS_AUTO=true
   MAILER_SENDER_EMAIL=Proton CRM <devotech29@gmail.com>
   ```
   Recreate `chatwoot-rails` and `chatwoot-sidekiq` for the tenant.
3. In the Chatwoot UI: `Settings → Inboxes → Add Inbox → Email`, then fill the
   IMAP section (`imap.gmail.com`, port `993`, SSL on, same username/app
   password) and the SMTP section (same values as step 2). Enable IMAP.
4. Assign the demo agents to the inbox and give it a clear production-ready
   name (the demo confusion in feedback item #9 was about vague inbox names —
   don't repeat it: call it `Proton Email` rather than `email test`).

### 3.3 What this unblocks

The EM-7 two-thread escalation (feedback #17) only fires on **Email-channel**
conversations (`agent/app/services/sync.py::maybe_escalate`), so an email inbox
was a hard prerequisite for testing it. **That prerequisite is now met**
(§3.5) — EM-7 is testable as soon as the PIC/dealer rows are populated. Note
that what EM-7 does is now known to be a subset of Proton's actual escalation
policy; see Package G.

### 3.4 Verification

| # | Check | Expected |
|---|---|---|
| 1 | Send mail from an outside address to `devotech29@gmail.com` | Within one IMAP poll cycle a new conversation appears in the Email inbox |
| 2 | Reply from Chatwoot as an agent | The reply arrives in the sender's mailbox, threaded, `From:` = `MAILER_SENDER_EMAIL` |
| 3 | Send a fresh mail | **Exactly one** acknowledgement, once per thread — see §3.5.2, this failed the first time |
| 4 | Add the `escalate` label with `EMAIL_ESCALATION_ENABLED=true` and PIC/dealer rows populated | Two separate mails: customer ack + internal forward. **The no-CC assumption here is now contested — see Package G** |
| 5 | Check Mailpit is no longer catching proton mail | Mail leaves the box for real |

### 3.5 What actually happened (2026-08-04) — findings worth keeping

#### 3.5.1 The env file is only half the configuration

Outbound SMTP was correctly set in `tenants/proton.env` and live in the
containers, yet no mail ever arrived, because `channel_email.imap_enabled` was
`false` with empty address/login/password. Chatwoot never polled the mailbox,
so no conversation was created, so no `conversation_created` webhook fired, so
no acknowledgement was sent. **The greeting complaint was the last symptom of a
chain that broke at the first link.** Configuring IMAP on the inbox fixed it.

Related red herring: the inbox showed *"Your inbox is disconnected… reauthorize"*,
and clicking Reauthorize produced Google's `Missing required parameter:
client_id`. That button routes to a Gmail-OAuth flow; this inbox has
`provider = (empty)`, i.e. plain IMAP, and no Google OAuth client is configured.
**The button does not apply to a plain IMAP inbox** and the banner was stale
state. It cleared once IMAP authenticated.

#### 3.5.2 Two acknowledgements were being sent

With IMAP working, every customer received the same paragraph **twice**:

| Source | Evidence | Appears as |
|---|---|---|
| Chatwoot's native inbox greeting | `message_type=3` (template), no sender | "Notifications from …" |
| Our `agent` lifecycle auto-ack | `message_type=1`, sender User 1, `content_attributes: {"proton_lifecycle": true}` | "Yuda Adi from …" |

This settles a previously open question: **Chatwoot does send its greeting on
the Email channel.**

**Decision: keep Chatwoot's native greeting, set `EMAIL_AUTOACK_ENABLED=false`.**
Two reasons — the greeting is operator-editable in the UI (ours needs an env
edit and redeploy, which contradicts the operator-configurable direction of the
product), and an automated acknowledgement should not arrive under a real
person's name. Applied to `proton` on 2026-08-04; `proton-agent` was
**recreated**, not restarted, since a restart does not re-read the env file.

Trade-off accepted: our auto-ack skips conversations created by an AI handoff
(`_created_by_ai_handoff`); Chatwoot's greeting has no such awareness. Handoff
conversations are created on the API inbox, not the email inbox, so this does
not bite today.

**Still untested:** a resolved conversation where the customer then replies to
the same old thread. If Chatwoot opens a *new* conversation, the greeting fires
again in the same thread, which Proton's SOP explicitly forbids ("do NOT keep
sending it repeatedly in the same email thread"). Test before go-live.

#### 3.5.3 Mailbox hygiene decides whether "every email reaches the CRM" is true

From Chatwoot's fetch code on the running system:

```ruby
imap.select('INBOX')                    # INBOX folder only
imap_client.search(['SINCE', since])    # since = today - 1 day
MAX_MESSAGES_PER_SYNC = 500
email_already_present?(channel, message_id)   # dedupe by message-id
```

Consequences, all verified rather than assumed:

- mail filtered away from the INBOX is **never** seen — permanently;
- there is **no historical backfill**; only mail arriving after go-live appears;
- an outage longer than a day loses that window permanently, because the search
  is relative to *today*, not to the last successful sync;
- the spam folder is never read.

So the production mailbox must deliver customer mail straight to the inbox with
no server-side filing rules. On the current demo Gmail this is already visibly
wrong: an Anthropic billing receipt became conversation 36 and was auto-replied
to. A dedicated mailbox is the fix.

#### 3.5.4 Side finding — the agent service posts as a real person

The `agent` service authenticates to Chatwoot as **user 1 ("Yuda Adi")**, so
every message it posts is attributed to that person on every channel — 59 such
messages already exist on the WhatsApp inbox alongside 52 correctly attributed
to the AgentBot. The fix is a dedicated bot/system user (e.g. "Proton e.MAS
Centre") whose access token the agent uses. Small change, affects all channels,
and it should land before Proton sees the platform. **Not scoped to this
package** — flagged here because this is where it was found.

## 4. Design — Facebook / Instagram

### 4.1 Correcting the record on the blocker

Existing docs say FB/IG is blocked on **Meta Business verification**. That is
true for *production* — a public app messaging arbitrary customers needs App
Review for `pages_messaging` (and `instagram_manage_messages`) plus business
verification. It is **not** true for testing: a Meta app in *Development mode*
can message and be messaged by Pages the developer administers, with no review
and no verification. That is enough for a demo and for our own smoke tests.

So the answer to "can we activate it?" is: **yes for testing, once HTTPS is in
place; production still needs Meta review.** Plan the demo accordingly and say
so to Proton rather than implying full capability.

### 4.2 Hard prerequisite — HTTPS on the tenant hostname

**Verified 2026-08-04:** the proton tenant serves plain HTTP
(`http://proton.crm.<ip>.nip.io`, browser shows "Not Secure"), because
`deploy/caddy/Caddyfile` sets `auto_https off` with the comment *"Plain HTTP +
nip.io hostnames only — no TLS cert to manage yet. remove when a real domain
lands"*.

Meta requires **HTTPS with a publicly trusted certificate** for both the OAuth
redirect URI and the webhook callback URL, and rejects `http://` outright. So
this is a blocker that hits *before* Business Verification does — dev mode
avoids verification, but nothing avoids HTTPS. Three ways out:

| Option | Notes |
|---|---|
| **Real domain + cert** (recommended) | Point a Devoteam- or Proton-owned domain at the VM and let Caddy issue certs. This is needed for production anyway, and it's the same conversation as the mail subdomain ask (feedback #18). Solves it permanently. |
| **Turn on `auto_https` for the nip.io hosts** | One-line Caddyfile change, no DNS work. Risk: `nip.io` is heavily used and Let's Encrypt rate limits are frequently already exhausted for it, so issuance may simply fail. Try it, don't depend on it. |
| **Tunnel (Cloudflare Tunnel / ngrok)** | Gives a trusted HTTPS hostname in minutes for a one-off test, without touching prod DNS. Good enough to *prove* the FB/IG flow works; not a deployment. |

Whichever is chosen, the public hostname must stay stable — Meta app settings
pin the redirect and webhook URLs, and a changing tunnel hostname means
re-registering them every session.

### 4.3 Steps

Credentials are set **at runtime in the Chatwoot super-admin console**, not via
environment variables — verified from the running v4.15.1 instance, which
exposes `/super_admin/app_config?config=facebook` and `?config=instagram`. This
supersedes the env-var approach: no compose change, no image rebuild, no
container restart of the whole stack.

1. Sort out HTTPS per §4.2. Nothing below works without it.
2. Create (or reuse) a Meta app at developers.facebook.com, add the **Messenger**
   and **Instagram** products, and connect a Facebook Page we control plus an
   Instagram professional account linked to that Page.
3. In `Super Admin → Settings → Messenger`, fill **Facebook App ID**,
   **Facebook App Secret**, **Facebook Verify Token**, and **Instagram Verify
   Token**. In `Super Admin → Settings → Instagram`, fill **Instagram App ID**,
   **App Secret**, and **Verify Token**. Submit each form.
4. **Check the API version fields.** The running instance defaults Facebook to
   `v18.0` (Instagram to `v22.0`). `v18.0` dates from late 2023 and is very
   likely past Meta's ~2-year deprecation window by now; if the connect flow
   fails with an unsupported-version error, raise it to a currently supported
   version. This is the single most likely non-obvious failure.
5. Register the webhook callback URL and the OAuth redirect URI from the Meta
   app against the tenant's **HTTPS** hostname, using the same verify token
   entered in step 3.
6. If the Facebook/Instagram cards in `Settings → Inboxes → Add Inbox` are still
   greyed out after saving, the installation config is cached — restart the
   tenant's `chatwoot-rails` (and `chatwoot-sidekiq`) containers and re-check.
7. Connect the inbox from `Settings → Inboxes → Add Inbox → Facebook` and
   authorize the Page.

**Leave "Enable human agent" as False.** It extends the messaging window to 7
days but requires additional Meta app approval (the `human_agent` feature) —
irrelevant for testing and it will fail review-gated.

### 4.4 Note on the "WhatsApp Call (Beta)" card

The same inbox grid shows a greyed **WhatsApp Call (Beta)** card and a greyed
"Coming Soon" Voice card. That is **out of scope for this package** and does not
become available by completing the steps above: Chatwoot's WhatsApp calling
requires a WhatsApp **Cloud API** inbox (ours is a Twilio-provider inbox) and,
on self-hosted from v4.15.0, a **paid plan** — we run community edition. See
Package C's appendix for the alternative route via Twilio WhatsApp Business
Calling straight into our existing AI voice bridge.

### 4.5 Verification

| # | Check | Expected |
|---|---|---|
| 0 | Load the tenant over `https://` | Valid certificate, no browser warning |
| 1 | Facebook option appears in Add Inbox | No longer greyed out |
| 2 | Page authorization completes | Inbox created, Page listed |
| 3 | Message the Page from a test user | Conversation appears in Chatwoot |
| 4 | Reply from Chatwoot | Message reaches Messenger |
| 5 | Repeat 3-4 for Instagram DM | Same |
| 6 | AI bot behaviour on the new inbox | Either the agent-bot answers or it stays silent by design — decide per inbox, don't leave it accidental |

## 5. Risks and open items

- **Gmail App Password requires 2FA.** If the account can't enable 2FA, this
  whole path stops; fall back to a different demo mailbox.
- **Gmail rate/spam limits.** Fine for demos, not for production volume. Proton
  supplying a real domain + SMTP relay remains the production answer (feedback
  item #18).
- **Meta app in Dev mode only reaches Pages/accounts we administer.** A demo to
  Proton where *they* message the Page from their own phone will work only if
  their account is added as a tester on the app. Add Proton's tester accounts
  before the demo or the demo fails live.
- **HTTPS is the real critical path, not Meta verification.** It needs a
  decision (domain vs nip.io certs vs tunnel) before any FB/IG work starts, and
  the domain option has lead time. Treat §4.2 as the first task, not a
  precondition someone else will handle.
- **Turning on TLS changes every existing URL.** Webhook URLs already registered
  with Twilio, the SPA's injected backend URL, and anything Proton has
  bookmarked all move from `http://` to `https://`. Sweep them, or the FB fix
  breaks WhatsApp.
- **Facebook API version `v18.0`** is the default in the running instance and is
  probably out of support — see step 4.3.4.

## 6. Out of scope

- Meta App Review / Business Verification submission (a Proton-side business
  process, weeks of lead time).
- Proton's own mail domain, subdomain delegation, or SMTP relay (feedback #18,
  awaiting Proton).
- WhatsApp (already live), Telegram, or any other channel.
- WhatsApp Call / Voice channel (§4.4) — Package C.
- Migrating to a production domain across all tenants. This package needs HTTPS
  on the proton hostname; a full domain migration is a separate deploy task.

## 7. Definition of done

Both channels are demonstrable on `proton` by a person following this doc, the
verification tables above pass, the tenant is reachable over HTTPS with a valid
certificate and no `http://` URL is left registered anywhere, any new SMTP env
var appears in **both** `docker-compose.tenant.yml` and `tenants/example.env`
(the Meta credentials live in super-admin config, not env), and
`docs/analysis/crm-channel-interaction-guide.md` is updated with the real,
tested state — including the explicit "dev-mode only" caveat for FB/IG.
