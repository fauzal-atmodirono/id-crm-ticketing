<!-- GENERATED FILE — do not edit by hand.
     Source: docs/client-materials/feature-guide-src-v3/ (the operator handbook)
     Regenerate: python3 docs/client-materials/build_crm_feature_guide.py --curricula
     Drift check: python3 docs/client-materials/build_crm_feature_guide.py --check
-->

# Supervisor / team leader curriculum — hands-on exercises

> **Generated from the operator handbook — do not edit.** Every line below is rendered from `feature-guide-src-v3/`; an edit here is overwritten by the next run. To change what a cohort is taught, change the handbook section this points at, or its `<!-- TRAINING: ... -->` marker, and regenerate.

**20 exercises.** Every step is the handbook's own documented procedure for that feature, so an exercise cannot drift from the guide the cohort keeps.

> **Run these on the sandbox tenant only.** Training an agent to escalate by escalating a real customer's complaint is not a viable exercise. Reset between cohorts with `./reset-sandbox-tenant.sh` (see `../delivery-plan.md` §4).

> **NOT YET DRY-RUN.** No exercise in this set has been executed against a sandbox tenant — no sandbox tenant has been provisioned, and the environment these were generated in has no live Chatwoot, Gemini or Twilio. "Completable as written" is therefore **owed, not verified**. Dry-run the set once before the first cohort and record the result in `../delivery-plan.md`.

**15 are procedures** the trainee carries out themselves, taken from a handbook section's *How to use it* steps. **5 are role-plays** drawn from the end-to-end scenarios, where the facilitator plays the customer — those narratives are told from both sides, so they are not a checklist one trainee can work through alone.

## SV-01 — Conversation inbox & views

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Conversation inbox & views`

**Where:** The left-hand column of the Conversations view, always visible once you're signed in.

**Do this:**

1. Use the assignee tabs to switch between **Mine**, **Unassigned**, and **All** conversations.
2. Use the status filter to narrow the list to **Open**, **Pending**, **Snoozed**, or **Resolved** conversations, or leave it on **All** to see everything regardless of status.
3. Click an inbox name in the sidebar to scope the list to a single channel (for example, just WhatsApp).
4. Click any conversation in the list to open its full thread.
5. Use the sort option to reorder the list, for example by most recent activity.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## SV-02 — Labels

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Labels`

**Where:** The label control on an open conversation, and a Labels view in the sidebar listing conversations by label.

**Do this:**

1. Open the conversation you want to label.
2. Click the label control and pick one or more existing labels.
3. **Apply the department label first** — one of `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, or `dept_technical` — then the dealer label if one is involved, and apply **escalate** last. The escalation handler reads whichever labels are already on the conversation at the moment `escalate` is applied — applying it first means the department/dealer leg doesn't fire for that trigger. On an Email-channel conversation with no department label yet, you may see a private note suggesting one before you get here — see AI-suggested escalation department, below.
4. Apply the relevant dealer label so the conversation counts toward that dealer's turnaround reporting, even outside an email escalation.
5. Remove a label the same way, by deselecting it.
6. **A limitation worth knowing before you rely on this:** the automated escalation email only fires on an **Email**-channel conversation. Applying `escalate` on a WhatsApp, web chatbot, or phone conversation changes the label and nothing else — no one is notified. The dealer label still records the case for turnaround reporting on any channel; that part isn't affected. See the Integration Overview chapter's WhatsApp, Web chatbot, and Phone sections for what to actually do on those channels instead.

**Done when:** all 6 steps have been carried out on the sandbox tenant without help.

## SV-03 — Private notes

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Private notes`

**Where:** The reply box's note mode, toggled from the main "reply" mode.

**Do this:**

1. Open the conversation and click into the reply box.
2. Switch the reply box to its private "Note" mode.
3. Type your note — mention a teammate with @ if you need their input (see Mentions below).
4. Send the note; it posts as private and is visually distinct from a customer-facing reply.

