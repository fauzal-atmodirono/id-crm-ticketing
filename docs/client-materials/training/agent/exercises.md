<!-- GENERATED FILE — do not edit by hand.
     Source: docs/client-materials/feature-guide-src-v3/ (the operator handbook)
     Regenerate: python3 docs/client-materials/build_crm_feature_guide.py --curricula
     Drift check: python3 docs/client-materials/build_crm_feature_guide.py --check
-->

# Frontline agent curriculum — hands-on exercises

> **Generated from the operator handbook — do not edit.** Every line below is rendered from `feature-guide-src-v3/`; an edit here is overwritten by the next run. To change what a cohort is taught, change the handbook section this points at, or its `<!-- TRAINING: ... -->` marker, and regenerate.

**12 exercises.** Every step is the handbook's own documented procedure for that feature, so an exercise cannot drift from the guide the cohort keeps.

> **Run these on the sandbox tenant only.** Training an agent to escalate by escalating a real customer's complaint is not a viable exercise. Reset between cohorts with `./reset-sandbox-tenant.sh` (see `../delivery-plan.md` §4).

> **NOT YET DRY-RUN.** No exercise in this set has been executed against a sandbox tenant — no sandbox tenant has been provisioned, and the environment these were generated in has no live Chatwoot, Gemini or Twilio. "Completable as written" is therefore **owed, not verified**. Dry-run the set once before the first cohort and record the result in `../delivery-plan.md`.

**9 are procedures** the trainee carries out themselves, taken from a handbook section's *How to use it* steps. **3 are role-plays** drawn from the end-to-end scenarios, where the facilitator plays the customer — those narratives are told from both sides, so they are not a checklist one trainee can work through alone.

## AG-01 — Conversation inbox & views

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Conversation inbox & views`

**Where:** The left-hand column of the Conversations view, always visible once you're signed in.

**Do this:**

1. Use the assignee tabs to switch between **Mine**, **Unassigned**, and **All** conversations.
2. Use the status filter to narrow the list to **Open**, **Pending**, **Snoozed**, or **Resolved** conversations, or leave it on **All** to see everything regardless of status.
3. Click an inbox name in the sidebar to scope the list to a single channel (for example, just WhatsApp).
4. Click any conversation in the list to open its full thread.
5. Use the sort option to reorder the list, for example by most recent activity.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## AG-02 — Labels

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

## AG-03 — Private notes

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Private notes`

**Where:** The reply box's note mode, toggled from the main "reply" mode.

**Do this:**

1. Open the conversation and click into the reply box.
2. Switch the reply box to its private "Note" mode.
3. Type your note — mention a teammate with @ if you need their input (see Mentions below).
4. Send the note; it posts as private and is visually distinct from a customer-facing reply.

**Done when:** all 4 steps have been carried out on the sandbox tenant without help.

## AG-04 — Canned responses

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Canned responses`

**Where:** A shortcut inside the reply box, typically triggered by typing a character like "/" or clicking a canned-response icon.

**Do this:**

1. Click into the reply box.
2. Trigger the canned-response search (for example, typing "/").
3. Search by the short code or keyword for the response you need.
4. Select it — it's inserted into the reply box.
5. Edit if needed, then send as normal.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## AG-05 — Ask Copilot panel

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

## AG-06 — Suggest-a-reply

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Suggest-a-reply`

**Where:** A "Suggest a reply" action above the reply box.

**Do this:**

1. Open the conversation.
2. Click "Suggest a reply" above the reply box.
3. Wait a moment for the draft to appear in the reply box.
4. Check the Sources line underneath the draft for the knowledge-base articles it drew on.
5. Edit the draft if needed, then send it like any other reply.

**Done when:** all 5 steps have been carried out on the sandbox tenant without help.

## AG-07 — Resolving, snoozing & transcripts

**Module:** 02 Conversations · **Source:** `02-conversations.md` → `## Resolving, snoozing & transcripts`

**Where:** Action buttons in the conversation header, and a menu option for exporting the transcript.

**Do this:**

1. Once the customer's issue is fully handled, click **Resolve**.
2. If you need to come back to a conversation later — for example, waiting on the customer or a dealer — click **Snooze** and choose when it should reopen.
3. A snoozed conversation reopens automatically at the chosen time, or immediately if the customer replies first.
4. To get a transcript of the conversation, open the conversation's menu and choose the transcript/export option.

**Done when:** all 4 steps have been carried out on the sandbox tenant without help.

## AG-08 — Contacts list & search

