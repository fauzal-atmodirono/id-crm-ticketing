# Feature Guide v3 — sections held back (internal)

**Not a client deliverable.** These three Administration sections were written
for Feature Guide v3 and then removed from it on 2026-08-09, because the
software they describe is not running on the `proton` tenant.

**What "not running" means, measured rather than assumed** (2026-08-09):

- The tenant's Chatwoot image is built from commit `33df8ef`, which carries
  fork patches `0001`-`0051`. Patches `0052`-`0056` — escalation manager
  contact, the workforce dashboard, the agent status selector, the Translate
  composer action and the FAQ composer strip — have never been through a
  Cloud Build. `grep` for "My status" and "Workforce" in the deployed JS
  bundle returns nothing.
- The tenant's `agent` and `backend` containers were built at
  2026-08-09T01:41Z from commit `e6dc537`. Everything in packages P4 through
  P8 landed after that. The deployed backend's own OpenAPI document has no
  `/assist/translate`, no `/routing/status`, no `/routing/presence` and no
  `/metrics/targets`.
- P8's eleven BigQuery views were never created, so even once its code ships
  the cost and AI-performance reports have nothing to read.

**To put a section back:** deploy the package it belongs to, verify it on the
tenant, then move the section into `feature-guide-src-v3/09-administration.md`
ahead of `## Account settings`, and restore its `OUTLINE.md` rows. The
`<!-- VERIFY-LIVE -->` comments are deliberately left in place — each one names
what still has to be confirmed.

| Section | Needs | Package |
|---|---|---|
| Agent Availability & Workforce Dashboard | Cloud Build of patches `0053`+`0054`, agent/backend rebuilt past `e6dc537` | P6 |
| AI Conversational Quality | Cloud Build of patches `0055`+`0056`, backend rebuilt, and a real photo through a real WhatsApp number for the diagnosis prompt | P7 |
| AI Cost & Performance Measurement | Backend rebuilt, plus the eleven BigQuery views created | P8 |

---

## Agent Availability & Workforce Dashboard

### What it is

Two related things. First, an extended set of **availability statuses** an
agent can choose beyond the three the CRM offers natively (Online, Busy,
Offline): Available, Busy, Lunch, Break, Coaching, Training, Toilet and
Prayer, plus **After-Call Work**, which the system sets automatically when a
phone call ends rather than being chosen by anyone. Second, a supervisor
**Workforce dashboard** showing, live, who is in which status, how long they
have been in it, how their day has been split across statuses, their
availability against the working day, and how many cases they currently have
open.

Three things about how this is presented are deliberate, and knowing them
prevents a supervisor drawing the wrong conclusion from the page:

> Named statuses are chosen on their own **My status** page, not from the
> availability control in the CRM's top bar. That control sets the CRM's own
> three-value status and is left exactly as it is; the named statuses sit
> alongside it and mirror into it.
>
> Named statuses mirror into Chatwoot's native Online/Busy/Offline. Selecting
> "Lunch" shows as **Busy** inside Chatwoot's own UI and as **Lunch** on the
> workforce dashboard. This is deliberate: Chatwoot's presence field is a fixed
> enum, and mirroring means an agent is still correctly excluded from routing
> even if the custom-status service is unavailable.
>
> The "Availability history" column is derived from transitions to and from
> Offline. It is **not** a login/logout record — an agent who closes their
> laptop without going offline stays shown as available until their next
> transition.

Which statuses raise an absence alert is also a deliberate choice rather than
a technical accident. **Lunch, Break, Toilet and Prayer** do: they mean an
agent has stepped away and cannot be relied on to be back within a normal
working rhythm, which is exactly what the alerts are for. **Coaching,
Training and After-Call Work** do not: those are expected, scheduled or
automatic states, and alerting on them would be noise. **Busy** does not,
because Busy means an agent is working — a long call must not raise an
"agent is missing" alert. And **Offline** does not, because going offline is
an agent saying they are off shift, not that they have disappeared mid-shift;
an alert there would fire for every agent every evening.

