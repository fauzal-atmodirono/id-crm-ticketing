# Package A — Live inbound email + Facebook/Instagram activation

**Date:** 2026-08-04
**Covers demo-feedback items:** #4 (live inbound email), #5 (FB/IG connection)
**Type:** configuration + runbook. **No first-party code changes.**
**Effort:** hours, not days. Both are gated on credentials we don't hold yet.

---

## 1. Goal

Get two dead channels to the point where they can be demoed and tested end to
end on the `proton` tenant:

1. **Inbound email** working against `devotech29@gmail.com` as the demo
   mailbox, so a mail sent to that address becomes a Chatwoot conversation and
   an agent reply goes back out as a real email. Proton will later swap in
   their own address/domain, so nothing may hard-code the Gmail account.
2. **Facebook and Instagram** inboxes connectable from Chatwoot's inbox
   settings, at least in a developer/test capacity, rather than showing as
   unavailable.

## 2. Why this is a config task and not an engineering one

`agent/` and `backend/` never touch either channel directly. Chatwoot owns both:

- Email is a per-inbox **IMAP + SMTP** configuration inside Chatwoot
  (`Settings → Inboxes → Add Inbox → Email`), fetched by a Sidekiq scheduled
  job. Our tenant compose already passes outbound SMTP through
  (`deploy/docker-compose.tenant.yml:32-39`, defaulting to the shared Mailpit
  catcher), and `deploy/tenants/example.env:52-65` already documents the Gmail
  app-password caveat. Nothing in our code needs to change.
- Facebook/Instagram are Chatwoot channels that require Meta app credentials
  supplied as environment variables to the Chatwoot containers, plus a Meta
  app that owns the target Page/IG account.

So the deliverable here is a **verified runbook plus tenant env changes**, not
a patch.

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
conversations (`agent/app/services/sync.py::maybe_escalate`). Until an email
inbox exists on `proton`, that feature is untestable. This package is
therefore a hard prerequisite for verifying #17.

### 3.4 Verification

| # | Check | Expected |
|---|---|---|
| 1 | Send mail from an outside address to `devotech29@gmail.com` | Within one IMAP poll cycle a new conversation appears in the Email inbox |
| 2 | Reply from Chatwoot as an agent | The reply arrives in the sender's mailbox, threaded, `From:` = `MAILER_SENDER_EMAIL` |
| 3 | Set `EMAIL_AUTOACK_ENABLED=true`, send a fresh mail | Exactly one auto-acknowledgement, once per thread |
| 4 | Add the `escalate` label with `EMAIL_ESCALATION_ENABLED=true` and PIC/dealer rows populated | Two separate mails: customer ack + internal forward, **no CC/BCC** |
| 5 | Check Mailpit is no longer catching proton mail | Mail leaves the box for real |

## 4. Design — Facebook / Instagram

### 4.1 Correcting the record on the blocker

Existing docs say FB/IG is blocked on **Meta Business verification**. That is
true for *production* — a public app messaging arbitrary customers needs App
Review for `pages_messaging` (and `instagram_manage_messages`) plus business
verification. It is **not** true for testing: a Meta app in *Development mode*
can message and be messaged by Pages the developer administers, with no review
and no verification. That is enough for a demo and for our own smoke tests.

So the answer to "can we activate it?" is: **yes for testing, now; production
still needs Meta review.** Plan the demo accordingly and say so to Proton
rather than implying full capability.

### 4.2 Steps

1. Create (or reuse) a Meta app at developers.facebook.com, add the **Messenger**
   and **Instagram** products, and connect a Facebook Page we control plus an
   Instagram professional account linked to that Page.
2. Collect the App ID, App Secret, and choose a verify token.
3. Set them on the Chatwoot containers for the tenant. **Verify the exact
   variable names against upstream `v4.15.1`** (the version pinned in
   `deploy/chatwoot-fork/UPSTREAM_VERSION`) before writing them — Chatwoot has
   renamed these between majors, and guessing produces a silently disabled
   channel. Expect a Facebook app id/secret/verify-token trio, with Instagram
   either sharing them or taking its own.
4. Add the variables to `deploy/docker-compose.tenant.yml`'s Chatwoot env block
   and document them in `deploy/tenants/example.env` (the repo convention is
   that no env var exists in one place only).
5. Register the webhook callback URL — Chatwoot's Facebook callback path on the
   tenant's public Caddy hostname — in the Meta app, using the same verify token.
6. Confirm the account has the Facebook/Instagram channels enabled in Chatwoot's
   super-admin account features; some builds gate channels per account.
7. Connect the inbox from `Settings → Inboxes → Add Inbox → Facebook` and
   authorize the Page.

### 4.3 Verification

| # | Check | Expected |
|---|---|---|
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
- **Exact Chatwoot env var names are unverified** in this checkout — the SPA
  source isn't vendored here. Step 4.2.3 is a verification step, not a
  copy-paste.

## 6. Out of scope

- Meta App Review / Business Verification submission (a Proton-side business
  process, weeks of lead time).
- Proton's own mail domain, subdomain delegation, or SMTP relay (feedback #18,
  awaiting Proton).
- WhatsApp (already live), Telegram, or any other channel.

## 7. Definition of done

Both channels are demonstrable on `proton` by a person following this doc, the
verification tables above pass, every new env var appears in **both**
`docker-compose.tenant.yml` and `tenants/example.env`, and
`docs/analysis/crm-channel-interaction-guide.md` is updated with the real,
tested state — including the explicit "dev-mode only" caveat for FB/IG.