**Done when:** all 4 steps have been carried out on the sandbox tenant without help.

## SV-04 — Canned responses

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Canned responses`

**Where:** A shortcut inside the reply box, typically triggered by typing a character like "/" or clicking a canned-response icon.

**Do this:**

1. Click into the reply box.
2. Trigger the canned-response search (for example, typing "/").
3. Search by the short code or keyword for the response you need.
4. Select it — it's inserted into the reply box.
5. Edit if needed, then send as normal.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## SV-05 — Ask Copilot panel

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Ask Copilot panel`

**Where:** Opened from a Copilot button above the reply box, on the right side of the conversation.

**Do this:**

1. Open the conversation you need help with.
2. Click the Copilot button above the reply box.
3. Type your question in the panel's chat box and send it.
4. Read the answer, along with the "Looked at" line noting which knowledge tools (for example, a knowledge-base search) it used to answer, and the "Sources" line underneath when it has one — click a source to open it, or read its title if it isn't linked.
5. Click "Insert into reply" on any answer you'd like to drop straight into your draft.
6. Reset the panel to start a fresh question thread, or close it when you're done.

**Done when:** all 6 steps have been carried out on the sandbox tenant without help.

## SV-06 — Suggest-a-reply

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Suggest-a-reply`

**Where:** A "Suggest a reply" action above the reply box.

**Do this:**

1. Open the conversation.
2. Click "Suggest a reply" above the reply box.
3. Wait a moment for the draft to appear in the reply box.
4. Check the Sources line underneath the draft for the knowledge-base articles it drew on.
5. Edit the draft if needed, then send it like any other reply.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## SV-07 — Resolving, snoozing & transcripts

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Resolving, snoozing & transcripts`

**Where:** Action buttons in the conversation header, and a menu option for exporting the transcript.

**Do this:**

1. Once the customer's issue is fully handled, click **Resolve**.
2. If you need to come back to a conversation later — for example, waiting on the customer or a dealer — click **Snooze** and choose when it should reopen.
3. A snoozed conversation reopens automatically at the chosen time, or immediately if the customer replies first.
4. To get a transcript of the conversation, open the conversation's menu and choose the transcript/export option.

**Done when:** all 4 steps have been carried out on the sandbox tenant without help.

## SV-08 — Contacts list & search

**Module:** 03 Contacts · **Source:** `03-contacts.md` → `## Contacts list & search`

**Where:** **Contacts** in the main sidebar.

**Do this:**

1. Open **Contacts** from the sidebar to see the full customer list.
2. Use the search box to find a customer by name, phone number, or email address.
3. Use the available filters to narrow the list (for example, by the channel a customer last used).
4. Click a customer's row to open their contact profile.

**Done when:** all 4 steps have been carried out on the sandbox tenant without help.

## SV-09 — Case list

**Module:** 05 Cases · **Source:** `05-cases.md` → `## Case list`

**Where:** **Cases** in the main sidebar. This page requires the same permission as Customer 360 (see the Contacts chapter) — an administrator who hasn't been granted that permission won't see it.

**Do this:**

1. Open **Cases** from the sidebar.
2. Use the Division, Case type, Status, Channel, and Dealer filters at the top to narrow the list, or use **Reset filters** to clear them.
3. Read the table: Case ID, Division, Concern, Purchased From, Escalated To, Agent, Car Plate, Aging (days), and Status. **Agent** shows the name of whoever the underlying conversation is assigned to, or a dash if it's unassigned.
4. Click a Case ID to open the underlying conversation.
5. If a banner appears saying the list is showing only the first N of a larger total, the list has reached its display limit — the filters and totals shown no longer reflect every case in the account.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## SV-10 — Case categorisation (five fields)

**Module:** 05 Cases · **Source:** `05-cases.md` → `## Case categorisation (five fields)`

**Where:** The conversation's custom attributes panel, alongside the conversation (the same panel used for any other custom attribute). Case Type and Vehicle Model are new rows in that same panel — before this edition, neither had a custom-attribute definition at all, so those two dropdowns didn't render there yet.

