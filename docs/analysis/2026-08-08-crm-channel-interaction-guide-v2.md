# CRM Channel Interaction Guide v2 — Working a Case on Every Channel

**Audience:** customer-service agents and team leaders using the Chatwoot CRM
day to day, and anyone training them. This is a "how do I actually work a
case" guide, written so an agent with no prior context can follow it.

**Date:** 2026-08-08. **Supersedes (does not replace):**
`crm-channel-interaction-guide.md` (dated 2026-08-04) — that file is kept as
the historical record of what was true on that date. Do not delete it; do
not treat it as current. Three things changed under it that this version
covers and the old one does not:

1. **Email escalation now has a reply loop.** A dealer's or PIC's emailed
   reply lands back on the original case as a private note, with an
   AI-drafted customer reply beside it. The 08-04 guide predates this
   entirely — it describes email escalation as one-way.
2. **Dealer routing is group-based.** A dealer entry now holds a *list* of
   member emails, all of whom receive the forward — not one address.
3. **SLA breaches now notify by email and by a note on the case**, not only
   the older WhatsApp-only ping the 08-04 guide assumed.

Everything below was checked against the code in this repo, not against the
older guide or the SOP spreadsheet. Where a capability exists but needs a
flag turned on, the flag is named **in the step**, not in a footnote. Where
something described here isn't built, it says so plainly.

**Companion docs, still current:**
- `crm-channel-ui-testing-guide.md` — per-step build status table.
- `phone-channel-package-c-verification.md` — the phone/voice scenarios in
  full detail, and the accepted limitations this guide will keep pointing
  back to rather than re-litigate.
- `2026-08-06-escalation-email-e2e-scenario.md` — the exact email-escalation
  test cases (TC-01…TC-10) this guide's Email section is built from. Its own
  results table is unfilled as of this writing — the scenarios describe
  intended, code-reviewed behaviour verified once against a live tenant
  configuration, not a completed pass/fail run. Treat the specifics (exact
  subject lines, note text, label order) as reliable; treat "will definitely
  work end-to-end on your tenant" as still worth a smoke test.
- `crm-process-flow-runbook.md` — how to wire the lifecycle engine
  (disclaimer → idle → resolution → survey → auto-categorize) per tenant.

**A rule that applies to every section below:** almost nothing here is on by
default. This guide describes the system **as configured** — every step
that depends on a flag names it. If your tenant hasn't had that flag turned
on, the step doesn't happen; say "not set up for you yet," not "broken."

---

## 1. How the channels map to the CRM

| Customer touchpoint | Chatwoot inbox type | What the AI does before a human sees it | Status today |
|---|---|---|---|
| WhatsApp message | Twilio-channel inbox (`Channel::TwilioSms`/`Channel::Whatsapp`) | Full bot: disclaimer, KB-grounded Q&A, idle/resolution/survey lifecycle, live handoff | Live, most complete channel |
| Web chatbot (website widget) | Native Chatwoot Website Widget inbox (`Channel::WebWidget`) | Same agent-bot as WhatsApp (identical decision logic), plain-text replies (no WhatsApp-style chunking) | Live where the widget is connected and the agent-bot is assigned to that inbox — confirm both with an admin before promising it |
| Voice bot (the AI-answered part of a call) | Generic API-channel inbox (`Channel::Api`), via Twilio + Gemini Live | Greeting, KB Q&A, CSAT prompt, optional live transcript/classification/recording behind flags — see §4 | The pre-Package-C basics (greeting, KB Q&A, mocked handoff, rating survey) were demoed live 2026-07-28. Everything added since (live transcript, classification, recording, real handoff) is code-complete and unit-tested but **has never been run against a real Twilio call** — see §5's opening caveat |
| Phone (the human/agent side of that same call) | Same `Channel::Api` inbox — the call's Chatwoot conversation | n/a — this is what's left behind for an agent to work | Same caveat as Voice bot: real behind flags, unconfirmed against a real call |
| Email | Native Email inbox (`Channel::Email`) | Once-per-thread auto-ack; two-thread escalation (customer ack + PIC + dealer group) on the `escalate` label, gated by `EMAIL_ESCALATION_ENABLED`; a dealer/customer reply now links back onto the case | Possible once an admin creates a two-way (IMAP+SMTP) Email inbox for the tenant — not automatic on a fresh one. The flow itself is built and code-reviewed; see §7 for the exact flags |
| Social (Facebook/Instagram) | Native Social inbox | None by SOP design | Unchanged since 08-04 — still blocked on Meta Business verification. Not covered further here; see the 08-04 guide §4 |

Every channel still funnels into the same Chatwoot conversation list — you
don't need a different mental model per channel, just different
expectations about what already happened before it reached you, and which
flags your tenant actually has on.

---

## 2. The agent's toolkit (identical across every channel)

