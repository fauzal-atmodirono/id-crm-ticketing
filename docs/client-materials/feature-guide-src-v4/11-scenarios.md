# End-to-End Scenarios
<!-- TRAINING: audience=agent -->

## Scenario 1: WhatsApp inquiry to resolution
<!-- TRAINING: audience=agent, exercise -->

1. A customer messages Proton's WhatsApp number asking about the price and
   availability of a test drive for the e.MAS 7. Chatwoot creates a new
   conversation on the WhatsApp inbox, which runs in **Auto mode** — the
   tenant-wide default — so no human review step applies here unless this
   particular inbox has been explicitly switched to Suggest (see the AI
   Assistant Behaviour chapter's Suggest mode vs. Auto mode section).
2. The AI assistant answers directly, grounded in the knowledge base, and
   sends the reply itself — this is what Auto mode means day to day (see
   the Conversations chapter's AI auto-draft and suggest-vs-auto mode
   section).
3. The customer confirms they'd like to book a test drive. Because
   arranging a booking needs a person, the conversation reopens and is
   assigned to an available agent whose channel priorities include
   WhatsApp (see the Channel Playbooks chapter's WhatsApp section,
   Scenario B).
4. The agent picks it up, confirms the booking details with the customer
   — using **Ask Copilot** for a knowledge-grounded draft if useful — and,
   once everything is confirmed, marks the conversation **Resolved** (see
   the Conversations chapter's Resolving, snoozing & transcripts section).
5. On resolve, the customer receives Chatwoot's own native satisfaction-
   rating request — not the assistant's own survey message, which is off
   tenant-wide on this account — and their 1–5 rating shows up later in
   the CSAT report (see the AI Assistant Behaviour chapter's Lifecycle
   messages section and the Reports chapter).

[[SCREENSHOT: ch11-scenario1-whatsapp | A WhatsApp inquiry answered directly by the AI assistant, then handed off for a human agent to continue]]

## Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report
<!-- TRAINING: audience=agent, exercise -->

1. A customer emails in about a recurring charging fault that wasn't fixed
   at their last service visit. The conversation lands on the Email inbox
   (see the Conversations chapter's Conversation inbox & views section).
2. The agent handling it decides the case needs the dealer's attention and
   applies the **`dept_aftersales`** label first, then the relevant
   dealer's label, then **escalate** last — in that order, since the
   handler only picks up whichever department/dealer labels are already
   on the conversation at the moment `escalate` is applied (see the
   Conversations chapter's Labels section).
3. The CRM automatically sends the customer a short acknowledgement email
   and forwards the case details to the dealer group's members by email,
   using the contacts set up in Escalation Routing; the dealer's
   turnaround clock starts at the same moment (see the AI Assistant
   Behaviour chapter's Escalation labels & the escalation email section
   and the Administration chapter's Escalation Routing section).
4. Two days later, the dealer emails back confirming the fault has been
   repaired. Within a couple of minutes, the agent sees a new private
   note on the **same** conversation starting `Reply from ` with the
   dealer's update, and directly beneath it a second note — `Suggested
   customer reply (draft — review before sending):` — with an AI-drafted
   update already written (see the Conversations chapter's Escalation
   replies section).
5. The agent reads the draft, adjusts a couple of words, sends it as their
   own reply to the customer, and marks the conversation **Resolved**.
6. During the weekly ops review, a supervisor opens the Weekly Report page
   and looks at the Dealer Escalation Turnaround table. Today it shows a
   fixed set of illustrative example figures rather than this account's
   real timings — the reporting connection behind it hasn't been switched
   on for this account yet — so the supervisor doesn't read this dealer's
   actual turnaround off that table. To know how long this specific case
   actually took, they'd check the case itself (see the Reports chapter's
   Weekly Report and Dealer escalation turnaround sections).

## Scenario 3: RSA call to Customer 360 follow-up
<!-- TRAINING: audience=supervisor -->

1. A customer calls Proton's support line reporting a breakdown on the
   toll road. The call is answered by the AI assistant, and the
   conversation appears in the Conversations view with the call transcript
   (see the AI Assistant Behaviour chapter's Voice bot behaviour section).
2. Because it's a roadside situation, an administrator with access to the
   RSA Incident Log page logs a new entry with the vehicle number, cause,
   and breakdown location (see the RSA Incident Log chapter's Logging an
   RSA incident section).
3. As towing is arranged and the vehicle reaches the dealer's outlet, the
   same administrator updates the incident record with the relevant
   timestamps (see the RSA Incident Log chapter's Incident statuses &
   updates section).
4. A few weeks later, the same dealer calls asking about that customer's
   vehicle history. An administrator with Customer 360 access opens
   **Customer 360**, searches by the vehicle number, and sees the
   completed RSA incident together with the customer's conversations in
   one place (see the Contacts chapter's Customer 360 section and the RSA
   Incident Log chapter's RSA in Customer 360 & reports section).
5. The same Customer 360 result also has a DMS/TSP block for that
   vehicle, but no live dealer-management-system adapter has been built
   into the platform yet, so on this tenant it shows a **Not connected**
   notice rather than a service history — the RSA incident and the
   customer's conversations are what the administrator actually has to go
   on today (see the Contacts chapter's Customer 360 section and the
   Integration Overview chapter's DMS/TSP section).

## Scenario 4: FAQ batch import to live bot answer
<!-- TRAINING: audience=admin, exercise -->

1. Ahead of an e.MAS 7 launch event, Proton's service team compiles a
   spreadsheet of 40 frequently asked warranty questions.
2. An administrator exports the spreadsheet as a CSV file and uses
   **Bulk upload (CSV)** under **Knowledge → FAQs** to import all 40
   entries in one go (see the Knowledge chapter's FAQs section).
3. Before the launch, the administrator opens **Knowledge → Playground**
   and asks a few of the same questions a customer might ask, to confirm
   the assistant answers correctly using the newly imported entries (see
   the Knowledge chapter's Playground section).
4. Satisfied with the answers, the administrator leaves the entries active.
   When the launch event starts and a real customer asks one of those
   questions on WhatsApp, the AI assistant answers it live, grounded in
   the newly imported FAQ (see the Conversations chapter's Suggest-a-reply
   and AI auto-draft sections, and the AI Assistant Behaviour chapter).
5. Any question the imported FAQs don't cover still falls back to a
   normal handoff to a human agent, the same as any other unanswerable
   question (see the AI Assistant Behaviour chapter's When the AI replies
   vs. hands off to a human section).

## Scenario 5: Weekly reporting routine
<!-- TRAINING: audience=supervisor, exercise -->

1. Every Friday afternoon, Proton's operations lead opens **Reports →
   Weekly Report** and sets the week picker to the period just finishing
   (see the Reports chapter's Weekly Report section).
2. They check **Per-Case Detail** at the bottom of the page — the one
   section here that's read live from Chatwoot's current conversations —
   for the actual cases behind the week.
3. They also open **Case Volume**, **Case Status Trend**, **Inquiry /
   Complaint / Feedback Detail — Departments & PIC**, **Call Centre & SLA
   Performance**, **Work-in-Progress / Case Aging**, and **Dealer
   Escalation Turnaround**, but treat every number on those sections as a
   placeholder: the reporting connection behind them hasn't been switched
   on for this account yet, so what's on screen is a fixed set of
   illustrative example figures, not this week's real activity — and
   nothing on the page marks them as examples today, so the operations
   lead has to know that going in (see the Reports chapter's Weekly Report
   section).
4. Because none of those sections reflect this account's real week yet,
   the operations lead does not read a conclusion off them for the client
   call — no "this dealer's turnaround has crept up," no "complaints were
   up this week." Anything they report comes from Per-Case Detail, or from
   asking their CRM administrator whether the reporting connection has
   been switched on since the last check.
5. Once that connection does go live — a step still pending for this
   account — this routine can move back to reading the full page. Until
   then, if the client needs a number beyond what Per-Case Detail shows,
   the operations lead asks their CRM administrator to arrange a bulk
   export from the CRM's live data instead (see the Integration Overview
   chapter's BI/reporting exports section).

[[SCREENSHOT: ch11-scenario5-weekly-report | Using the Weekly Report page for the weekly client meeting]]

## Scenario 6: New agent onboarding
<!-- TRAINING: audience=admin, exercise -->

1. Proton hires a new customer-service agent, Dian, to cover the
   After-Sales WhatsApp and email inboxes.
2. An administrator opens **Settings → Agents**, adds Dian with the
   **Agent** role, and an invitation is sent to her email address (see the
   Administration chapter's Agents section).
3. The administrator assigns her to the **After-Sales** team under
   **Settings → Teams**, so conversations can be routed to the team as a
   whole rather than to her individually at first (see the Administration
   chapter's Teams section).
4. The administrator makes sure she has access to the relevant WhatsApp
   and email inboxes under **Settings → Inboxes** (see the Administration
   chapter's Inboxes section).
5. Dian signs in for the first time using the credentials from her
   invitation, lands on the Conversations view, and — since her role is
   Agent — sees Conversations, Contacts, Knowledge, and **My status**,
   where she sets her own availability, but none of the administrator-only
   pages (see the Introduction chapter's Logging in and Roles: agent vs
   administrator sections, and the Administration chapter's Agent
   Availability & My Status section).

## Scenario 7: A customer replies to their own acknowledgement email

1. A customer emails in about a delayed part; the agent escalates it with
   a department label, a dealer label, and **escalate**, and the customer
   receives the short acknowledgement email (see Scenario 2, above).
2. Two days later, still waiting, the customer hits **Reply** on that same
   acknowledgement email and asks for an update, without changing the
   subject line.
3. Chatwoot has no way to thread that reply onto the original case on its
   own, so it briefly appears as a brand-new conversation on the Email
   inbox — the agent doesn't need to do anything with this one; the CRM
   resolves it automatically in the background.
4. **Correction from the previous edition:** on the **original**
   conversation, the customer's message does **not** appear as a normal
   incoming message. Chatwoot only accepts a synthetic incoming message
   on an Api-channel inbox, and this always runs on the Email channel, so
   that attempt is always rejected. What the agent actually sees is a
   private note prefixed `Customer's own reply (from <email>, could not
   be posted inline -- see conversation <id>):`, followed by the
   customer's own words — and the conversation still reopens by itself,
   exactly as before (see the Conversations chapter's Escalation replies
   section).
5. **What to actually do:** treat that private note as the customer's
   message — it is, verbatim, just delivered as a note rather than an
   inline bubble — and reply publicly to it the same way as any other
   reopened case. Don't wait for it to turn into an ordinary message; it
   won't. Nothing about the escalation itself (the ack, the PIC email,
   the dealer forward) fires again just because the customer replied.

## Scenario 8: Maintaining a dealer group
<!-- TRAINING: audience=admin, exercise -->

1. Dealer Kelapa Gading adds a second service advisor, Pak Rudi, who
   should also see escalated cases forwarded to that dealer, alongside the
   existing contact.
2. An administrator with the escalation-routing permission opens
   **Escalation Routing** and finds the **Dealer groups** section (see the
   Administration chapter's Escalation Routing section).
3. They click **Edit** on the Kelapa Gading dealer group's row, add Pak
   Rudi's email address to the comma-separated **Members** field alongside
   the existing address, and click **Save**.
4. From that point on, every escalation forwarded to Kelapa Gading —
   including one already mid-conversation — goes to both addresses, with
   no redeploy or waiting period.
5. Months later, the original contact leaves the dealership. The same
   administrator edits the group again, removes that address from
   **Members**, and saves — future escalations stop reaching the old
   address immediately.

## Scenario 9: An SLA breach reaches the PIC group and the case
<!-- TRAINING: audience=supervisor -->

1. An email case sits open overnight with no first agent reply, past the
   Email inbox's configured Response window (see the Administration
   chapter's SLA Policies section).
2. Once the next SLA scan runs, the CRM posts a private note on the
   conversation starting `⚠️ SLA breach`, naming which target was missed
   (see the Conversations chapter's SLA breach alerts section).
3. At the same moment, the department's PIC group (resolved from the
   conversation's own department label) receives an email with the
   breach details and a link back to the case.
4. The next morning, the on-duty agent opens Conversations, spots the
   breach note on a case in their queue, and replies immediately —
   already aware the PIC group has been notified too, so a follow-up from
   that side may already be in motion.
5. A supervisor checks later in the day and confirms no second alert fired
   for the same breach — the CRM only alerts once per breach per case.

[[SCREENSHOT: ch11-scenario9-sla-breach | An SLA breach private note on a conversation]]

## Scenario 10: Adjusting SLA thresholds ahead of a launch event
<!-- TRAINING: audience=supervisor, exercise -->

1. Ahead of an e.MAS 7 launch event, Proton expects a spike in email
   inquiries and wants a tighter response target on the Email inbox for
   the week, plus more advance warning before anything breaches.
2. An administrator with the SLA-management permission opens **SLA
   Policies**, sets **Scope** to the Email inbox, and lowers the
   **Response window (hours)** from 1 to 0.5 (see the Administration
   chapter's SLA Policies section).
3. They also set **Warn before breach (minutes)** to 30, so the team gets
   an early nudge with half an hour of runway rather than finding out only
   once a case has already breached.
4. They leave **Tier-2 re-alert after (hours)** blank, since the deployed
   default re-alert timing is fine for this event.
5. They click **Save** — the tighter thresholds apply to Email-inbox cases
   from that moment on. The following week, once the event traffic has
   settled, the administrator returns to the same page and clears the
   Response window and warning fields back to blank, restoring the tenant
   default.

## Scenario 11: Editing a customer-facing email template
<!-- TRAINING: audience=admin -->

1. Proton's brand team wants the escalation acknowledgement email to
   sound warmer than the built-in default wording.
2. An administrator opens **Knowledge → Settings**, scrolls to **Tenant
   settings**, and finds the **Escalation acknowledgement template** field
   (see the Knowledge chapter's Settings section).
3. They rewrite the wording, leave every other field on the panel
   untouched, and click **Save settings**.
4. The next time an agent escalates an Email case, the customer receives
   the new wording instead of the platform default — nothing else about
   the escalation flow changes.
5. The administrator also looks at the **Inbound auto-acknowledgement
   template** field just above it and edits that wording too, for
   consistency — but is careful to check with their CRM contact first,
   since on this tenant the inbound auto-acknowledgement is currently
   switched off. Editing the template alone doesn't turn the feature on;
   nothing will actually send until an administrator enables it.

## Scenario 12: A WhatsApp case looks escalated, but nothing was sent

1. A customer messages about a repeated delivery delay, and the agent
   handling it decides the case needs the dealer's attention. Following
   the same steps as an Email escalation (see Scenario 2, above), the
   agent adds a `dealer_<slug>` label, then **escalate**, to the WhatsApp
   conversation.
2. Nothing happens. **Adding the `escalate` label to a WhatsApp
   conversation by hand does not send an email or a WhatsApp alert to
   anyone** — the automatic escalation flow only fires on an Email-channel
   conversation (see the Integration Overview chapter's Email section);
   WhatsApp conversations don't qualify, no matter what labels are on
   them.
3. The `dealer_<slug>` label does still do one thing: it stamps the
   conversation for that dealer's turnaround reporting, the same as it
   would on any channel. That part works — it's a reporting timestamp, not
   a notification, and it's easy to mistake one for the other.
4. The only way a PIC gets notified automatically on WhatsApp is if the AI
   assistant itself judges the customer's message to be a genuine
   complaint — its own decision, not something an agent can trigger by
   adding a label (see the AI Assistant Behaviour chapter).
5. **What the agent actually does:** call or email the dealer directly,
   outside the CRM, and tell the customer only that the right team is
   being looped in — not that "I've escalated this," since the label
   alone didn't do anything on this channel.

## Scenario 13: The same limitation on a web chat case

1. A website visitor asks about a warranty issue that turns out to need
   the dealer's attention. The agent applies a `dealer_<slug>` label, then
   **escalate**, to the Web Chatbot conversation — exactly the same steps
   as Scenario 12, above.
2. The result is identical: no email fires, because the conversation is on
   the Web Chatbot inbox, not the Email inbox. The dealer turnaround stamp
   still applies from the `dealer_<slug>` label; the notification still
   doesn't happen.
3. Since the visitor may not have given a phone number or email through
   the widget, the agent may have no address to escalate to inside the CRM
   even if the flow did fire here — reinforcing why this has to be handled
   outside the CRM today, the same as WhatsApp.
4. The agent contacts the dealer directly and tells the visitor only that
   the right team has been looped in, the same wording as Scenario 12.

## Scenario 14: An after-hours breakdown call, and an unanswered transfer

1. A customer calls late one evening reporting a breakdown and asks the
   assistant to connect them to a person right away.
2. Because every transfer attempt — roadside or otherwise — is gated by
   the support inbox's normal business hours with no exception, the
   transfer isn't attempted at all; the call continues with the assistant,
   which tells the caller a specialist will follow up (see the AI
   Assistant Behaviour chapter's Phone handoff behaviour section). This is
   a real correction from the 08-04 guide, which described roadside calls
   as bypassing business hours — that bypass was never built.
3. The next morning, an agent opens the resulting conversation, reads the
   transcript, and logs the incident in the RSA Incident Log chapter with
   the vehicle number and cause, then calls the customer back directly —
   not assuming any overnight transfer or callback already happened.
4. Later that week, a different caller reaches an agent transfer during
   business hours, but nobody picks up at the other end. The caller hears
   a short apology and the call ends without returning to the assistant;
   the conversation is tagged so agents can find it. The agent on duty
   sees the tag, calls the customer back personally, since the apology's
   promised callback doesn't send itself.
5. Neither of these cases can be escalated to a dealer with just the
   `escalate` label either — a phone conversation isn't on the Email
   inbox, the same limitation as Scenarios 12 and 13.

## Scenario 15: Categorising a case through all five taxonomy dropdowns
<!-- TRAINING: audience=agent, exercise -->

1. A customer emails complaining that their new e.MAS 5's delivery has no
   estimated date. The agent opens the conversation's custom attributes
   panel — these are the same five dropdowns every agent fills in on a
   case, distinct from the admin-only Case Taxonomy page that maintains
   three of these option lists behind the scenes (see the Cases chapter's
   Case categorisation section).
2. They set **Case Type** to `Complaint` — one of three values, and
   independent of everything else in the panel.
3. They set **Case Category** to `Sales`, one of eight divisions.
4. **Case Subcategory** now only offers Sales' own Level-1 values, each
   prefixed `Sales: ` — the agent picks `Sales: Delivery`.
5. **Case Detail** now only offers Delivery's own Level-2 values, each
   prefixed `Sales: Delivery: ` — the agent picks `Sales: Delivery: No
   Estimated Time Delivery`. Nobody pre-filled this: Case Detail is never
   set by the AI, only ever picked by hand.
6. They set **Vehicle Model** to `e.MAS 5` — again independent, doesn't
   touch the other four fields.
7. Later, the agent realizes this is actually a Charging complaint, not
   Sales, and changes **Case Category** to `Charging`. **Case Subcategory**
   and **Case Detail** both clear immediately, since `Sales: Delivery` and
   `Sales: Delivery: No Estimated Time Delivery` no longer match the new
   division — the agent reselects both from Charging's narrowed lists.
   **Case Type** and **Vehicle Model** are untouched by this change.

[[SCREENSHOT: ch11-scenario15-case-taxonomy | All five case-categorisation dropdowns filled in, with Case Subcategory and Case Detail narrowed by the chosen division]]

## Scenario 16: An AI-suggested escalation department — accepted, and ignored

1. A customer emails Proton asking about financing options and a
   trade-in value before committing to an order. No department label is
   on the conversation yet.
2. Within moments, a private note appears naming **pre_sales** as the
   AI-suggested escalation department, explicitly stating it's a
   suggestion only and that no label has been applied (see the
   Conversations chapter's AI-suggested escalation department section).
3. The agent agrees with the suggestion, adds `dept_pre_sales` from the
   label control, then applies `escalate` last — the case routes to the
   Pre-Sales PIC exactly as if the agent had picked the department
   unprompted.
4. On a second, unrelated case — a customer asking a routine
   delivery-status question — the same kind of note suggests `sales`.
   The agent judges this one is actually an After-Sales matter instead,
   so they simply don't add `dept_sales`; they apply `dept_aftersales`
   themselves and escalate with that. The suggestion is never applied
   automatically either way, so ignoring it costs nothing and requires no
   extra click.

[[SCREENSHOT: ch11-scenario16-dept-suggestion | An AI-suggested escalation department private note (pre_sales) on an Email conversation]]

## Scenario 17: Checking escalation routing before you rely on it
<!-- TRAINING: audience=admin -->

1. Ahead of relying on escalation routing for a live launch event, a
   supervisor with the escalation-routing permission opens
   **Escalation Routing** (see the Administration chapter's Escalation
   Routing section) and reads down the six department rows —
   `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`,
   `dept_cs`, and `dept_technical` — plus the dealer group rows, checking
   which ones actually show a contact.
2. **Why this check matters:** applying `escalate` stamps the
   conversation and looks, from the conversation view, exactly like a
   successful escalation — whether or not that department or dealer group
   has a contact configured. A department with no PIC, or a dealer group
   with no members, sends the escalation email to nobody, silently, with
   no error anywhere in the CRM to say so.
3. For every row that does show a contact, the supervisor escalates one
   test case with that department's label, then `escalate`, and confirms
   the contact actually receives the forward. For a dealer group, they add
   the dealer's label alongside a department label and confirm every
   member listed receives it too.
4. **What this check does not establish:** how many of the rows are
   populated overall, since the directory is editable at any time and what
   was complete last month may not be today. Check the Escalation Routing
   page yourself before a launch event rather than relying on a past
   check, and don't tell the team they can skip double-checking after an
   escalation on the strength of a check that's already gone stale.
5. **As it stood on 2026-08-12:** all six department rows and both dealer
   group rows did show a contact — a supervisor doing this check that day
   would have found nothing missing. That observation is already out of
   date by the time you read it, which is exactly why step 1 above is
   worth repeating rather than trusted from a past run.

[[SCREENSHOT: ch11-scenario17-escalation-routing-verified | The Escalation Routing page, checked row by row before a launch event]]