**Do this:**

1. Open the conversation you want to categorize, and open its custom attributes panel.
2. Choose a value for **Case Type** — `Inquiry`, `Complaint`, or `Compliment & Feedback`. This doesn't affect any other field.
3. Choose a value for **Case Category** (the division — for example `Sales`, `Aftersales`, `Charging`, `Apps`, `Product`, `Network`, `Marketing`, or `Others`).
4. Choose a value for **Case Subcategory** — only Level 1 values belonging to the division you just picked are offered, each shown with its `<Division>: ` prefix.
5. Choose a value for **Case Detail** — only Level 2 (and folded Level 3/4) values belonging to the subcategory you just picked are offered, each shown with its full `<Division>: <Level 1>: ` prefix. If the subcategory has no Level 2 in the source taxonomy, this list is empty and there's nothing further to pick — that's expected, not an error.
6. Choose a value for **Vehicle Model** if the case concerns a specific vehicle (`e.MAS 5`, `e.MAS 7`, `e.MAS 7 PHEV`) or `Not Applicable` otherwise. This also doesn't affect any other field.
7. Changing **Case Category** after Case Subcategory/Case Detail are already set clears both, since they no longer match the new division — reselect them from the narrowed lists. The same happens one level down if you only change Case Subcategory.
8. Save, or move on — most CRM attribute panels save automatically as soon as a value is picked.

**Done when:** all 8 steps have been carried out on the sandbox tenant without help.

## SV-11 — Logging an RSA incident

**Module:** 06 RSA Incident Log · **Source:** `06-rsa.md` → `## Logging an RSA incident`

**Where:** **RSA Incident Log** in the main sidebar. In the current release this page shares its visibility with the SLA Policies page — an administrator needs the same permission that controls SLA Policies (see the Administration chapter) to see it.

**Do this:**

1. Open **RSA Incident Log** from the sidebar.
2. Review the **Cases by cause** and **Cases by dealer** summary at the top of the page for a running total of incidents logged so far.
3. Under **Log an incident**, fill in **Incident date**, **Vehicle no.**, and **Cause** (all required).
4. Fill in whichever of the optional fields you already know: Vehicle model, Purchased from, Breakdown location, Arrived location, Customer called-in time, Towing assigned time, Time arrived breakdown area, Time arrived outlet, Total km, Late reason, and Remarks.
5. Click **Log incident** to save it. It appears in the incidents table below, and the cause/dealer summary at the top updates immediately.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## SV-12 — Anomaly report

**Module:** 07 Reports · **Source:** `07-reports.md` → `## Anomaly report`

**Where:** **Reports → Anomaly** in the left sidebar.

**Do this:**

1. Open **Reports → Anomaly**.
2. Check the **Flagged Channels** count at the top — this is how many channels currently show anomalous volume.
3. Read the table below it: each row is a channel with its current volume, its normal baseline (mean and standard deviation), and a deviation score.
4. Treat a deviation badge in the yellow or red range as worth investigating — it means the channel's current volume sits well outside its usual pattern.
5. If a channel is flagged, cross-check the relevant Inbox report or ask the on-duty agents whether something changed (a broken integration, a marketing blast, a public holiday).

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## SV-13 — Weekly Report

**Module:** 07 Reports · **Source:** `07-reports.md` → `## Weekly Report`

**Where:** **Reports → Weekly Report** in the left sidebar.

**Do this:**

1. Open **Reports → Weekly Report**.
2. Use the week picker at the top to choose the window you're reporting on. It defaults to the current Monday–Sunday week, but its start date can be set to any day — useful if your own weekly routine runs on a different 7-day cycle (for example Friday-to-Thursday).
3. Read **Case Volume** for total cases in the window, the week-over-week change, and a breakdown by channel and by case type/division.
4. Read **Case Status Trend** for how cases split across statuses within the window.
5. Scroll through **Inquiry / Complaint / Feedback Detail — Departments & PIC**, **Call Centre & SLA Performance**, **Work-in-Progress / Case Aging**, and **Dealer Escalation Turnaround** — each section carries a small badge saying whether it's scoped to "This week" or "All time", so it's clear which numbers are windowed and which are running totals.
6. Check **Per-Case Detail** at the bottom for the individual conversations behind the week's numbers.

