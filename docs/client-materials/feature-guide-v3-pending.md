# Feature Guide — sections held back (internal)

**Not a client deliverable.** Three Administration sections were written for
Feature Guide v3 and removed from it on 2026-08-09, because the software they
described was not running on the `proton` tenant. **Two of the three came back
in v4 on 2026-08-12; one is still held back and is all that remains in this
file.**

**Status after Task 11's re-probe (2026-08-12).** The v3 rationale below is
superseded and is kept only so the earlier decision can be audited:

- The tenant's Chatwoot image is now built from commit `0866fda`
  (`proton-chatwoot:v4.15.1-custom-rc6`), which carries fork patches `0001`
  through `0065`. Patches `0053`/`0054`/`0055`/`0056` — the workforce
  dashboard, the agent status selector, the Translate composer action and the
  FAQ composer strip — **are** in the deployed image, along with `0065`, which
  unified the two availability selectors that used to disagree.
- The backend's OpenAPI document now serves `/assist/translate`,
  `/admin/workforce` and `/routing/presence/{status,statuses,statuses/{key}}`.
  **The v3 note that "`/routing/status` and `/routing/presence` are absent"
  was an exact-path check against paths that do not exist under those exact
  names** — the sanctioned prefix check finds them. That mistake is the reason
  the ledger now bans exact-path checks.
- `PRESENCE_TRACKING_ENABLED` and `PRESENCE_CUSTOM_STATUSES_ENABLED` are both
  `true` on this tenant, which is what mounts those routers.
  `PRESENCE_THRESHOLD_ALERTS_ENABLED` and `ACW_ENABLED` are absent (code
  default `False`), so the absence alerts and After-Call Work are **off** —
  the restored chapter says so rather than describing them as working.
- `TRANSLATION_ENABLED` and `FAQ_SUGGESTION_POPUP_ENABLED` are `true`; the
  four P7 AI-behaviour settings and `FAQ_KEYWORD_WEIGHT` are at their
  default-off/default-zero values, and the restored chapter says so.
- **P8's warehouse still has nothing usable behind it.** `/metrics/targets` is
  absent. `metrics_provider` defaults to `"noop"` and is unoverridden on this
  tenant, so every `/metrics/*` route is served by `MockMetricsQuery` — canned
  rows, not this account's data. The only proton-named BigQuery dataset is
  `demo_proton` in `lv-playground-genai`, which is the *code default* value of
  `bigquery_project_id`/`bigquery_dataset` rather than a provisioned tenant
  dataset, and it holds 8 views, not the 11 this section describes.
  `/metrics/ai-cost` and `/metrics/control-items` exist as routes but are read
  by no fork patch, so there is no operator-reachable page for either.

**To put the remaining section back:** switch the tenant's metrics provider to
BigQuery against a real per-tenant dataset, create the views this section
names, run the two owed one-off database changes, confirm a real (non-canned)
number reaches a report, then move the section into
`feature-guide-src-v4/09-administration.md` ahead of `## Account settings` and
restore its `OUTLINE.md` row. The `<!-- VERIFY-LIVE -->` comments are
deliberately left in place — each one names what still has to be confirmed.

| Section | Status | Needs |
|---|---|---|
| Agent Availability & Workforce Dashboard | **Restored to v4, 2026-08-12** | Nothing further to reach it. Two capabilities inside it are switched off and documented as off: absence alerts and After-Call Work. |
| AI Conversational Quality | **Restored to v4, 2026-08-12** | Nothing further to reach it. Four settings inside it are switched off and documented as off. One live check is still owed and is carried into the chapter as a `<!-- VERIFY-LIVE -->`: the photo/video diagnosis prompt against a real image through a real WhatsApp number, before that setting is enabled anywhere. |
| AI Cost & Performance Measurement | **Still held back** | A real metrics-warehouse connection (the tenant is on the `noop` provider, so every metrics route serves canned data), a per-tenant dataset with the views this section names (only the 8-view default `demo_proton` exists), the `/metrics/targets` route, the two owed one-off database changes, and an operator-reachable page for the AI-cost and control-item reports — no fork patch reads either route today. |

---

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