| Action | Where | Notes |
|---|---|---|
| See if the bot already answered | Conversation timeline | Bot messages look like any other message |
| Ask the AI to draft a reply | Reply box → AI-actions menu → **Ask Copilot** | Needs the conversation's KB grounded first |
| Get a suggested answer to the customer's last message | Reply box → AI-actions menu → suggestion icon ("FAQ Assist", informal name) | Matches the last message only, not the whole thread; shows a `Sources:` line when grounded |
| Categorize the case | Right sidebar → **Conversation Actions → Labels** | Flat list — no main→sub cascading dependency in the picker yet, even though the underlying taxonomy (7 divisions / 26 subcategories) is real |
| Escalate to a PIC/dealer | Add the **`escalate`** label | **Only sends anything on an Email-channel conversation, and only when `EMAIL_ESCALATION_ENABLED=true`** — see §7. On WhatsApp/Web, adding this label by hand does not by itself notify anyone; see each channel's Scenario C |
| Reassign to another agent | Click the assignee name in the conversation header | Logged in **Audit Log** |
| Resolve | Status control, top-right of the conversation | Triggers the rating survey when the lifecycle engine is on (`LIFECYCLE_ENABLED`) |
| See who reassigned what | Standalone **Audit Log** icon, left sidebar | Needs `audit.view` permission + `RBAC_ENABLED=true` for the tenant |
| See a customer's history across channels | Contact panel → **Previous Conversations** | Stock, unforked Chatwoot — depends on the customer using the same contact identity on every channel |
| Look up a customer by phone/vehicle number | Standalone **Customer 360** icon, left sidebar | Needs `customer360.view` permission + `RBAC_ENABLED=true`. Any vehicle/DMS data shown is **fabricated demo data** unless `DMS_MOCK_CLIENT_ENABLED` is set — and even then it's marked demo, not a real DMS connection (Phase 1 ships no real adapter) |
| Edit PIC/dealer routing | Standalone **Escalation Routing** icon, left sidebar | Needs `escalation.manage` permission + `RBAC_ENABLED=true`. Dealers are edited as a **list of member emails**, not one address |
| Edit SLA response/resolution targets, including Tier-2 and warning thresholds | Standalone **SLA Policies** icon, left sidebar | Needs `sla.manage` permission + `RBAC_ENABLED=true`. `Tier-2 re-alert after (hours)` and `Warn before breach (minutes)` are now editable fields on this page alongside response/resolution hours |
| Edit the persona, disclaimer, and lifecycle messages | **Settings → Knowledge → Settings** (pick the assistant from the dropdown) | Not the "Assistants" list item — that's just rename/description |
| Edit the two customer-facing email templates | Same page, **"Tenant settings" panel** | Both the inbound auto-ack and the escalation acknowledgement bodies are editable here; env vars (`EMAIL_AUTOACK_TEMPLATE`, `EMAIL_ESCALATION_ACK_TEMPLATE`) are only the fallback when nothing's been saved in the UI |

**None of the RBAC-gated pages above (Audit Log, Customer 360, Escalation
Routing, SLA Policies, Roles & Permissions) appear in the sidebar at all**
if `RBAC_ENABLED` is off for the tenant or your account lacks the specific
permission — that's expected, not a bug to report.

---

## 3. WhatsApp

Still the most complete channel — everything below works today, with two
corrections from the 08-04 guide.

### 3.1 What the customer does
Messages the connected WhatsApp number.

### 3.2 What happens before you see it
1. Bot posts the AI-use disclaimer (`LIFECYCLE_DISCLAIMER_ENABLED`, on by
   default when the lifecycle engine is on).
2. Bot replies in the customer's own language, grounded in the tenant's
   uploaded FAQ/KB content when the question matches.
3. Outside business hours, the bot posts the hours/website auto-reply
   instead.
4. Idle handling and the resolution/rating survey run per
   `crm-process-flow-runbook.md` — `LIFECYCLE_ENABLED` must be on for any of
   this to happen; it's a byte-identical no-op when off.