**Done when:** all 6 steps have been carried out on the sandbox tenant without help.

**Expect this limitation:** The page notes that Per-Case Detail (read live from current conversations) and the Case Volume total above (read from the reporting warehouse) can legitimately show slightly different counts, since they come from two different data sources — this is expected, not a bug.

## SV-14 — SLA reports

**Module:** 07 Reports · **Source:** `07-reports.md` → `## SLA reports`

**Where:** **Reports → SLA** in the left sidebar.

**Do this:**

1. Open **Reports → SLA**.
2. Review the native SLA table for individual conversations that hit or missed their SLA.
3. Scroll down to **Cross-Channel SLA Achievement** for the overall met/missed percentage across every channel, plus a per-channel breakdown chart.
4. Continue to **SLA Compliance by Bucket** for a chart of how many cases fall into each SLA time bucket, split by case type.

**Done when:** all 4 steps have been carried out on the sandbox tenant without help.

## SV-15 — SLA Policies

**Module:** 09 Administration (Settings) · **Source:** `09-administration.md` → `## SLA Policies`

**Where:** In the main left-hand navigation, select **SLA Policies** (visible only if your role has been granted the "Manage SLA policies" permission — see **Roles & Permissions** below).

**Do this:**

1. Open **SLA Policies**. Use the **Scope** dropdown to choose **Tenant default** or a specific inbox.
2. Set the **Response window (hours)** — how quickly a first reply is expected — and the **Resolution window (hours)** — how quickly the conversation is expected to be resolved.
3. Set **Tier-2 re-alert after (hours)** if you want a second, level-2 alert to fire when a case is still unresolved that many hours after its first breach — leave it blank to inherit the deployed default.
4. Set **Warn before breach (minutes)** to have the CRM raise an early warning that many minutes before a case is about to breach its resolution target, rather than waiting for the breach itself — leave it blank to inherit the deployed default.
5. Optionally set **Per-channel ACK minutes (JSON)** for a channel-specific acknowledgement target, for example `{"whatsapp": 15}`, and a **PIC WhatsApp number** to notify.
6. Leave any field empty on an inbox's policy to inherit the tenant default (or the deployed default, for Tier-2/warning) instead of setting an inbox-specific value.
7. Click **Save**. The policy applies to conversations on that scope going forward; it does not re-evaluate already-closed conversations.

**Done when:** all 7 steps have been carried out on the sandbox tenant without help.

## SV-16 — Scenario 1: WhatsApp inquiry to resolution  *(role-play)*

**Module:** 11 End-to-End Scenarios · **Source:** `11-scenarios.md` → `## Scenario 1: WhatsApp inquiry to resolution`

**Reproduce this scenario on the sandbox tenant**, with the facilitator playing the customer:

1. A customer messages Proton's WhatsApp number asking about the price and availability of a test drive for the e.MAS 7. Chatwoot creates a new conversation on the WhatsApp inbox, which is running in Suggest mode (see the AI Assistant Behaviour chapter's Suggest mode vs. Auto mode section).
2. The AI assistant drafts an answer grounded in the knowledge base and posts it as a private note, then reopens the conversation for a human (see the Conversations chapter's AI auto-draft section and the AI Assistant Behaviour chapter).
3. The on-duty agent opens the conversation, reads the suggested draft and its source citations, tweaks the wording slightly, and sends it as their own reply (see the Conversations chapter's Private notes and Suggest-a-reply sections).
4. The customer confirms they'd like to book the test drive; the agent arranges it and, once everything is confirmed, marks the conversation **Resolved** (see the Conversations chapter's Resolving, snoozing & transcripts section).
5. The customer receives the standard resolution prompt and satisfaction survey, and their 1–5 rating shows up later in the CSAT report (see the AI Assistant Behaviour chapter's Lifecycle messages section and the Reports chapter).

**Done when:** the cohort has walked all 5 steps and can say which of them the CRM did on its own.

## SV-17 — Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report  *(role-play)*

**Module:** 11 End-to-End Scenarios · **Source:** `11-scenarios.md` → `## Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report`

**Reproduce this scenario on the sandbox tenant**, with the facilitator playing the customer:

1. A customer emails in about a recurring charging fault that wasn't fixed at their last service visit. The conversation lands on the Email inbox (see the Conversations chapter's Conversation inbox & views section).
2. The agent handling it decides the case needs the dealer's attention and applies the **`dept_aftersales`** label first, then the relevant dealer's label, then **escalate** last — in that order, since the handler only picks up whichever department/dealer labels are already on the conversation at the moment `escalate` is applied (see the Conversations chapter's Labels section).
3. The CRM automatically sends the customer a short acknowledgement email and forwards the case details to the dealer group's members by email, using the contacts set up in Escalation Routing; the dealer's turnaround clock starts at the same moment (see the AI Assistant Behaviour chapter's Escalation labels & the escalation email section and the Administration chapter's Escalation Routing section).
4. Two days later, the dealer emails back confirming the fault has been repaired. Within a couple of minutes, the agent sees a new private note on the **same** conversation starting `Reply from ` with the dealer's update, and directly beneath it a second note — `Suggested customer reply (draft — review before sending):` — with an AI-drafted update already written (see the Conversations chapter's Escalation replies section).
5. The agent reads the draft, adjusts a couple of words, sends it as their own reply to the customer, and marks the conversation **Resolved**.
6. During the weekly ops review, a supervisor opens the Weekly Report page and checks the Dealer Escalation Turnaround table to see how long that dealer took to close the case, alongside every other escalation from the same week (see the Reports chapter's Weekly Report and Dealer escalation turnaround sections).

**Done when:** the cohort has walked all 6 steps and can say which of them the CRM did on its own.

## SV-18 — Scenario 5: Weekly reporting routine  *(role-play)*

**Module:** 11 End-to-End Scenarios · **Source:** `11-scenarios.md` → `## Scenario 5: Weekly reporting routine`

**Reproduce this scenario on the sandbox tenant**, with the facilitator playing the customer:

1. Every Friday afternoon, Proton's operations lead opens **Reports → Weekly Report** and sets the week picker to the period just finishing (see the Reports chapter's Weekly Report section).
2. They read **Case Volume** for the week's total and its channel/division breakdown, then **Case Status Trend** for how cases split across statuses during the window.
3. They scroll through **Inquiry / Complaint / Feedback Detail — Departments & PIC**, **Call Centre & SLA Performance**, and **Work-in-Progress / Case Aging** to note anything that needs follow-up before the client call.
4. They check **Dealer Escalation Turnaround** for any dealer whose average turnaround has crept up, ready to raise it on the call (see the AI Assistant Behaviour chapter's Escalation labels & the escalation email section for how that clock starts).
5. Figures from the page are copied into the weekly client deck; if the client asks for a deeper cut of the data than the page shows, the operations lead asks their CRM administrator to arrange a bulk export (see the Integration Overview chapter's BI/reporting exports section).

**Done when:** the cohort has walked all 5 steps and can say which of them the CRM did on its own.

## SV-19 — Scenario 10: Adjusting SLA thresholds ahead of a launch event  *(role-play)*

**Module:** 11 End-to-End Scenarios · **Source:** `11-scenarios.md` → `## Scenario 10: Adjusting SLA thresholds ahead of a launch event`

**Reproduce this scenario on the sandbox tenant**, with the facilitator playing the customer:

1. Ahead of an e.MAS 7 launch event, Proton expects a spike in email inquiries and wants a tighter response target on the Email inbox for the week, plus more advance warning before anything breaches.
2. An administrator with the SLA-management permission opens **SLA Policies**, sets **Scope** to the Email inbox, and lowers the **Response window (hours)** from 1 to 0.5 (see the Administration chapter's SLA Policies section).
3. They also set **Warn before breach (minutes)** to 30, so the team gets an early nudge with half an hour of runway rather than finding out only once a case has already breached.
4. They leave **Tier-2 re-alert after (hours)** blank, since the deployed default re-alert timing is fine for this event.
5. They click **Save** — the tighter thresholds apply to Email-inbox cases from that moment on. The following week, once the event traffic has settled, the administrator returns to the same page and clears the Response window and warning fields back to blank, restoring the tenant default.

**Done when:** the cohort has walked all 5 steps and can say which of them the CRM did on its own.

## SV-20 — Scenario 15: Categorising a case through all five taxonomy dropdowns  *(role-play)*

**Module:** 11 End-to-End Scenarios · **Source:** `11-scenarios.md` → `## Scenario 15: Categorising a case through all five taxonomy dropdowns`

**Reproduce this scenario on the sandbox tenant**, with the facilitator playing the customer:

1. A customer emails complaining that their new e.MAS 5's delivery has no estimated date. The agent opens the conversation's custom attributes panel (see the Cases chapter's Case categorisation section).
2. They set **Case Type** to `Complaint` — one of three values, and independent of everything else in the panel.
3. They set **Case Category** to `Sales`, one of eight divisions.
4. **Case Subcategory** now only offers Sales' own Level-1 values, each prefixed `Sales: ` — the agent picks `Sales: Delivery`.
5. **Case Detail** now only offers Delivery's own Level-2 values, each prefixed `Sales: Delivery: ` — the agent picks `Sales: Delivery: No Estimated Time Delivery`. Nobody pre-filled this: Case Detail is never set by the AI, only ever picked by hand.
6. They set **Vehicle Model** to `e.MAS 5` — again independent, doesn't touch the other four fields.
7. Later, the agent realizes this is actually a Charging complaint, not Sales, and changes **Case Category** to `Charging`. **Case Subcategory** and **Case Detail** both clear immediately, since `Sales: Delivery` and `Sales: Delivery: No Estimated Time Delivery` no longer match the new division — the agent reselects both from Charging's narrowed lists. **Case Type** and **Vehicle Model** are untouched by this change.

**Done when:** the cohort has walked all 7 steps and can say which of them the CRM did on its own.

## Exercises this set does not contain

An exercise exists only where the handbook documents steps a cohort can actually carry out on a sandbox tenant. The topics below are presentation-only, and the reason is recorded rather than papered over with a lab that cannot run.

### What this curriculum cannot teach yet

3 topics this role would be expected to cover are absent from the handbook source, or present only as the tenant's current behaviour. They are listed here rather than written from a specification, because a curriculum that teaches an unbuilt page loses its cohort on day one.

| Topic | Why it is not here | Unblocked by |
|---|---|---|
| Agent availability and the workforce dashboard | Held out of the handbook source on 2026-08-09 because fork patches `0053`/`0054` have never been built into an image — "My status" and "Workforce" do not appear in the deployed JS bundle. The written section is parked in `feature-guide-v3-pending.md`. | P6 · a Cloud Build of patches 0053+0054 |
| Performance targets and attainment | P5 built a targets store and an attainment view, and the deployed backend's own OpenAPI document has no `/metrics/targets`. There is no handbook section, and inventing one would teach a page no supervisor can open. | P5 · backend rebuilt past `e6dc537`, then a handbook section |
| Hands-on voice and phone practice | The voice and phone topics are taught from the handbook, but no real Twilio call has ever been placed (risk R10) and every `PHONE_*` capability switch is off on the tenant. The channel topics are therefore presentation-only: there is no exercise for them, and inventing one would be a lab that cannot run. | R10 · one real call, then a sandbox phone number |