**Module:** 03 Contacts · **Source:** `03-contacts.md` → `## Contacts list & search`

**Where:** **Contacts** in the main sidebar.

**Do this:**

1. Open **Contacts** from the sidebar to see the full customer list.
2. Use the search box to find a customer by name, phone number, or email address.
3. Use the available filters to narrow the list (for example, by the channel a customer last used).
4. Click a customer's row to open their contact profile.

**Done when:** all 4 steps have been carried out on the sandbox tenant without help.

## AG-09 — Case categorisation (five fields)

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

## AG-10 — Scenario 1: WhatsApp inquiry to resolution  *(role-play)*

**Module:** 11 End-to-End Scenarios · **Source:** `11-scenarios.md` → `## Scenario 1: WhatsApp inquiry to resolution`

**Reproduce this scenario on the sandbox tenant**, with the facilitator playing the customer:

1. A customer messages Proton's WhatsApp number asking about the price and availability of a test drive for the e.MAS 7. Chatwoot creates a new conversation on the WhatsApp inbox, which is running in Suggest mode (see the AI Assistant Behaviour chapter's Suggest mode vs. Auto mode section).
2. The AI assistant drafts an answer grounded in the knowledge base and posts it as a private note, then reopens the conversation for a human (see the Conversations chapter's AI auto-draft section and the AI Assistant Behaviour chapter).
3. The on-duty agent opens the conversation, reads the suggested draft and its source citations, tweaks the wording slightly, and sends it as their own reply (see the Conversations chapter's Private notes and Suggest-a-reply sections).
4. The customer confirms they'd like to book the test drive; the agent arranges it and, once everything is confirmed, marks the conversation **Resolved** (see the Conversations chapter's Resolving, snoozing & transcripts section).
5. The customer receives the standard resolution prompt and satisfaction survey, and their 1–5 rating shows up later in the CSAT report (see the AI Assistant Behaviour chapter's Lifecycle messages section and the Reports chapter).

**Done when:** the cohort has walked all 5 steps and can say which of them the CRM did on its own.

## AG-11 — Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report  *(role-play)*

**Module:** 11 End-to-End Scenarios · **Source:** `11-scenarios.md` → `## Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report`

**Reproduce this scenario on the sandbox tenant**, with the facilitator playing the customer:

1. A customer emails in about a recurring charging fault that wasn't fixed at their last service visit. The conversation lands on the Email inbox (see the Conversations chapter's Conversation inbox & views section).
2. The agent handling it decides the case needs the dealer's attention and applies the **`dept_aftersales`** label first, then the relevant dealer's label, then **escalate** last — in that order, since the handler only picks up whichever department/dealer labels are already on the conversation at the moment `escalate` is applied (see the Conversations chapter's Labels section).
3. The CRM automatically sends the customer a short acknowledgement email and forwards the case details to the dealer group's members by email, using the contacts set up in Escalation Routing; the dealer's turnaround clock starts at the same moment (see the AI Assistant Behaviour chapter's Escalation labels & the escalation email section and the Administration chapter's Escalation Routing section).
4. Two days later, the dealer emails back confirming the fault has been repaired. Within a couple of minutes, the agent sees a new private note on the **same** conversation starting `Reply from ` with the dealer's update, and directly beneath it a second note — `Suggested customer reply (draft — review before sending):` — with an AI-drafted update already written (see the Conversations chapter's Escalation replies section).
5. The agent reads the draft, adjusts a couple of words, sends it as their own reply to the customer, and marks the conversation **Resolved**.
6. During the weekly ops review, a supervisor opens the Weekly Report page and checks the Dealer Escalation Turnaround table to see how long that dealer took to close the case, alongside every other escalation from the same week (see the Reports chapter's Weekly Report and Dealer escalation turnaround sections).

**Done when:** the cohort has walked all 6 steps and can say which of them the CRM did on its own.

## AG-12 — Scenario 15: Categorising a case through all five taxonomy dropdowns  *(role-play)*

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

1 topic this role would be expected to cover is absent from the handbook source, or present only as the tenant's current behaviour. They are listed here rather than written from a specification, because a curriculum that teaches an unbuilt page loses its cohort on day one.

| Topic | Why it is not here | Unblocked by |
|---|---|---|
| Hands-on voice and phone practice | The voice and phone topics are taught from the handbook, but no real Twilio call has ever been placed (risk R10) and every `PHONE_*` capability switch is off on the tenant. The channel topics are therefore presentation-only: there is no exercise for them, and inventing one would be a lab that cannot run. | R10 · one real call, then a sandbox phone number |