Where the dashboard cannot measure something, it shows **blank rather than
zero**, the same rule the monthly control-item report follows. In particular
the **"Cases closed today"** column is always blank: nothing in the platform
records a date-filtered "resolved today" count that can be read cheaply enough
to refresh every half minute, and a zero there would be a statement about the
team's output rather than about what is instrumented. Read a blank as "not
measured", never as "none".

Two capacity limits worth stating plainly, so nobody plans around them:

- **The one-hour unavailability alert lists the agent's open cases from the
  inboxes the SLA engine watches**, not from every inbox in the account. If a
  team routes work through an inbox outside that scope, those cases are not in
  the list. Treat it as a prompt to review, not as a complete audit.
- **Average handling time is not part of this.** The After-Call Work *state*
  exists; the handling-time figure that usually accompanies it depends on call
  queue statistics the platform does not yet receive from the telephony side —
  the same gap that leaves several call-related rows of the monthly report
  unmeasured.

### Where to find it

Agents open **My status** in the main left-hand navigation. Supervisors open
**Workforce** in the same navigation. Each entry appears only if your role has
been granted the matching permission — "Set your own availability status" for
My status (which the default Agent role carries) and "View the
workforce/presence dashboard" for Workforce — so if an agent cannot see **My
status**, check that they have been assigned a role on the **Roles &
Permissions** page.

> **Pending verification.** Both pages are built, but the CRM interface
> containing them has not yet been rebuilt and deployed at the time of
> writing, so the nav labels and the exact layout described here are what to
> expect rather than what has been confirmed on a live tenant. The underlying
> service behind My status is complete and tested. This is tracked in the
> project's blocked-work register alongside the same note for the Workforce
> page; ask for confirmation before scheduling a demonstration of either page.

<!-- VERIFY-LIVE: confirm both nav labels ("My status", "Workforce") and the page layouts on the live tenant once fork patches 0053 + 0054 have gone through a Cloud Build -->

### How to use it

1. **As an agent**, open **My status** and pick the status that matches what
   you are doing — for example **Lunch** when you step out. You do not need to
   remember to also set yourself Busy: choosing Lunch does that for you, which
   is why colleagues see you as Busy in the CRM while the supervisor's
   dashboard shows the reason. The CRM's own top-bar availability control keeps
   working as it always did and will show you as Busy; it does not list the
   named statuses.
2. Set yourself back to **Available** when you return. Anything other than
   Available means new conversations are not routed to you. Your current
   status and how long you have been in it are shown at the top of the page,
   so a page refresh never leaves you guessing.
3. **After a phone call**, you are placed into **After-Call Work**
   automatically so you can finish your notes. Set yourself back to
   **Available** when you are done — and if you forget, the system releases
   you automatically after a short interval, so a forgotten wrap-up can never
   quietly remove you from routing for the rest of the shift.
4. **As a supervisor**, open **Workforce** to see the live grid. Refresh the
   page (or leave it open — it re-reads roughly every half minute) to follow
   the day; this is a polled view of current data, not a pushed live feed.
5. Read the **"Time in status today"** and **"Availability %"** columns
   together: the percentage is measured against the inbox's configured working
   hours, not against a flat 24 hours, so an agent who worked their whole shift
   reads near 100% rather than around a third.