5. If the AI itself judges a handoff to be a genuine complaint (its own
   classification, not a human's decision), it automatically adds the
   `escalate` label **and** — separately from the label-based Email flow in
   §7 — fires a PIC email + WhatsApp alert on its own, gated by the
   backend's `ESCALATION_EMAIL_ENABLED`. This happens with no agent action.

### 3.3 Scenario A — bot handles it fully
Customer asks a routine question, bot answers from the KB, conversation
auto-closes with a `category_*` label if `LIFECYCLE_AUTO_CATEGORIZE=true`.
**Your role:** none — spot-check via **Reports → CSAT** or by opening the
closed conversation.

### 3.4 Scenario B — customer asks for a human
1. The conversation reopens and is assigned via `POST /routing/assign` on
   the backend — but **only if `ROUTING_ENABLED=true`** there. With it off
   (the default), the call is a no-op (`{"disabled": true}`) and the
   conversation is simply left open/unassigned for whoever picks it up
   next, or falls back to whatever native Chatwoot round-robin/team
   assignment the inbox already has configured.
2. When routing is on, the assignee becomes whichever online agent has
   WhatsApp as their `Primary channel`
   (**Settings → Inboxes → *(WhatsApp inbox)* → Collaborators → Agent
   Channel Priorities**).
3. Work the case normally; use **Ask Copilot** for a KB-grounded draft.
4. Resolve → triggers the agent-performance rating survey (a different
   variant from Scenario A's AI-performance one).

### 3.5 Scenario C — case needs escalation
**Correction from the 08-04 guide:** adding the `escalate` label to a
WhatsApp conversation by hand does **not** send an email or WhatsApp alert.
`agent/app/services/sync.py::maybe_escalate` checks the conversation's
inbox `channel_type` and returns immediately unless it's `Channel::Email` —
that's the whole EM-7 two-thread flow in §7, and WhatsApp doesn't qualify.

1. If you add a `dealer_<slug>` label to a WhatsApp conversation, it still
   stamps a `dealer_escalated_at` custom attribute for BI reporting (this
   part is channel-agnostic) — but nobody is emailed as a result.
2. The only automatic PIC notification on WhatsApp is the AI's own
   complaint-classification path (§3.2 step 5) — you cannot trigger that
   yourself by adding a label.
3. **What to actually do:** contact the dealer/PIC outside the CRM (phone,
   a direct email) until this channel gets the same manual escalation email
   Email-channel conversations have. Don't tell a customer "I've escalated
   this" on the strength of the label alone.

### 3.6 What's not usable yet on WhatsApp
- No vehicle/customer lookup tool beyond what the customer tells you in
  chat (Customer 360's DMS data is demo-only, see §2).
- Voice notes/photos: `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` gates this and
  has **not been tested end to end** against a real WhatsApp number —
  describe it as untested if asked, not as working.
- Manual `escalate`-label escalation, per §3.5 above.

---

## 4. Web chatbot (website widget)

Same underlying decision logic as WhatsApp (`agent/app/services/orchestrator.py`
handles both — a native Chatwoot Website Widget inbox is just another inbox
the agent-bot can be assigned to), so this section is shorter and cross-
references §3 rather than repeating it. The two real differences: replies
are sent as plain text (no WhatsApp-style Markdown-to-formatting conversion
or 1600-character chunking — `Channel::WebWidget` isn't in the orchestrator's
`_WHATSAPP_CHANNELS` set), and the visitor may be anonymous.

### 4.1 What the customer does
Opens the chat widget embedded on the website and types a message. There's
no requirement to give a name, email, or phone number unless the widget's
pre-chat form has been configured to ask for one (stock Chatwoot behaviour,
untouched by any fork patch) — a visitor who skips it gets an auto-created
contact record with nothing identifying in it.

**What that means for you:** if the same person later emails or calls in,
**Previous Conversations** (§2) has no way to connect the dots unless they
happened to give an email/phone this time, or gave the same one both times.
Don't assume a web-chat contact and a later phone/email contact are the same
person just because the story sounds similar — check the contact panel.

### 4.2 What happens before you see it
1. Bot posts the AI-use disclaimer/welcome (`LIFECYCLE_DISCLAIMER_ENABLED`),
   same as WhatsApp §3.2 step 1 — this needs `LIFECYCLE_ENABLED=true` and
   the agent-bot assigned to the widget's inbox, or nothing happens at all.
2. Bot answers from the **same KB** every other channel uses — there's no
   separate web-only knowledge source.
3. Idle/resolution/rating-survey lifecycle behaves identically to WhatsApp
   §3.2 step 4, same flags.
4. Same AI-driven auto-escalation as WhatsApp §3.2 step 5 (channel-agnostic,
   gated by the backend's `ESCALATION_EMAIL_ENABLED`).

### 4.3 Scenario A — bot handles it fully
Same shape as WhatsApp §3.3: visitor asks a routine question, bot answers
from KB, conversation can auto-close with a `category_*` label if
`LIFECYCLE_AUTO_CATEGORIZE=true`. Your role: none, spot-check via
**Reports → CSAT**.

### 4.4 Scenario B — visitor wants a human
1. Same `ROUTING_ENABLED` dependency as WhatsApp §3.4 step 1 — off (the
   default) means no automatic per-agent assignment by channel priority;
   the conversation is simply left open for whoever picks it up, or
   whatever native Chatwoot assignment rule the inbox already has.
2. Reply directly or use **Ask Copilot**; the reply goes out as plain text,
   with no formatting conversion.
3. Resolve → agent-performance rating survey, same as every chat channel.

### 4.5 Scenario C — case needs escalation
Identical limitation to WhatsApp §3.5: the widget's inbox is
`Channel::WebWidget`, not `Channel::Email`, so `maybe_escalate` returns
immediately regardless of the `escalate` label — no email fires. The only
automatic PIC notification is the AI's own complaint-classification path
(§4.2 step 4). Escalate manually outside the CRM, same as WhatsApp.

### 4.6 What's not usable yet on Web chatbot
- Manual `escalate`-label escalation, per §4.5.
- No vehicle/customer lookup beyond what the visitor types (Customer 360's
  DMS data is demo-only, §2) — and an anonymous visitor may not even have a
  phone number to look up with.
- No voice/media understanding on this channel — it's text-only by design,
  unlike WhatsApp where at least the *code* for media understanding exists
  (untested) behind a flag.

---

## 5. Voice bot — the AI-answered part of a call

**Read this before anything else in this section:** per
`phone-channel-package-c-verification.md`, *"no real Twilio call has ever
hit this code"* for everything Package C added (live transcript,
classification, recording, real handoff). Unit tests and hand-written fakes
cannot catch a mis-built TwiML verb or real Gemini Live latency behaviour.
Every "Expected" statement below is what the code should do, not a
confirmed report of what it did. The baseline underneath it — greeting, KB
Q&A, rating survey, ticket created at hangup — was demoed live on a real
call 2026-07-28 and is the one part of this section you can say is proven.

There is also no browser "click to talk to AI" widget live on any tenant's
website today: the code for a browser softphone exists
(`backend/apps/frontend/src/features/phone/components/PhoneCall.vue`, using
the backend's `phone_token`/`voice-sdk` endpoints), but `apps/frontend`
isn't part of this platform's deployed images — only `backend` and `agent`
are built and run on the VM. The only way a real customer reaches this
today is a real Twilio phone number pointed at the tenant's `/voice/phone/
incoming` webhook — ask engineering which number, if any, is provisioned
for your tenant.

### 5.1 What the customer does
Dials the number, or (in whatever future setup embeds it) uses a "call us"
button.

### 5.2 What happens before you see anything in Chatwoot
1. **If `PHONE_RECORDING_ENABLED` and `PHONE_RECORDING_ANNOUNCEMENT` and
   `TWILIO_WEBHOOK_BASE_URL` are ALL set**, a PDPA notice plays before the
   AI even greets the caller — miss any one of the three and the caller is
   never told, even if recording is otherwise on.
2. AI (Gemini Live) greets the caller. It is instructed to answer in
   English, Bahasa Melayu, or Chinese and switch languages mid-call to
   match the caller — but Bahasa-language reliability on this voice
   pipeline (a separate codepath from the text-channel fix that resolved
   WA-2) is an **open, unresolved issue** (`IVR-4` in the testing guide) —
   a `PHONE_LANGUAGE_NUDGE_ENABLED` mitigation shipped but is unconfirmed
   against a real call. Don't promise reliable Bahasa on a call today.
3. **If `PHONE_TRANSCRIPT_LIVE_ENABLED`** (default off): the Chatwoot
   conversation is created the moment the call starts (external id
   `phone-<CallSid>`), and completed transcript turns stream into it live,
   roughly every `PHONE_TRANSCRIPT_FLUSH_SECONDS` (default 15s) — you can
   watch it grow mid-call. **Off** (the default): the conversation is only
   created at hangup, from the complete transcript, exactly like before
   Package C.
4. A call that ends before anyone speaks (wrong number) self-cleans with a
   `[Call ended — no conversation]` marker instead of sitting open and
   empty in your queue.
5. AI answers vehicle/product questions grounded in the same KB every other
   channel uses.

### 5.3 Scenario A — routine question, fully AI-handled
Caller asks a spec question, AI answers from KB, then asks "anything else?"
until the caller signals they're done, then asks for a 1–5 rating and calls
a `submit_csat` tool. **Your role:** none in real time; QA by opening the
conversation afterward and checking **Reports → CSAT**.

### 5.4 Scenario B — caller wants a human
1. **`PHONE_HANDOFF_ENABLED=false` (the default):** the AI says "a
   specialist will follow up" and the call just continues — no transfer is
   attempted, ever. This is every tenant's behaviour today unless someone
   has explicitly turned this on.
2. **`PHONE_HANDOFF_ENABLED=true`:** the AI says something like *"Let me
   try to get a specialist for you now — if I can't connect you right
   away, they'll call you back soon,"* then attempts a real transfer to
   `PHONE_HANDOFF_TARGET_NUMBER`. **There is one static hunt-group number
   for every handoff reason** — there is no separate, always-on line for
   any particular kind of call. See §6 for what happens next; the
   agent-facing side of a transfer (business-hours gating, unanswered
   apology) is documented there since that's where a human actually acts
   on it.

### 5.5 Scenario C — a case needs escalation
Voice calls don't reach Email-channel conversations, so the same limitation
as WhatsApp §3.5 applies: an `escalate` label on a phone-originated
conversation does not by itself email anyone. If the AI's own complaint
classification fires (same mechanism as WhatsApp §3.2 step 5, channel-
agnostic), a PIC is notified automatically; otherwise, escalate manually
outside the CRM.

### 5.6 What's not usable yet on Voice bot
- **Reliable Bahasa Melayu on calls** — open issue, see §5.2 step 2.
- **DTMF vs. conversational routing** is an open decision, not built either
  way as a keypad menu — don't train agents on "press 1 for sales."
- **No real customer-facing browser widget is deployed** — see the opening
  caveat.
- Everything in Package C (§5.2 steps 1, 3–4, and all of §6) is unit-tested
  only, never run against a real call.

---

## 6. Phone — the human/agent side of the same call

This continues the call from §5 — there's no separate "what the customer
does" here; it's the conversation Chatwoot is left holding, and what an
agent does with it. Same top-line caveat as §5: everything below beyond the
plain transcript-at-hangup is unit-tested, not call-tested.

### 6.1 What happens before you see it
1. **`PHONE_TRANSCRIPT_CLASSIFICATION_ENABLED`** (default off, requires
   `PHONE_TRANSCRIPT_LIVE_ENABLED` on first): at hangup, a one-shot Gemini
   call derives `case_type`/`division`/`concern`/`status` from the
   transcript, writes them as custom attributes plus a `division_<slug>`
   label, and can flip the closing status from `solved` to `open` if the
   call sounded unresolved. **Both `division` (UI-only) and `case_category`
   (canonical spelling the reporting pipeline actually reads) get written**
   — checking only `division` in the sidebar can look right while
   reporting never sees the call.
2. **`PHONE_RECORDING_ENABLED`** (default off, also requires
   `PHONE_TRANSCRIPT_LIVE_ENABLED`): starts a dual-channel recording; once
   Twilio's status callback reports `completed`, `recording_sid`/
   `recording_duration`/`recording_url` land as **internal-only** custom
   attributes — never a visible comment. Retrieval is meant to be gated
   behind the `call_recording.listen` permission. `PHONE_RECORDING_
   RETENTION_DAYS` (default 90) is **informational only** — nothing
   automatically deletes a recording.

### 6.2 Scenario A — fully AI-handled call, agent reviews afterward
Open the resulting conversation, read the transcript, check
`case_category`/`division_<slug>` if classification is on, check
**Reports → CSAT** for the rating. Nothing to act on unless the classifier
flagged it `open`.

### 6.3 Scenario B — the call was transferred to a human
1. Confirm `PHONE_HANDOFF_ENABLED`, `PHONE_HANDOFF_TARGET_NUMBER`, and
   `PHONE_HANDOFF_CALLER_ID` are **all** set — the resolver refuses to
   dial without a caller id (would otherwise be a dropped call, Twilio
   error 13214) and silently falls back to "ticket_created" instead, which
   from the caller's side looks identical to the feature being off.
2. **Business hours gate every transfer, uniformly — there is no
   RSA/accident-specific bypass.** This is a real correction to the 08-04
   guide, which described RSA calls as routed to "a 24/7 line, bypassing
   the normal agent-only-hours restriction, confirmed live in the
   orchestrator." That code does not exist in the shipped Package C
   resolver: `HandoffTargetResolver.resolve()` checks the tenant's single
   configured Chatwoot inbox's business hours for **every** handoff reason
   with no exception, and **returns `None` (refuses the transfer) when
   outside those hours** — the opposite of a bypass. The only "always
   works" case is when the inbox has **no** business hours configured at
   all, which fails open (always attempts the dial) by coincidence, not by
   an RSA-aware design. There is a separate, unrelated Roadside-Assistance
   incident-log admin page (`RSA_ENABLED`, its own Postgres DB) for manual
   staff record-keeping once a human picks up an RSA case — it has nothing
   to do with call routing.
3. If a person answers at the target number: the conversation flips to
   `open` with a `[Handoff to human agent]` note (reason + summary) the
   moment the transfer is dialled — check while the transfer is still
   ringing, not after everyone hangs up.
4. If nobody answers (`no-answer`/`busy`/`failed`): the caller hears a
   scripted bilingual (English then Bahasa Melayu — not the trilingual
   AI-conversation language matching from §5.2) apology, then the call
   **hangs up**. It does **not** return the caller to the bot — this is
   the documented design, not a bug. The conversation gets an
   `unanswered_handoff` tag and a `[Handoff unanswered -- <status>]` note,
   status `open`. **The apology promises a callback that nothing
   automatically schedules** — only a human working the
   `unanswered_handoff` tag makes that true.

### 6.4 Scenario C — case needs escalation to a dealer/PIC
Same limitation as §3.5/§5.5: a phone-originated conversation is not on the
Email inbox, so `escalate` alone notifies nobody. Escalate manually.

### 6.5 What's not usable yet on Phone
- **No call recording retention enforcement** — policy only.
- **No DTMF menu**, decision pending.
- **The unanswered-handoff apology promises a callback nothing schedules.**
- **No RSA-specific after-hours transfer bypass** — see §6.3 step 2; if
  Proton specifically wants this, it needs to be built, not assumed
  already live.
- **Auto-busy status during calls is not built** — an agent on a phone call
  isn't marked busy for WhatsApp routing purposes.
- The whole of §6.1–6.3 is unconfirmed against a real Twilio call; treat
  every "Expected" above as a hypothesis until someone runs
  `phone-channel-package-c-verification.md`'s scenarios for real and
  records the result.

---

## 7. Email — the full current flow

This is the biggest change from the 08-04 guide, which described email as
blocked end-to-end. **"Wired" here describes what's now possible once an
admin sets it up, not something automatic on a fresh tenant:** a two-way
Email inbox (inbound IMAP + outbound SMTP) is added per tenant via
**Settings → Inboxes → Add Inbox → Email** in the Chatwoot UI itself, not
by any env var — a freshly-provisioned tenant has no Email inbox and no
working inbound mail until an admin does this once. Once that inbox
exists, the two-thread escalation flow plus a reply loop described below
are built and code-reviewed. The exact behaviour below is drawn from
`2026-08-06-escalation-email-e2e-scenario.md`'s ten test cases (TC-01…TC-10)
against a live tenant — the most concrete source available — but that
document's own pass/fail results are unfilled, so treat the specifics as
reliable and the "will definitely work on your tenant" as worth a smoke
test before a demo.

### 7.1 What the customer does
Emails the support address.

### 7.2 What happens before you see it
1. **The entire two-thread escalation flow in §7.5 — customer ack, PIC
   email, dealer forward, all of it — is gated by one master switch:
   `EMAIL_ESCALATION_ENABLED`** (agent service, `agent/app/config.py`,
   default `false` — confirmed `EMAIL_ESCALATION_ENABLED=false` at
   `deploy/tenants/example.env:113`). With it off, applying `dept_<slug>`/
   `dealer_<slug>`/`escalate` labels in §7.5 changes the labels and nothing
   else — no email of any kind fires, silently. **Do not confuse this with
   `ESCALATION_EMAIL_ENABLED`** (backend service, named in §3.2 step 5 and
   §4.2 step 4 above) — that one is a completely different flow: the AI's
   own autonomous complaint-detection escalation on WhatsApp/Web. The two
   names are one word apart, live in different services, and gate
   different things — setting one does not set the other.
2. Inbound mail is IMAP-polled, not pushed — a new email typically takes
   **roughly 1–2 minutes** to appear as a Chatwoot conversation after the
   customer sends it. If a test email doesn't show up instantly, that's
   the poll interval, not a failure.
3. **New subject line** → one auto-acknowledgement (`EMAIL_AUTOACK_ENABLED`,
   agent). The body is editable at **Settings → Knowledge → Settings →
   Tenant settings** (§2); the env var `EMAIL_AUTOACK_TEMPLATE` is only the
   fallback when nothing's saved there.
4. **Reply on an existing thread** → no additional auto-ack, just appended.
5. Once **you** (a human) reply from the Chatwoot UI, further auto-acks on
   that thread are suppressed.
6. A reply to an **escalation** email (dealer, PIC, or the customer's own
   acknowledgement) lands as a new conversation but gets **no** auto-ack —
   it carries the `[CASE-n]` correlation token, so it is recognised as a
   reply rather than a fresh enquiry. See §7.6.

### 7.3 Scenario A — routine email, no escalation needed
Customer sends the email; allow the ~1–2 minute IMAP poll (§7.2 step 2)
before expecting the conversation to appear. Auto-ack fires, conversation
assigned, you reply with a status update from the UI (your reply
suppresses further auto-acks), customer replies again later with no
duplicate ack, you resolve → rating survey.

### 7.4 Scenario B — customer wants a human / needs a substantive reply
Work it like any assigned conversation — **Ask Copilot** for a KB-grounded
draft, reply, resolve when done.

### 7.5 Scenario C — escalation (the two-thread flow, EM-7)
**Requires `EMAIL_ESCALATION_ENABLED=true`** (agent service, default off —
§7.2 step 1). With it off, everything below still lets you apply the
labels, but no email fires — confirm this flag before promising a customer
anything below happens. **Label order also matters and changes what fires
once the flag is on — this is the single most important operational fact
in this guide.** The handler reads whatever labels are present on the
conversation **at the moment `escalate` is applied**. Apply `escalate`
before the department label and the PIC leg silently does not fire for
that trigger (a real, documented negative case, not a bug) — only the
customer ack goes out.

1. Apply the **`dept_<slug>` label first** (e.g. `dept_sales` —
   pre-provisioned; ask an admin which department slugs have a PIC
   configured, since an unmapped one only logs
   `escalation_notifier_no_pic_for_dept` and sends no PIC mail).
2. **If a dealer is involved**, also apply a **`dealer_<slug>` label**
   before `escalate` — this must be **created first** under
   **Settings → Labels**, named to match the dealer's routing-table slug
   **exactly** (a label with a space, or a mismatched slug, silently
   resolves nothing). Dealer routing is edited as a **list of member
   emails** at **CRM → Escalation Routing**; every member on that list
   receives the forward.
3. Apply **`escalate`** last.
4. Up to three independent, best-effort emails fire:
   - **Customer acknowledgement** — subject `Update on your case: <first
     line of the customer's message>`. Never carries a `[CASE-n]` tag —
     the customer thread always stays clean.
   - **PIC email** — subject `[Escalation] <title>`, CC's the department's
     configured "relevant personnel" list when `ESCALATION_CC_PIC` is true
     (the default).
   - **Dealer forward** — subject `[Escalation - Dealer Forward] <title>`,
     to every member email on the dealer's list.
5. The conversation gains a `dealer_escalated_at` custom attribute the
   first time a `dealer_<slug>` label appears — idempotent, never
   overwritten, and this part fires **on any channel**, not just Email
   (it's a separate, always-on stamp used for BI turnaround-time
   reporting).
6. **Re-triggering:** the fan-out is edge-triggered — it runs once per
   escalation. Any other change to the conversation while `escalate` is
   still on it (a label, a custom attribute, a reopen) sends nothing more;
   an `escalation_notified_at` custom attribute records that this
   escalation has already gone out. Removing `escalate` clears that
   attribute, so removing and re-adding the label **does** send the ack and
   PIC mail again — a case escalated, worked, and escalated again later is
   a real second escalation. Toggling the label in front of a client still
   sends a second round of real email.
7. **No contact email on file** → no customer ack (nothing to send it to),
   but the PIC mail still goes out normally, no error shown.

### 7.6 Scenario C continued — the dealer/PIC reply comes back

This is the part the 08-04 guide didn't have at all. Requires
`ESCALATION_REPLY_LINKING_ENABLED` (agent), `ESCALATION_REPLY_TO_TEMPLATE`
(backend) — empty disables the whole reply loop, mail goes out untagged —
and, separately from any config file, **`message_created` must be added to
the Chatwoot account webhook's subscribed events**
(Settings → Integrations → Webhooks). Miss that last one and the reply
loop fails completely and silently — no error anywhere, the agent simply
never receives the dealer's reply.

1. A dealer/PIC replies to the escalation email without editing the
   subject. Chatwoot has no way to thread it onto the original case on its
   own — it lands as a brand-new, throwaway conversation. Allow the same
   ~1–2 minute IMAP poll (§7.2 step 2) before checking for it — a reply
   that hasn't shown up after a few seconds isn't necessarily lost.
2. The **original** conversation gains a private note that starts with
   `Reply from <name> <<email>>:` followed by the reply text with quoted
   trail/signature stripped — no "On ... wrote:" block.
3. **If `ESCALATION_REPLY_DRAFT_ENABLED` is also on** (agent): a second
   private note appears, titled exactly `Suggested customer reply (draft —
   review before sending):`, with an AI-drafted customer-facing reply
   beneath it. **You review and send this yourself** — it never goes out
   automatically; nothing here posts to the customer without a human
   clicking send.
4. The original conversation also gains an `escalation_replied` label and an
   `escalation_replied_at` timestamp. **A second reply from the same dealer
   address after that stamp is silently not linked** — the stamp gates the
   internal-reply path so a second note never piles on top of the first.
   (The names are deliberately not `dealer_*`: anything in the
   `dealer_<slug>` label namespace is read as a real dealer slug by the
   BigQuery reporting mapping and by the `dealer_escalated_at` stamper.)
5. The throwaway conversation the reply landed in gets labelled
   `escalation_reply` and auto-resolved — you don't need to do anything
   with it. **The dealer receives nothing back from the CRM**: the
   correlation token on their mail suppresses the customer-facing
   "Dear Customer" auto-acknowledgement, and closing that conversation's
   lifecycle before resolving it suppresses the "rate our support agent
   from 1 to 5" survey. Neither belongs in an external dealer's mailbox.
6. **The original case is not re-escalated by any of this.** The linker's
   writes are ordinary conversation updates and the case still carries
   `escalate`, so without the §7.5 step 6 guard each reply would send the
   customer another `Update on your case:` email and the PIC another
   `[Escalation]`.
7. **If a customer instead replies to their own acknowledgement email**
   (not the dealer/PIC mail), the reply comes back as a normal **public
   incoming message on their own case**, reopening it exactly like a fresh
   message would — not a private note. No `escalation_replied_at`/
   `escalation_replied` gets set for a customer reply; those are
   internal-reply only. They get no duplicate acknowledgement either.

### 7.7 SLA breach alerts (new since 08-04)
Requires `SLA_ENGINE_ENABLED`, `SLA_ALERT_EMAIL_ENABLED`,
`SLA_ALERT_NOTE_ENABLED` (all backend), and the Email inbox's id listed in
`SLA_INBOX_IDS` — the SLA scan is otherwise scoped to a single inbox and
will never look at Email at all if that inbox isn't listed. The value
**replaces** that default scope rather than extending it, so list the main
inbox id alongside the Email one or the main inbox stops being scanned.

1. On breach, the department's PIC (resolved from the conversation's
   `dept_<slug>` label, same routing table as escalation) receives an
   email subject `[SLA] SLA_BREACH_NO_RESPONSE on case <N>`.
2. The conversation gets a private note starting `⚠️ SLA breach
   (SLA_BREACH_NO_RESPONSE) on case <N>.`
3. A second scan without any activity in between does **not** re-send —
   the audit trail already has an entry for that breach type on that
   conversation.

### 7.8 What's not usable yet on Email
- The reply loop depends on `message_created` being subscribed on the
  Chatwoot account webhook — a setting made in the Chatwoot UI itself, not
  in any tenant `.env` file, and easy to miss. (SLA alerts do **not**: the
  SLA engine polls the conversations API on a timer and never reads a
  webhook.)
- Backend-sent mail (escalation, acks, SLA alerts) needs its **own**
  `SMTP_HOST`/`SMTP_USER`/`SMTP_FROM` set — these are read independently
  from Chatwoot's own `SMTP_ADDRESS`/`SMTP_USERNAME`/`MAILER_SENDER_EMAIL`
  block. Setting only the Chatwoot names leaves the backend's mail sender
  silently no-op — no email, no log line.
- The sender identity in the one live test run was a Gmail test relay, not
  a Proton-branded address — worth resolving before any client-facing run.
- Department slugs used for escalation routing (`dept_sales`, `dept_cs`,
  etc.) are a **separate taxonomy from the `case_category` labels** used
  for reporting — don't assume every case-category label has a matching
  PIC record.

---

## 8. Cross-channel scenario — same customer, four touchpoints

> A customer first messages WhatsApp about a delivery delay, gets a routine
> answer from the bot and moves on. A week later they open the website chat
> widget with a follow-up question the bot can't resolve, and an agent hands
> off. Two weeks after that they call in asking for a status update, and
> the AI (having no memory of the earlier channels) treats it as a fresh
> inquiry. Finally, the human who picks up the call transcript decides the
> case needs the dealer's attention and escalates it by email.

1. **WhatsApp (§3.3):** bot answers, case auto-closes.
2. **Web chatbot (Web chatbot section, Scenario B):** the AI on this
   channel has no awareness the WhatsApp conversation ever happened — it's
   a fresh bot session grounded only in what's said in *this* conversation.
   It can't resolve the follow-up, hands off; an agent works it and
   resolves it.
3. **Phone (§5/§6), two weeks later:** same story — the AI answering the
   call has no memory of either earlier conversation. It treats this as a
   brand-new inquiry unless the caller explicitly brings up the earlier
   case and the human eventually assigned notices.
4. **As the agent picking up the call transcript:** open the customer's
   contact record → **Previous Conversations** to see the WhatsApp and web
   chat history and confirm status before replying. If your tenant has
   `RBAC_ENABLED` and the `customer360.view` permission, **Customer 360**
   (left sidebar) can also pull up the same phone number's conversation
   history in one search — but any vehicle/DMS panel on that page is
   fabricated demo data unless a real DMS integration exists (it doesn't,
   today), so don't read a "vehicle" field there as authoritative.
5. **Escalation to the dealer:** this only works as a two-thread email flow
   if the call's Chatwoot conversation happens to be on the Email inbox —
   it isn't (it's the `Channel::Api` phone inbox, §6.4). The agent's real
   option here is to open (or start) an **Email**-channel conversation with
   the customer, apply `dept_<slug>` then `dealer_<slug>` then `escalate`
   on *that* conversation, and follow §7.5 from there — not expect the
   phone conversation itself to escalate.
6. **The single biggest cross-channel gap, unchanged from 08-04:** there is
   still no automatic link between a phone number/vehicle and every prior
   case across channels — **Previous Conversations** (and, where enabled,
   Customer 360's conversation-history search) is the whole of what exists,
   and it depends on the customer using the same contact identity every
   time.

---

## 9. Quick-reference cheat sheet

| Task | Where to click |
|---|---|
| Draft a reply with AI help | Reply box → AI-actions → **Ask Copilot** |
| Get a suggested answer to the last message | Reply box → AI-actions → suggestion icon |
| Categorize a case | Right sidebar → **Conversation Actions → Labels** |
| Escalate an **Email** case to PIC/dealer | Labels: `dept_<slug>` → (`dealer_<slug>`) → `escalate`, **in that order** — needs `EMAIL_ESCALATION_ENABLED=true` |
| Escalate a WhatsApp/Web/Phone case | No automatic email — contact the PIC/dealer directly; only the AI's own complaint detection notifies anyone automatically on these channels |
| See a dealer's reply to an escalation | Private note on the **original** case, `Reply from ... :` — plus a second note with a draft reply if `ESCALATION_REPLY_DRAFT_ENABLED` |
| See who reassigned a case | Standalone **Audit Log** icon |
| Look up PIC/dealer routing | Standalone **Escalation Routing** icon |
| Look up a customer by phone/vehicle | Standalone **Customer 360** icon |
| See/edit SLA targets, incl. Tier-2 and warning thresholds | Standalone **SLA Policies** icon |
| Edit customer-facing email templates | **Settings → Knowledge → Settings → Tenant settings** |
| Edit the bot's persona/disclaimer/lifecycle messages | **Settings → Knowledge → Settings** (assistant dropdown) |
| See which agent owns which case at a glance | **Cases List → Agent column** (list view only — see §10 for what this doesn't cover) |
| See a customer's history across channels | Contact panel → **Previous Conversations** |

---

## 10. Known limitations — what to tell the customer

| Channel | Limitation | Why | What to tell the customer |
|---|---|---|---|
| WhatsApp / Web / Phone | `escalate` label alone doesn't notify a PIC/dealer | The email side of escalation is coded as Email-channel-only | "I'm looping in the right team directly — I'll follow up once I hear back," then actually contact them outside the CRM |
| All chat/voice channels | The AI has no memory across channels | Each bot session is scoped to its own conversation; there's no shared customer memory store | "Let me pull up your earlier conversation so I have the full picture" — then actually use Previous Conversations before answering |
| Email | Applying escalation labels does nothing at all unless `EMAIL_ESCALATION_ENABLED=true` | The whole two-thread flow is behind one master switch (agent service, default off) — easy to confuse with the similarly-named `ESCALATION_EMAIL_ENABLED` (backend, a different flow entirely) | If labels don't seem to send anything, check this flag with an admin before assuming the reply loop or routing table is broken |
| Email | Label order matters (`dept_` then `dealer_` then `escalate`) | The handler reads labels present at the moment `escalate` is applied | Internal-only; don't promise a specific order to a customer, just follow it yourself |
| Email | A second dealer reply after the first is linked doesn't post a new note | The stamp gates the internal-reply path so a note never piles on | If a dealer says they replied twice, check the mailbox directly, not just the case |
| Email | Reply loop depends on a webhook subscription set in Chatwoot's UI, not a config file | Easy for an operator to miss during setup | If dealer replies "aren't showing up," ask an admin to check Settings → Integrations → Webhooks before assuming the feature is broken |
| Voice bot / Phone | Nothing past the basic greeting/KB/CSAT loop has been run against a real call | Package C shipped against unit tests and fakes only | Don't promise live transcript, recording, classification, or a real transfer works flawlessly — treat every claim as "should work, unconfirmed" until someone runs the real-call verification |
| Phone | No RSA-specific after-hours transfer bypass exists | The shipped handoff resolver applies the same business-hours gate to every reason, with no exception | Don't tell a caller reporting an accident after hours that they'll be automatically transferred to a 24/7 line — that isn't built |
| Phone | An unanswered/busy/failed transfer hangs up rather than returning to the bot, and its "we'll call you back" line schedules nothing automatically | Documented design choice (Task 6), not a bug | "I can see your call didn't connect to a specialist — let me personally make sure someone follows up," then actually work the `unanswered_handoff` tag |
| Phone | Bahasa Melayu reliability on live calls is an open, unresolved issue | Separate voice pipeline from the text-channel fix; root cause not found as of this writing | If a Bahasa-speaking caller gets stuck in English, apologize and switch to manual handling — don't call it fixed |
| Cases List | The `Agent` column was added to the **Cases List** table only | The matching change to the conversation-card assignee name in the regular inbox/conversation list was explicitly descoped | Don't expect to see the assignee's name on a conversation card in the normal inbox view — check the Cases List, or open the conversation, instead |
| Customer 360 | Any vehicle/DMS data shown is fabricated demo data | No real DMS/TSP integration exists yet (Phase 1 ships no adapter) | Never read a vehicle field on this page to a customer as if it's their real vehicle record |
| Social (FB/IG) | Still can't connect a channel | Meta Business verification incomplete, unchanged since 08-04 | "We don't support Facebook/Instagram messages yet" |
