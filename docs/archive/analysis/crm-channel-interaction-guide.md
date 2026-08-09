# CRM Channel Interaction Guide — End-to-End Walkthroughs

**Audience:** customer-service agents and team leaders using the Chatwoot CRM
day-to-day, and anyone training them. This is a **"how do I actually work a
case"** guide — not a QA checklist.

**Companion docs:**
- `crm-channel-ui-testing-guide.md` — per-step build status (✅/⚠️/❌) for
  every claim made here; if a step below says "not live yet," that doc has
  the detail on why and what's blocking it.
- `crm-process-flow-runbook.md` — how the automated lifecycle (disclaimer →
  idle → resolution → survey) is configured under the hood.
- `proton-crm-gap-analysis-2026-07-27.md` — full gap list against the SOP.

**As of 2026-08-04:** WhatsApp is the only channel fully live end-to-end
(bot + human handoff + escalation). Social Media (Facebook/Instagram) is
blocked on Meta Business verification. Email inbound is blocked on SMTP/IMAP
credentials, but email **escalation** (the two-thread customer-ack +
internal-forward flow) is now built — see §5.3. Phone/IVR is live for
AI-handled calls but has no real human hand-off yet. Each section below is
written as the **target workflow**, with a status note up front so you know
what you can actually run today.

---

## 1. How the channels map to the CRM

| Customer touchpoint | Chatwoot inbox type | AI involvement | Status today |
|---|---|---|---|
| WhatsApp message | Twilio-channel inbox | Full bot: disclaimer, KB-grounded Q&A, idle/resolution/survey lifecycle, live handoff | ✅ Live |
| Facebook / Instagram DM or comment | Native Social inbox | None by SOP design — goes straight to a human agent | ❌ Blocked (Meta verification) |
| Email | Native Email inbox | Auto-acknowledgement only, no AI Q&A | ❌ Blocked (mailbox not wired) |
| Phone call | Generic API-channel inbox (via Twilio + Gemini Live voice bridge) | Full voice bot: KB Q&A, rating survey, business-hours-aware RSA routing | ✅ Live for AI-handled portion; ❌ no real human transfer yet |

Every channel funnels into the **same Chatwoot conversation list** — as an
agent you don't need a different mental model per channel, just different
expectations about how much the bot has already done before it reaches you.

---

## 2. The agent's toolkit (same across every channel)

Before the channel walkthroughs, here's what you'll actually click, regardless
of which channel the conversation came in on:

| Action | Where |
|---|---|
| See if the bot already answered | Conversation timeline — bot messages are visible like any other message |
| Ask the AI to draft a reply for you | Reply box → AI-actions menu → **Ask Copilot** (needs the conversation's KB grounded first) |
| Get a suggested answer to the customer's last message | Reply box → AI-actions menu → suggestion icon ("FAQ Assist") — shows a `Sources:` line when grounded |
| Categorize the case | Right sidebar → **Conversation Actions → Labels** |
| Escalate to a PIC/dealer | Add the **`escalate`** label — triggers an email (and WhatsApp alert, if enabled) per the Escalation Policy mapping |
| Reassign to another agent | Click the assignee name in the conversation header |
| Resolve | Status control, top-right of the conversation |
| Check who reassigned what | Standalone **Audit Log** icon (left sidebar, needs `audit.view` permission) |
| See a customer's history across channels | Contact panel → **Previous Conversations** |

---

## 3. WhatsApp — end-to-end

**Status: ✅ fully live.** This is the channel to demo or train on first — every
step below works today.

### 3.1 What happens before an agent ever sees the case

1. Customer messages the connected WhatsApp number.
2. Bot immediately posts the AI-use disclaimer.
3. Bot replies in the customer's own language, grounded in the tenant's
   uploaded FAQ/KB content when the question matches.
4. If outside business hours, the bot instead posts the hours/website
   auto-reply.
5. If the customer goes idle, the bot warns, then auto-closes and asks
   "Is your case resolved? YES/NO."
6. `YES` → 1–5 AI-performance rating survey → conversation auto-closes and
   gets a `category_*` label. **You never see this case as an agent unless
   the customer asks for one, or answers NO.**

### 3.2 Scenario A — routine question, bot handles it fully

> Customer: *"Apa spesifikasi Proton X70?"*
> Bot answers from the uploaded KB, in Bahasa.
> Customer goes quiet. 10 min later → idle warning. 5 more min → auto-close +
> "Is this resolved?" Customer replies `YES` → rating survey → `4` → conversation
> closes itself with `category_sales` (or similar) applied automatically.

**Your role as an agent:** none — this never lands in your queue. If you're
spot-checking quality, open **Reports → CSAT** to see the rating, or open the
closed conversation directly to read the transcript and confirm the label is
correct.

### 3.3 Scenario B — customer asks for a human

> Customer: *"I want to talk to a real person about my service booking."*
> Bot recognizes the request and hands off.

1. The conversation's **Assignee** (conversation header) automatically
   becomes whichever online agent has WhatsApp set as their `Primary channel`
   (**Settings → Inboxes → *(WhatsApp inbox)* → Collaborators → Agent Channel
   Priorities**).
2. **You must acknowledge within 2 minutes** — open the conversation and send
   at least an initial reply. This is a soft SLA, not a hard block, but it's
   what the SOP measures.
3. Work the case as a normal chat: reply directly, or use **Ask Copilot** if
   you want an AI-drafted answer grounded in the KB to start from.
4. When done, set status to **Resolved**. This triggers the
   agent-performance 1–5 survey (a different variant from the AI-performance
   one in Scenario A).
5. A Team Leader can reassign at any point by clicking the assignee name —
   this is logged in **Audit Log**.

### 3.4 Scenario C — case needs escalation to a dealer/PIC

> During Scenario B, you determine this needs the dealer's attention (e.g. a
> warranty dispute you can't resolve directly).

1. Open the conversation → right sidebar → **Conversation Actions → Labels**
   → add **`escalate`**.
2. The mapped PIC (per the tenant's `PIC_MAP_JSON`, ask engineering if you
   don't know the mapping) receives an email, and a WhatsApp alert if that's
   enabled for this tenant.
3. **Known gap:** if the customer sent a photo/video earlier in the thread,
   it is **not** forwarded in the escalation email — text only today. If the
   PIC needs to see the media, forward it manually or note it in your reply.
4. Once the PIC resolves the underlying issue, close the loop as you would
   any other case.

### 3.5 What's not usable yet on WhatsApp

- **WA-13 vehicle lookup** — there's no "find this customer's vehicle" tool;
  you're relying on whatever the customer tells you in the conversation until
  Customer 360/DMS integration lands.
- Voice notes/photos: media understanding is deployed but not yet confirmed
  against a real WhatsApp number — if a customer sends a voice note and the
  bot doesn't react to it, that's worth flagging, not expected.

---

## 4. Social Media (Facebook & Instagram) — end-to-end

**Status: ❌ blocked today** — Meta Business verification hasn't completed,
so there is no way to connect a Page/Account yet. The workflow below is the
**target design**, useful for training before the channel goes live, and for
everything downstream of the connection (which is otherwise ready).

### 4.1 How it will work

Unlike WhatsApp, the SOP deliberately has **no AI auto-answer step** on
Social — every inbound DM or comment goes straight to a human once it's
within business hours.

1. Customer DMs or comments on the connected FB Page / IG account.
2. A new conversation appears in the Social inbox, tagged `facebook` or
   `instagram`.
3. Outside business hours → the same hours/website auto-reply as WhatsApp
   posts (shared Business Hours mechanism).
4. Within business hours → routed straight to whichever online agent has
   Social Media as their `Primary channel`.

### 4.2 Scenario — customer comments on a promo post

> Customer comments "How much is the X70 this month?" on a Facebook post.

1. Conversation appears in the Social inbox tagged `facebook`.
2. You (or whoever has Social as priority-1) must acknowledge **within 2
   working hours** — this is a measured SLA (**Reports → SLA**), unlike
   WhatsApp's softer 2-minute expectation.
3. Reply directly — no bot draft available on this channel by design.
4. Mark Resolved → a rating-request DM is sent automatically; result lands
   in **Reports → CSAT**.

### 4.3 What to tell people asking about this channel

There's genuinely nothing to click today — **Settings → Inboxes → Add
Inbox** has no working Facebook/Instagram path until Meta verification
completes. Don't spend training time on the connection step itself; the
downstream agent workflow (steps 2–4 above) is otherwise unchanged from any
other assigned-conversation flow.

---

## 5. Email — end-to-end

**Status: ❌ blocked today** — the mailbox isn't wired for live inbound mail
(SMTP/IMAP credentials pending). The auto-ack and suppression logic is
code-complete and tested, just not observable in a live inbox yet. Workflow
below is the target design.

### 5.1 How it will work

1. Customer emails the support address (e.g. `e.mascentre@pronet.my`).
2. **New subject line** → one auto-acknowledgement is sent, then the
   conversation is assigned to an agent for the next business hour.
3. **Reply on an existing thread** → no additional auto-ack; the message is
   just appended to the conversation.
4. Once **you** (the agent) reply from the Chatwoot UI, further auto-acks on
   that thread are suppressed — the customer won't get a duplicate "we got
   your email" if they reply again.
5. You're expected to give a substantive update within **4 working hours**
   (**Reports → SLA** tracks the breach).
6. Resolve → 1–5 rating survey.

### 5.2 Scenario — customer emails about a delayed part

> Customer emails a new thread: *"My spoiler part hasn't arrived after 3
> weeks."*

1. Auto-ack sent immediately ("we've received your email, we'll respond
   within X hours").
2. Conversation assigned to an agent.
3. You reply with a status update from the Chatwoot UI — this is your one
   reply that suppresses further auto-acks on the thread.
4. Customer replies again on the same thread two days later — no duplicate
   ack fires; their message just appends.
5. You resolve once the part ships; survey goes out.

### 5.3 Escalation on email — now built (2026-08-04), partially configured

The two-thread format the SOP wants — a customer-facing acknowledgement and
a **separate** internal email to the PIC/dealer, not CC'd on one thread — is
now built and deployed. Same trigger as every other channel:

1. Open the email conversation → right sidebar → **Conversation Actions →
   Labels** → add **`escalate`**.
2. Up to three independent emails fire:
   - **Customer acknowledgement** — a reassurance email to the customer, a
     separate thread from anything internal. ✅ live.
   - **PIC/department email** — goes to whoever is mapped to a
     `dept_<slug>` label already on the conversation (the same taxonomy
     labels used for case categorization elsewhere), with a real transcript
     excerpt. ⚠️ **not yet configured on proton** — no department→PIC
     mapping exists yet, so this leg silently does nothing until it's set
     up (see "STILL TODO" below).
   - **Dealer forward** — goes to whoever is mapped to a `dealer_<slug>`
     label on the conversation. ⚠️ **not yet configured** either, same
     reason.
3. If neither a `dept_` nor `dealer_` label is present on the conversation,
   only the customer ack fires — there's nothing to route the internal
   copy to.

**STILL TODO (ops, not engineering):** the department→PIC and dealer→email
mappings need to be filled in before the internal legs of this actually
notify anyone. Until then, keep escalating a case to the PIC/dealer
manually outside the CRM as before — the `escalate` label alone doesn't yet
guarantee internal delivery, only the customer sees a response.

**Update (2026-08-04):** the self-service configuration screen for these
mappings is now live — no more editing raw config. Open the standalone
**Escalation Routing** icon in the left sidebar (RBAC-gated; ask an admin if
you don't see it) to add/edit PIC and dealer entries directly. The mappings
themselves still need to actually be filled in for proton before the
internal email legs will notify anyone — the UI existing doesn't mean the
data is populated yet. See §10 below before relying on this in front of a
customer or during a demo.

---

## 6. Phone / IVR — end-to-end

**Status: ✅ live for AI-handled calls; ❌ no real human transfer.** There is
**no IVR-configuration screen in Chatwoot** — call logic lives entirely in
the Twilio + Gemini Live voice bridge. As an agent, you only ever interact
with the **artifact** a call leaves behind: a conversation with the
transcript, same as any other inbox.

### 6.1 How it works

1. Customer calls the support number.
2. AI (female voice) answers — 24/7, business-hours-aware.
3. Within business hours, AI can also route/hand off; outside hours it still
   answers, since no agents are available anyway.
4. If the queue is busy (>10s ring) and it's not an RSA situation, AI gives a
   bilingual EN/BM prompt to wait or use the e.MAS app.
5. If the customer needs a human, sales, repair, or reports an accident/RSA
   situation, AI is *supposed* to hand off to the right team — **today this
   hand-off is mocked**, not a real transfer. Don't promise a customer a live
   transfer will actually happen.
6. Call ends with a 1–5 rating survey.
7. A conversation appears in Chatwoot with the full transcript, usually
   updating close to real-time during the call — check with engineering
   which inbox `CHATWOOT_INBOX_ID` points to for this tenant, since there's
   no dedicated "Call" inbox.

### 6.2 Scenario A — vehicle spec question, fully AI-handled

> Customer calls, asks "What's the range on the X70 EV variant?"
> AI answers from the same KB WhatsApp uses, in the language the customer
> spoke (English is confirmed working; **Bahasa on calls had a known bug**
> — separate voice pipeline from the text-based language fix. A mitigation
> shipped 2026-08-04 that nudges the AI to re-check the reply language on
> every turn instead of getting "stuck" on whichever language it used
> first — deployed to proton, but not yet confirmed against a real call.
> If a caller still gets stuck-in-English replies after this date, that's
> worth reporting as a regression, not the previously-known behavior).
> Call ends, customer rates 5.

**Your role:** none in real time. If QA'ing, open the conversation in
Chatwoot afterward and read the transcript against **Reports → CSAT** for the
rating.

### 6.3 Scenario B — after-hours road-side-assist call

> Customer calls outside business hours reporting an accident.

1. AI recognizes the RSA/accident intent even out of hours and is designed
   to transfer directly to the 24/7 RSA line, bypassing the normal
   agent-only-hours restriction. This routing logic is confirmed live in the
   orchestrator.
2. Once a human picks up the RSA case, log/track it on the dedicated
   **Roadside-Assistance incident log** page (separate from the call itself)
   — note this page is code-complete but not yet deployed on every tenant;
   confirm it's live before relying on it in a real incident.
3. Cross-check the transcript in the Chatwoot conversation afterward for a
   full record of what the customer reported.

### 6.4 What not to promise customers on a call today

- **No real transfer to a human** — if a customer insists on speaking to a
  person, the call does not actually connect them; know what actually
  happens on your setup (continues with AI, or drops) before you're on a
  live call, since this is presenter-observed mocked behavior.
- **No call recording** — there's nothing to pull for QA/compliance disputes
  yet.
- **DTMF vs. conversational routing is still an open decision** — don't
  train agents on a "press 1 for sales" menu; it may never ship that way.

---

## 7. Cross-channel scenario — same customer, multiple channels

> A customer first messages WhatsApp about a warranty issue, the bot can't
> resolve it and hands off to an agent, the agent escalates to the dealer via
> email, and two weeks later the same customer calls in asking for a status
> update.

1. **WhatsApp (Scenario 3.3/3.4):** case opens, escalates to PIC.
2. **Phone call (2 weeks later):** the AI answers the call fresh — it has
   **no memory of the WhatsApp case**; it will treat this as a new inquiry
   unless the customer explicitly references it and the human agent manually
   connects the dots.
3. **As the agent picking up the call transcript:** open the customer's
   contact record → **Previous Conversations** to pull up the original
   WhatsApp thread and confirm status before replying.
4. **Known gap:** there's no unified Customer 360 view that automatically
   links a phone number/vehicle to prior cases across channels — this is the
   single biggest cross-channel gap (see the gap-analysis doc). Today,
   **Previous Conversations** on the contact panel is the only cross-channel
   continuity you get, and it depends on the customer using the same
   phone/contact identity on both channels.

---

## 8. Quick-reference cheat sheet

| Channel | Bot pre-handles routine cases? | Your SLA to acknowledge | Escalation mechanism | Live today? |
|---|---|---|---|---|
| WhatsApp | Yes — full KB Q&A + lifecycle | 2 min after handoff | `escalate` label → email/WhatsApp alert (text-only) | ✅ |
| Social (FB/IG) | No — always human | 2 working hours | Same `escalate` label mechanism (once channel connects) | ❌ blocked |
| Email | No — ack only | 4 working hours | ✅ two-thread `escalate` label (customer ack live; PIC/dealer legs need mapping config) | ❌ blocked (inbound mail not wired) |
| Phone/IVR | Yes — full voice Q&A | N/A (real-time call) | RSA auto-route works; general hand-off mocked | ✅ AI portion only |

---

## 9. Known limitations to set expectations on, right now (2026-08-04)

- Facebook/Instagram: no channel connection possible yet.
- Email: no live inbound mail yet.
- Phone hand-off to a human: mocked, not real.
- Email escalation: two-thread format is built and deployed (customer ack
  works), but the PIC/dealer internal legs need a department→PIC and
  dealer→email mapping filled in before they actually notify anyone — see
  §5.3.
- No Customer 360 / vehicle lookup on any channel.
- IVR language-matching (Bahasa) had a known open bug; a per-turn-reminder
  mitigation shipped 2026-08-04 and is deployed to proton, but not yet
  confirmed against a real call — treat as "should be fixed, needs
  verification" rather than fully closed.
- Category picker: main→sub cascading dependency (select a division →
  only that division's subcategories selectable) is built as a fork patch,
  deployed to proton — should be live in the conversation's category
  fields, but hasn't had a manual browser confirmation yet.

See `crm-channel-ui-testing-guide.md` for the authoritative, per-step status
table these are drawn from, and `proton-crm-gap-analysis-2026-07-27.md` for
the full requirement-level gap analysis.

---

## 10. New this build (2026-08-04) — smoke-test before relying on any of it

Deployed to `default` and `proton` overnight 2026-08-04, tested and reviewed
in code but **not yet manually clicked through in a browser**. Run this
checklist before a demo or before telling an agent/PIC it's ready:

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Escalation Routing page reachable | Left sidebar → **Escalation Routing** icon (RBAC-gated on `escalation.manage`) | Page loads, lists any existing PIC/dealer entries |
| 2 | Can add a PIC entry | On that page, add a PIC department entry, save | Entry persists after a page refresh |
| 3 | Customer 360 page reachable | Left sidebar → **Customer 360** icon (RBAC-gated on `customer360.view`) | Page loads with a single search box |
| 4 | Phone-number search returns something | Search a known customer's phone number | Contact + their cross-channel conversation history shown, or an empty-but-no-error result if that number has no contact yet |
| 5 | FAQ bulk-upload works | **Knowledge → FAQs** page → new "Bulk upload (CSV)" button → upload a small sample CSV (`question,answer,keywords,tags` columns) | A created/errors count appears, list refreshes with the new entries |
| 6 | Zammad confirmed gone | `docker ps` on each tenant on the VM | Zero containers with `zammad` in the name, on `default`, `proton`, and `wahchan` |
| 7 | No leftover Zammad references in code | `grep -ri zammad agent/ backend/apps/backend/src/` from the repo root | No output |

**Known issue found during this deploy, not yet resolved:** `wahchan-backend`
(the WhatsApp AI service) is currently crash-looping — its tenant config is
missing the Vertex AI / Gemini API key setup that `default` and `proton`
have. `wahchan-agent` and its Chatwoot instance are unaffected. This needs an
engineering decision (share the existing service-account key or issue
`wahchan` its own) before its AI features will work — don't rely on WhatsApp
AI behavior on `wahchan` until this is fixed.

**Not yet enabled anywhere (built, shipped disabled by default):** the
round-robin per-agent ticket cap (`routing_max_concurrent_per_agent`). Left
at `0` (unlimited) on every tenant — nothing to smoke-test here unless it's
deliberately turned on for a tenant later.