6. If an agent stays in a status that counts as unavailable — Lunch, Break,
   Toilet or Prayer — past the configured thresholds, the agent and an
   administrator are notified after the first threshold, and an administrator
   again (with that agent's open cases attached) after the second. Coaching,
   Training and After-Call Work are expected, scheduled states and never alert;
   neither does the CRM's own Busy or Offline (see the reasoning above). A
   three-hour absence therefore produces exactly two notifications, not one
   every minute, and an agent who returns and steps away again starts the
   count fresh.
7. To add a status of your own — a shift pattern or an activity specific to
   your team — an administrator can add one from the **Status catalogue**
   section at the bottom of the **My status** page, with no software release.
   Give it a key, a label, a colour, which of the CRM's three native statuses
   it should mirror into, whether an agent in it can still receive new
   conversations, and whether it should raise the absence alerts. Existing
   statuses can be edited the same way — including turning an alert off for a
   team that does not want it. Statuses are never deleted, because past
   history refers to them; retire one by marking it as not receiving new
   conversations.

[[SCREENSHOT: ch09-workforce | The Workforce dashboard, with the availability-history disclaimer shown on the page]]

### Example scenario

An agent sets herself to **Lunch** from **My status** at 10:45. Proton's
after-sales supervisor opens **Workforce** at 11:40 and sees she has been in
**Lunch** for 55 minutes while three cases sit open against her name. The dashboard is where she sees the *reason*; in the conversation
list that agent simply reads as Busy. Rather than guessing, the supervisor
waits for the one-hour alert, which arrives with the agent's three open case
numbers attached, and reassigns the most urgent of them to a colleague from
the conversation view. Separately, a second agent finished a phone call two
minutes ago and shows as **After-Call Work** — the supervisor leaves him
alone, because that status is expected and clears itself.

### Integrations & automation

Availability is what conversation routing already reads to decide who can
receive new work (see the Conversations chapter), so a custom status takes
effect on routing through the native status it mirrors — which is why routing
keeps behaving correctly even if the custom-status service is unavailable. The
same status history feeds the availability figures in the Reports chapter's
agent reporting.

Three separate permissions, all granted from **Roles & Permissions**, govern
this area, and the split matters: *setting your own status* belongs to the
Agent role by default, because it is part of an agent's own working day;
*viewing the workforce dashboard* and *editing the status catalogue or setting
another agent's status* are administrator permissions. An agent cannot mark a
colleague as being on Lunch — that would take the colleague out of routing and
start an absence clock against their name. Who may reassign a conversation to
a named agent is a fourth, separate permission.

## AI Conversational Quality

### What it is

Six adjustments to how the AI reads and answers customers, each switched on or
off per tenant by your administrator rather than by anyone inside the CRM:

1. **Sentiment on every conversation.** The AI records how the customer sounds
   — positive, neutral, negative or urgent — on the conversation itself, using
   the same request it already makes to answer the message. There is no extra
   AI call and no extra delay.
2. **Tone matched to that sentiment.** With sentiment recorded, the reply's
   tone is chosen from it: a measured, apologetic register for an angry
   customer, a brisk one for an urgent safety issue. It applies from the
   customer's *first* angry message, not from the following one. A sentiment
   older than fifteen minutes is ignored, so an hour-old complaint does not
   make a cheerful "thanks, all sorted" come back apologetic.
3. **Translate, for the agent.** A **Translate** action in the reply composer
   renders the customer's latest message in English as a **private note** on
   the conversation. The translation is never sent to the customer — the only
   thing the platform can do with it is add a note.
4. **FAQ matching that also counts keywords.** Product codes and model names
   (e.MAS7, for instance) match poorly on meaning alone. A keyword-overlap
   score can be blended into FAQ ranking to fix that. It ships at zero weight,
   which reproduces today's suggestions exactly.
5. **Photo and video diagnosis.** When a customer sends a photo, the AI is
   asked to describe what is actually visible, name the likely fault, state how
   confident it is and ask at most one follow-up question — instead of a
   generic "please describe the issue". Voice notes are deliberately excluded:
   there is nothing to look at.
6. **Resolved-case summaries.** When a case is resolved, the AI can post a
   summary of it as a private note, and can add that summary to a searchable
   store of previously resolved cases.

**Everything in this list is off until an administrator switches it on.** With
none of it enabled the platform behaves exactly as it did before: no sentiment
is recorded, FAQ suggestions are unchanged, a photo gets the old generic
instruction, and nothing is summarised on resolve.

Two of the six carry limits that must be read before they are switched on.

> **Tamil.** Inbound Tamil translation — so an agent can read a Tamil message —
> is enabled with `TRANSLATION_ENABLED`. **Outbound Tamil replies to customers
> remain disabled** pending an evaluation of 30 real Tamil enquiries scored by a
> Tamil speaker. Enabling `TRANSLATION_OUTBOUND_TAMIL_ENABLED` before that
> evaluation sends unverified machine translation to customers.
>
> **Resolved-case suggestions** are generated from summaries of previously
> resolved cases, and are labelled as such wherever they are shown. They are not
> approved guidance — a resolved-case summary is what a colleague did last
> month. The store holds summaries rather than transcripts, and the summariser
> is instructed to omit customer identifiers, but **this is a mitigation and not
> PII masking** — that is gap R16. Two things about the mitigation are worth
> knowing precisely: nothing checks or edits the summary before it is stored, and
> an operator's own persona **guardrails** are placed ahead of that instruction
> in the same request, so a guardrail saying the opposite ("always include the
> customer's full name") is text the AI may prefer. Anyone who can edit the
> persona can weaken the mitigation without touching software.

### Where to find it

All six are **backend settings in the tenant's configuration**, not pages in
the CRM: there is no admin screen that toggles them, which is deliberate —
each one changes what customers receive, so switching it on is a deployment
decision with a record, not a checkbox. Ask your administrator to change them.

What *is* visible in the CRM: the **Translate** button in the reply composer's
Proton panel. Sentiment appears on the conversation's custom attributes.
Resolved-case summaries appear as private notes in the conversation.

A seventh setting, `FAQ_SUGGESTION_POPUP_ENABLED`, is **not yet connected to
anything and setting it has no effect.** The dismissible FAQ suggestion strip it
was intended to gate is built, but the only switch that actually shows it is the
CRM front-end's own `PROTON_FEATURES` list (it must contain
`faq_suggestion_popup`). Ask us before relying on either: the two switches are
independent today, so setting the back-end one alone changes nothing, and setting
the front-end one alone turns the strip on regardless of the back-end setting.

When the strip is shown it offers a single best-matching FAQ answer for
the customer's last message, with an Apply button that writes it into
the reply. It only appears when the suggestion's confidence clears a
threshold, and dismissing it for a message keeps it dismissed for that
message. Two things about it are not yet true in practice, and an
administrator should know both before promising it to agents:

- **It ships as an unbuilt fork patch.** Like the Translate button below, it
  has not been through a Cloud Build or been seen on a live tenant.
- **This setting alone does not turn it on.** The strip's visibility on the
  screen is actually controlled by the tenant's separate `PROTON_FEATURES`
  list, which an administrator must also update. Until both are set, enabling
  this setting by itself changes nothing an agent can see.

FAQ suggestions in the agent-assist side panel are a separate, existing
feature and are unaffected either way.

> **Pending verification.** The Translate button and the FAQ suggestion strip
> both ship as fork patches that have not yet been through a build at the time
> of writing, so their exact position in the composer panel is what to expect
> rather than what has been confirmed on a live tenant. The endpoints behind
> them are complete, mounted and tested. The photo-diagnosis wording has also
> **not yet been tried against a real photo through a real WhatsApp number**
> — that check is owed and is tracked in the project's blocked-work register.
> Do not present any of the three as demonstrated.

<!-- VERIFY-LIVE: confirm the Translate button's and FAQ suggestion strip's placement in the composer panel, and the resolved-case auto-summary note's appearance, on the live tenant once fork patches 0055 and 0056 have gone through a Cloud Build -->

### How to use it

1. **Switch sentiment on before tone.** Tone selection does nothing on its own:
   with no sentiment recorded there is nothing to choose a tone from, and the
   bot keeps its standard wording.

   **Enabling sentiment alone does change one thing, though, so it is not a
   pure no-op:** a case whose customer sounded negative or urgent is mirrored
   into the CRM as **open** rather than resolved, and the reading is not
   time-bounded — so one angry turn keeps later turns in the same conversation
   mirroring as open, including a closing "thanks, all sorted". Replies
   themselves are unchanged. Expect the effect on case status and tell us if it
   is unwanted, rather than discovering it in a report.
2. **Leave the FAQ keyword weight at zero until you have a measurement.** Zero
   reproduces today's ordering and today's scores exactly. A large weight would
   damage the common case (ordinary questions, which already match well) in
   order to fix the rare one (exact product codes).
3. **Use Translate to read, not to reply.** It posts a private note. To answer a
   Malay or Chinese customer, write the reply as you normally would — the AI
   already answers in the language the customer wrote in.
4. **Do not enable outbound Tamil.** See the note above. It is the one setting
   deliberately excluded even from the platform's own all-features-on test run.
5. **The two resolve settings are independent.** The private-note summary needs
   no database and can be used on its own. The searchable store of resolved
   cases needs the platform's knowledge database to be configured; if it is not,
   the summary note still posts, the store is skipped, and — importantly —
   **resolving the case always succeeds either way.** Resolving is the agent's
   action; the summary is an addition to it and can never fail it.
6. **A resolved case that is reopened and resolved again gets a second
   summary**, appended rather than overwriting the first, because the first
   summary is a true record of the first resolution.
7. **The resolved-case store is separated from your authored FAQs, so clearing
   it cannot remove curated content** — they live in separate tables with
   nothing shared between them. That containment is what makes enabling the
   store reversible in principle. **In practice there is no button or command
   for it yet:** the purge is implemented in code but nothing calls it, so
   emptying the store today means asking us to run a statement against the
   database. Ask before you need it in a hurry.

[[SCREENSHOT: ch09-ai-quality | The reply composer's Proton panel showing the Translate action, with the resulting translation as a private note in the conversation]]

### Example scenario

A customer messages in Tamil about a warning light. The agent, who does not
read Tamil, clicks **Translate**: a private note appears in the conversation
with the English text, and the customer sees nothing. The customer then sends a
photo of the dashboard; because photo diagnosis is enabled, the AI's suggested
reply names the specific warning symbol, says how confident it is, and asks one
question rather than five. Sentiment is recorded as *urgent*, so the drafted
wording is brisk rather than chatty. When the case is closed the next morning, a
private note summarising it appears at the end of the thread — the agent who
picks up the follow-up call reads three bullet points instead of forty messages.

### Integrations & automation

Sentiment is written to the conversation's own custom attributes, so it is
available to automation rules and to the Reports chapter exactly like any other
case field — no new integration surface. The resolved-case summary is produced by
the same summariser the **Summarize** button in the composer already uses, so
there is one summariser and one set of wording to review, not two that can drift
apart. The summary is posted through the same private-note mechanism as
escalation notes.

**Translate is governed by its own permission** ("use translation"), granted to
the default Agent role rather than kept administrator-only: reading a customer's
own message is part of an ordinary agent's job on every conversation they handle.

Two limits on what has actually been delivered, recorded here rather than only
in the engineering notes because both read as delivered if nobody says so:

- **Nothing yet shows resolved-case suggestions to an agent.** Summaries are
  stored and labelled, and the labelling exists so that whichever panel adds
  them cannot present them with a curated FAQ's authority — but no panel queries
  the store today. Enabling it builds the corpus; it does not yet surface it.
- **No before-and-after accuracy figures exist.** The evaluation sets and the
  runner for them are built and are part of the delivery, but they have never
  been run against the real AI service, so the accuracy tables in
  `docs/testing/2026-08-08-ai-calibration-baseline.md` read "unmeasured". Any
  percentage produced by the test harness in its current form is a property of
  the harness, not of the model, and must not be quoted as a result.

## AI Cost & Performance Measurement

### What it is

Six settings that turn on measurement of what the AI costs and how well it
performs, plus a per-agent view of customer satisfaction and an optional NPS
question. Everything here is a **number in the reporting warehouse**, not a page
in the CRM, and everything is off until an administrator switches it on.

1. **AI token metering.** Records how many tokens each AI request used, per day,
   per product surface and per model.
2. **An AI cost report.** Prices those tokens from an editable rate table, with
   the rate that was in force on the *day of the usage* — so last month's figure
   does not change when a rate changes.
3. **NPS instead of CSAT, for a sampled share of conversations.** The
   end-of-conversation survey asks "how likely are you to recommend us" instead
   of asking for a rating — never both, because asking both halves the response
   rate to each. The choice is made once per conversation, so a customer is never
   asked to rate the same conversation twice.
4. **CSAT per agent**, alongside the existing channel-level CSAT report, which is
   unchanged.
5. **A ranking floor.** An agent with too few ratings is left out of league-table
   *rankings* but still appears, with their rating count, in the listing.
6. **A five-point call QA rubric** (greeting, identification, resolution,
   closing, compliance) for a reviewer to score a phone call against, expressed
   as a percentage against your QA target.

Alongside these, four AI performance reports are added to the warehouse: AI case
resolution, AI vs human handling, AI escalation reasons, and AI deflection rate,
plus an AI-vs-human split on satisfaction and two KB-health views.

**This section is deliberately as much about what is not measured as what is.**
The figures below are meant to be defensible in a monthly review, which means
the gaps have to be stated rather than discovered.

> **The cost report is a lower bound, and it has no total.** It reports a
> *subtotal* of what could be priced, plus a list of what could not, and that is
> on purpose — there is no single "AI cost" figure anywhere in the payload,
> because one would silently omit the largest line item. Three specific gaps:
>
> - **The busiest AI surface — the WhatsApp/web conversational bot — is not
>   metered at all.** The library that runs it builds its own connection to the
>   AI service inside its own package, where nothing we own can observe the
>   usage. This is an architectural limit of that library, not an oversight, and
>   it is the reason a total would mislead.
> - **Live phone-call AI usage is not metered** either: that service reports its
>   usage in a different channel from the one a response arrives on.
> - **Search-index (embedding) usage is visible but cannot be priced.** It is
>   billed per character, and the record has no character count, so a rate on
>   file still does not produce a cost.
>
> Every unmetered surface appears in the report as a row with a **blank** cost
> and a **blank** call count and a sentence saying why — never as a zero, because
> a zero reads as "this is free". Even the surfaces that *are* priced are
> understated for the newer "thinking" models, whose reasoning tokens are billed
> but fall outside the three counts recorded.

> **The AI performance reports cannot tell you who resolved a case — they infer
> it.** The one field that looks like it says so is derived from the
> case's status alone, so its "bot" value means only "this case is resolved". All
> four reports therefore infer human involvement from whether an agent was ever
> *assigned*, whether the case was escalated, and whether it went to a dealer,
> and each report states that basis in its own output. What that cannot see is a
> human who replied without ever being assigned.
>
> Two consequences worth knowing before quoting a number. **Deflection means
> resolved with no agent involvement at all** — a conversation the bot answered
> before a human took over is *not* deflected; the definition travels with the
> report because two reasonable readings of it differ by roughly a factor of two.
> And the older **"bot vs agent resolution split"** on the native Bot report is
> not an AI-vs-human measure at all (see the Reports chapter) — the two numbers
> under it are resolved and not-yet-resolved.

> **Not built, and not planned in this package.** AI *root cause analysis* and
> *KB improvement recommendations* are not delivered: both need a model to
> summarise failure patterns across many cases, which is its own piece of work.
> AI *accuracy* is answered by the calibration baseline described under AI
> Conversational Quality — as a measured figure, when it has been measured, not
> as a report page. There is also **no cut of any report by customer
> sentiment**: sentiment is recorded on the conversation but does not reach the
> reporting warehouse yet.

### Where to find it

All six are **backend settings in the tenant's configuration**, like the AI
Conversational Quality settings above — there is no admin screen for them. The
resulting numbers appear in the reporting warehouse, for your BI tool (Looker,
Power BI) to read; the call QA rubric is submitted through the same QA
mechanism the existing accuracy/quality labels use.

Three things have to happen on a tenant before any of these numbers exist, and
until they do the reports are **absent, not zero**:

- The new warehouse views have to be created. None of them has been created on
  any tenant yet.
- The token-usage table only comes into existence the first time a tenant runs
  with metering on, and the cost views cannot be created before it exists.
- Two one-off database changes are owed on any tenant that is already live: one
  for the AI service's own token columns, one for the QA table's new rubric
  columns. Neither is added automatically to an existing table.

While those are outstanding the cost report answers **"unavailable"** rather than
`0.00` — with no warehouse there is no evidence of zero spend, and that
distinction is the whole point of the report.

> **Pending verification.** Nothing in this section has been run against a real
> warehouse, a real AI service, a real phone call or a live database — there are
> no such credentials in the environment it was built in. Every figure described
> here is produced by code that is unit-tested against recorded and synthetic
> data. Treat the section as what the platform will report once the steps above
> are done on a tenant, not as a description of numbers anyone has seen. One view
> in particular, the **KB staleness queue, returns nothing at all today**: it
> reads a table of FAQ entries that does not exist and that nothing populates, so
> it must not be put on a dashboard in the belief that an empty result means a
> healthy knowledge base.

[[SCREENSHOT: ch09-ai-measurement | Deferred — there is no CRM screen for this section. Capture the BI tool's AI cost and AI performance tables only once the warehouse views have actually been created on a live tenant; a screenshot taken before that would show empty tables, not the feature]]

### How to use it

1. **Turn metering on before the cost report.** The cost report prices rows that
   metering writes; switching it on first gives you a correctly-empty report.
   Leave metering running for a full billing period before quoting a figure.
2. **Read the cost report as a floor, never as the bill.** Compare it against
   your actual invoice from the AI provider: the gap is the unmetered surfaces
   listed above, and it is not small — the conversational bot is the busiest of
   them.
3. **Keep the NPS sample small at first, and expect CSAT volume to move.** Every
   sampled conversation is asked the NPS question *instead of* the CSAT one, so
   sampling at half means roughly half as many CSAT responses. The two are
   separate scores and should not be blended.
4. **The agent credited with an NPS score is the one assigned when the customer
   answered**, recorded at that moment. Reassigning the case afterwards does not
   move the score.
5. **Do not rank agents on a handful of ratings.** The ranking floor exists for
   this: below it an agent is listed with their count but not given a rank. Raise
   the floor rather than lower it — a per-agent score without its sample size
   next to it is how a measurement becomes an industrial-relations problem, and
   every rate in these reports ships with its denominator for that reason.
6. **The call QA rubric is scored by a person, always.** Nothing reads a call
   recording or transcript and produces a score. A partly-scored rubric reports
   "incomplete", never a low percentage — a review in progress is not a bad call.

### Example scenario

Ibu Sari is asked in a monthly review what the AI is costing. She opens the AI
cost report for last month: it shows a subtotal for the assist and
transcription surfaces, priced at the rates in force on each day, and beside it
three rows with blank costs — the conversational bot, live call audio, and the
search index — each with a sentence saying why. She reports the subtotal as a
floor, names the three gaps, and adds the cost-per-conversation figure from the
same report, which carries its own note that the numerator is partial. Nobody
leaves the room with a number that later turns out to be half the invoice.

### Integrations & automation

The numbers are warehouse views, so any BI tool that can read the warehouse
gets them with no extra integration — and the caveats travel *in the data*, not
only in this guide: each unmetered surface is a real row with a blank cost and a
reason, the deflection definition is a column on the deflection report, and each
report carries the basis on which it inferred AI-versus-human handling. A
dashboard built directly on the warehouse therefore cannot show the numbers
without also having the caveat available beside them.

Two limits on reach. The **web live-chat widget's own survey is not part of NPS
sampling** — it asks its rating question from its own interface, so switching NPS
on affects WhatsApp, email and phone conversations only. And the **AI service's
token counts do not reach the warehouse**: they are recorded in that service's
own database, so the cost report lists it as unmetered with a reason rather than
folding it in.

