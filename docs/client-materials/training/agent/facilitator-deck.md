<!-- GENERATED FILE — do not edit by hand.
     Source: docs/client-materials/feature-guide-src-v3/ (the operator handbook)
     Regenerate: python3 docs/client-materials/build_crm_feature_guide.py --curricula
     Drift check: python3 docs/client-materials/build_crm_feature_guide.py --check
-->

# Frontline agent curriculum — facilitator deck

> **Generated from the operator handbook — do not edit.** Every line below is rendered from `feature-guide-src-v3/`; an edit here is overwritten by the next run. To change what a cohort is taught, change the handbook section this points at, or its `<!-- TRAINING: ... -->` marker, and regenerate.

**Audience:** Frontline agent · **Topics:** 54 of 108 handbook sections · **Hands-on exercises:** 12

**Rule-derived length:** 5 h 31 min. **Design target (spec §3.1):** 2 h. **Difference: +3 h 31 min.**

> Durations are **derived by rule** — 3 min per topic, plus 1 min per two documented steps, plus 5 min where the cohort does it themselves — and are **not measured**: no session has been delivered or timed. Where the derived length exceeds the design target, either the target or the topic list has to move; the generator will not scale one into agreement with the other.

## How to run a slide

Each topic below is one slide. **Say** is the handbook section's opening paragraph — read the whole section before delivering it. **Show** is where the feature lives in the CRM. **Walk through** is the documented procedure, verbatim, so a demo cannot drift from the handbook the cohort takes away. **Say out loud** carries the section's own caveats; skipping those is how a cohort learns a limitation from a customer instead.

## Module 01 — Introduction  ·  5 topics  ·  27 min

### What is Proton e.MAS CRM  ·  5 min  ·  agent+

**Source:** `01-introduction.md` → `## What is Proton e.MAS CRM`

**Say:** Proton e.MAS CRM is Proton's unified customer support platform for the e.MAS electric-vehicle business. It brings every customer conversation — WhatsApp, email, and phone/IVR calls — into a single inbox, adds an AI assistant that can draft or send replies, and layers on automotive support tools such as roadside-assistance (RSA) incident logging, a vehicle/service lookup (Customer 360), case tracking, and reporting built around dealer and PIC (person-in-charge) escalation.

**Show:** The platform is reached at your organization's CRM web address, provided by your administrator. Bookmark it — you'll come back to it every shift.

**Walk through:**

1. Open a web browser and go to your organization's CRM address.
2. Sign in with the account your administrator created for you (see **Logging in** below).
3. After signing in you land on the **Conversations** view — the default screen showing every conversation across the inboxes you have access to.
4. Use the sidebar on the left to move between Conversations, Contacts, Knowledge, Cases, RSA, Reports, and (for administrators) Administration.

### Logging in  ·  6 min  ·  agent+

**Source:** `01-introduction.md` → `## Logging in`

**Say:** The sign-in screen that authenticates you as an agent or administrator before you can see any conversations or customer data.

**Show:** Your CRM web address opens directly to the login screen if you are not already signed in.

**Walk through:**

1. Go to your organization's CRM address.
2. Enter the email address and password your administrator set up for you.
3. Click the sign-in button.
4. If you forget your password, use the "Forgot password?" link to request a reset email.
5. Once signed in, you stay logged in on that browser until you sign out or your session expires.

### Screen layout  ·  6 min  ·  agent+

**Source:** `01-introduction.md` → `## Screen layout`

**Say:** The main working screen you see after logging in: a navigation sidebar on the left, the conversation list in the middle-left column, the open conversation with its reply box in the center, and a contact side panel on the right.

**Show:** This layout is what you see any time you are inside the Conversations area of the CRM.

**Walk through:**

1. Use the **sidebar** on the far left to switch between Conversations, Contacts, Knowledge, Cases, RSA, Reports, Campaigns/Help Center, and (for administrators) Administration and other admin-only pages.
2. Use the **conversation list** to browse and filter the conversations in your inboxes — see the Conversations chapter for the filters available.
3. Click a conversation to open it in the **main conversation pane**, where the message thread and reply box live.
4. Check the **contact side panel** on the right for the customer's details and history alongside the conversation you're reading.
5. Look for AI-assist buttons (Ask Copilot, Suggest a reply, Summarize) above the reply box when you need help drafting a response.

### Roles: agent vs administrator  ·  5 min  ·  agent+

**Source:** `01-introduction.md` → `## Roles: agent vs administrator`

**Say:** Every account has a base role — **Agent** or **Administrator** — that controls what you can see and do. Administrators can additionally be granted specific permissions (for example, managing escalation routing, SLA policies, integrations, or viewing the audit log), so two administrators may not see exactly the same admin pages unless they've been granted the same permissions.

**Show:** Your role is set by an administrator when your account is created, under Administration → Agents. Fine-grained permissions are managed under Administration → Roles & Permissions.

**Walk through:**

1. As an agent, you see Conversations, Contacts, and Knowledge (read access) — the day-to-day support tools.
2. As an administrator, you additionally see Cases, the RSA Incident Log, Customer 360, Reports, and Administration together with any admin-only pages your permissions grant, such as Integrations, Escalation Routing, SLA Policies, Audit Log, and Roles & Permissions.
3. If you believe you're missing access you should have, ask an administrator to check your role and permissions under Administration → Roles & Permissions.

### Language (English / Indonesian)  ·  5 min  ·  agent+

**Source:** `01-introduction.md` → `## Language (English / Indonesian)`

**Say:** A setting that switches the CRM's interface text between English and Indonesian. It changes labels and menus, not the language customers write to you in.

**Show:** Your profile settings menu, usually reached from your avatar/name in the top corner of the screen.

**Walk through:**

1. Open your profile settings from your avatar or name.
2. Find the interface language option.
3. Choose English or Indonesian (Bahasa Indonesia).
4. The interface labels update immediately (or after a page refresh).

## Module 02 — Conversations  ·  17 topics  ·  2 h 8 min

### Conversation inbox & views  ·  11 min  ·  agent+

**Source:** `02-conversations.md` → `## Conversation inbox & views`

**Say:** The conversation list is where every incoming conversation lands — WhatsApp, the website chatbot, email, and phone/IVR alike — filterable by who it's assigned to and by its status. By default the CRM shows **every** conversation across **all** statuses, so nothing is hidden behind a narrower filter unless you choose one.

**Show:** The left-hand column of the Conversations view, always visible once you're signed in.

**Walk through:**

1. Use the assignee tabs to switch between **Mine**, **Unassigned**, and **All** conversations.
2. Use the status filter to narrow the list to **Open**, **Pending**, **Snoozed**, or **Resolved** conversations, or leave it on **All** to see everything regardless of status.
3. Click an inbox name in the sidebar to scope the list to a single channel (for example, just WhatsApp).
4. Click any conversation in the list to open its full thread.
5. Use the sort option to reorder the list, for example by most recent activity.

**Hands-on:** exercise `AG-01` — see `exercises.md`.

### Assignment & teams  ·  5 min  ·  agent+

**Source:** `02-conversations.md` → `## Assignment & teams`

**Say:** Assignment determines which agent — or which team — owns a conversation. An unassigned conversation is visible to the whole team until someone picks it up or it's assigned.

**Show:** The assignee and team controls in the conversation's action panel, next to an open conversation.

**Walk through:**

1. Open the conversation you want to assign.
2. Click the assignee control and choose an agent, or click the team control and choose a team.
3. To reassign, repeat the same steps and pick a different agent or team.
4. To unassign, clear the assignee field.

### Labels  ·  11 min  ·  agent+

**Source:** `02-conversations.md` → `## Labels`

**Say:** Labels are tags you attach to a conversation to categorize it. Alongside general-purpose labels, Proton uses two special families: an **escalate** label that flags a conversation for the escalation email workflow, and `dept_<slug>`/dealer-specific labels used to route that escalation and, for dealer labels, to feed turnaround-time reporting. **All six departments now route somewhere**: `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, and `dept_technical` each have a PIC configured, and both live dealer groups — `dealer_komang_motor` and `dealer_caroline_motor` — do too. Previously most department labels had no PIC behind them, so escalating with one sent no email to anyone, with no error to warn you; that's fixed. A dealer label now also routes to a **dealer group** — every member of that group is forwarded the case, not just one address (see the Administration chapter's Escalation Routing section).

**Show:** The label control on an open conversation, and a Labels view in the sidebar listing conversations by label.

**Walk through:**

1. Open the conversation you want to label.
2. Click the label control and pick one or more existing labels.
3. **Apply the department label first** — one of `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, or `dept_technical` — then the dealer label if one is involved, and apply **escalate** last. The escalation handler reads whichever labels are already on the conversation at the moment `escalate` is applied — applying it first means the department/dealer leg doesn't fire for that trigger. On an Email-channel conversation with no department label yet, you may see a private note suggesting one before you get here — see AI-suggested escalation department, below.
4. Apply the relevant dealer label so the conversation counts toward that dealer's turnaround reporting, even outside an email escalation.
5. Remove a label the same way, by deselecting it.
6. **A limitation worth knowing before you rely on this:** the automated escalation email only fires on an **Email**-channel conversation. Applying `escalate` on a WhatsApp, web chatbot, or phone conversation changes the label and nothing else — no one is notified. The dealer label still records the case for turnaround reporting on any channel; that part isn't affected. See the Integration Overview chapter's WhatsApp, Web chatbot, and Phone sections for what to actually do on those channels instead.

**Hands-on:** exercise `AG-02` — see `exercises.md`.

### AI-suggested escalation department  ·  5 min  ·  agent+

**Source:** `02-conversations.md` → `## AI-suggested escalation department`

**Say:** **New since the last edition of this guide.** On an Email-channel conversation with no `dept_<slug>` label yet, an incoming customer message can trigger a private note naming the department the AI thinks is the best fit — for example:

**Show:** As a private note on the conversation, the same place any other private note appears (see Private notes, below). Not something an agent turns on — it's an administrator/tenant-level setting, off by default, and live today on this tenant.

**Walk through:**

1. Watch for a private note starting `AI-suggested escalation department:` on an Email conversation you haven't yet labelled with a department.
2. **To act on it:** add the exact `dept_<slug>` label the note names — for example `dept_pre_sales` — yourself, from the label control, then continue the normal escalation order (dealer label if needed, then `escalate` last; see Labels, above). The note doesn't do this for you.
3. **To ignore it:** simply don't add the suggested label. Nothing happens automatically either way — you can pick a different department, or none at all, and the conversation carries on exactly as if the note weren't there. There's no "reject" button to click; not acting on it is the reject.
4. If you disagree with the suggestion, trust your own read of the case over the note — it's a best-effort classification from a single Gemini call over the recent transcript, not a guarantee.

**Say out loud:** AI-suggested escalation department: **pre_sales**. This is a suggestion only — no label has been applied. If you agree, add the `dept_pre_sales` label yourself, BEFORE the `escalate` label…

### Escalation replies  ·  6 min  ·  agent+

**Source:** `02-conversations.md` → `## Escalation replies`

**Say:** When a dealer or PIC replies to an escalation email, their reply doesn't start a fresh conversation you have to go hunting for — it's linked back onto the original conversation automatically, as a private note starting `Reply from <name> <email>:`. If a customer instead replies to their own acknowledgement email, that reply rejoins their case too and the conversation reopens — but **correction from the previous edition of this guide: it does not arrive as an ordinary incoming message.** Chatwoot only accepts a synthetic incoming message on an Api-channel inbox; every Email-channel conversation (which is the only channel this loop ever runs on) rejects it with `{"error":"Incoming messages are only allowed in Api inboxes"}`. The CRM tries the inline post first, and it always fails on this tenant's Email inbox today, so what you actually see is a **private note**, prefixed `Customer's own reply (from <email>, could not be posted inline -- see conversation <id>):`, followed by the customer's own words. The reopen still happens either way — that part behaves exactly as before.

**Show:** The reply lands as a new message on the **original** escalated conversation — not on any new conversation that may briefly appear elsewhere in the inbox. A throwaway conversation is created behind the scenes to receive the raw email, but it's automatically labelled and resolved, so it shouldn't show up in your queue as something to work.

**Walk through:**

1. Watch a conversation you've already escalated for a new private note starting `Reply from `. That's the dealer or PIC's reply, with any quoted trail and signature stripped out.
2. Directly beneath it, look for a second private note titled exactly `Suggested customer reply (draft — review before sending):`. This is an AI-drafted reply based on what the dealer said — read it, edit it to sound right, and send it as your own reply. It never goes to the customer on its own.
3. The conversation also picks up an `escalation_replied` label, so you can spot which escalated cases already have an answer waiting for review just by scanning labels in the conversation list.
4. **If the reply instead comes from the customer** replying to their own acknowledgement email, watch instead for a private note prefixed `Customer's own reply (from <email>, could not be posted inline -- see conversation <id>):`, followed by their own words — not a normal incoming message, on this tenant's Email inbox. The conversation still reopens by itself either way. **What to actually do:** read the note as if it were the customer's message (it is, word for word, just delivered as a note instead of an inline bubble), then reply publicly the same way you would to any other reopened case — don't wait for a "real" incoming message that this loop is never going to post here.
5. A second reply from the same dealer/PIC address after the first one's already linked in doesn't post another note — check the dealer's own mailbox directly if they say they've replied twice. A customer can reply more than once without limit; each reply lands as its own private note the same way.

### SLA breach alerts  ·  5 min  ·  agent+

**Source:** `02-conversations.md` → `## SLA breach alerts`

**Say:** If a conversation on the Email inbox goes past its response or resolution target without action, the CRM posts a private note on that conversation flagging the breach, and separately emails the department's PIC group (resolved from the conversation's own department label) so the breach doesn't rely on someone happening to notice the note.

**Show:** The private note appears directly in the conversation's timeline, marked private the same way any other internal note is. The matching email goes to whichever PIC group is configured for that conversation's department under Escalation Routing (see the Administration chapter).

**Walk through:**

1. Watch for a private note starting `⚠️ SLA breach` on a conversation — it names which target was missed (first response or resolution) and the case number.
2. Treat it as a prompt to act on the conversation now, not just a record — the department's PIC group has already been emailed the same breach, so a customer-facing follow-up may already be expected.
3. Respond to or resolve the conversation as normal; a second scan doesn't re-alert for the same breach once it's been recorded, so you won't see the note repeat on its own.
4. If a breach note appears but you believe the response/resolution target is wrong for this inbox, check — or ask an administrator to check — the thresholds under SLA Policies (see the Administration chapter).

### Priorities  ·  5 min  ·  agent+

**Source:** `02-conversations.md` → `## Priorities`

**Say:** A priority flag (for example Urgent, High, Medium, Low, or none) you can set on a conversation to indicate how urgently it needs attention. It shows as a badge in the conversation list so high-priority conversations stand out.

**Show:** The priority control in the conversation's action panel.

**Walk through:**

1. Open the conversation you want to flag.
2. Click the priority control.
3. Choose the priority level that matches how urgently it needs handling.
4. Change it at any point as the situation develops.

### Private notes  ·  10 min  ·  agent+

**Source:** `02-conversations.md` → `## Private notes`

**Say:** Internal messages attached to a conversation that only agents and administrators can see — never the customer. Used for handoff context, questions to a teammate, or reviewing an AI-drafted reply before sending it.

**Show:** The reply box's note mode, toggled from the main "reply" mode.

**Walk through:**

1. Open the conversation and click into the reply box.
2. Switch the reply box to its private "Note" mode.
3. Type your note — mention a teammate with @ if you need their input (see Mentions below).
4. Send the note; it posts as private and is visually distinct from a customer-facing reply.

**Hands-on:** exercise `AG-03` — see `exercises.md`.

### Canned responses  ·  11 min  ·  agent+

**Source:** `02-conversations.md` → `## Canned responses`

**Say:** Pre-written reply snippets for common questions, so agents don't retype the same answer over and over.

**Show:** A shortcut inside the reply box, typically triggered by typing a character like "/" or clicking a canned-response icon.

**Walk through:**

1. Click into the reply box.
2. Trigger the canned-response search (for example, typing "/").
3. Search by the short code or keyword for the response you need.
4. Select it — it's inserted into the reply box.
5. Edit if needed, then send as normal.

**Hands-on:** exercise `AG-04` — see `exercises.md`.

### Macros  ·  6 min  ·  agent+

**Source:** `02-conversations.md` → `## Macros`

**Say:** A macro bundles a sequence of actions — for example, adding a label, assigning a team, sending a reply, and marking the conversation resolved — so an agent can run all of them in a single click instead of doing each step by hand.

**Show:** The macro option in the conversation's action menu, inside an open conversation.

**Walk through:**

1. Open the conversation you want to act on.
2. Open the macro menu from the conversation's action area.
3. Pick the macro you want to run from the list.
4. Confirm — its steps run automatically in order (for example: apply a label, send a reply, then resolve).
5. Check the conversation afterward to confirm every step applied as expected.

### Mentions  ·  5 min  ·  agent+

**Source:** `02-conversations.md` → `## Mentions`

**Say:** Typing @ followed by a teammate's name inside a private note to notify them directly — useful for handoffs or asking for a second opinion.

**Show:** Inside the private-note editor in the reply box.

**Walk through:**

1. Switch the reply box to note mode.
2. Type @ followed by the teammate's name.
3. Select them from the suggestion list that appears.
4. Finish writing your note and send it — the mentioned teammate is notified.

### Ask Copilot panel  ·  11 min  ·  agent+

**Source:** `02-conversations.md` → `## Ask Copilot panel`

**Say:** A chat panel next to the conversation where an agent can ask an AI assistant questions about the customer or the topic at hand, and get answers grounded in the knowledge base. Each answer is followed by a "Looked at" line naming which knowledge tools it consulted, and — when sources are available — a "Sources" line with each source title, shown as a clickable link where one exists or as plain text otherwise.

**Show:** Opened from a Copilot button above the reply box, on the right side of the conversation.

**Walk through:**

1. Open the conversation you need help with.
2. Click the Copilot button above the reply box.
3. Type your question in the panel's chat box and send it.
4. Read the answer, along with the "Looked at" line noting which knowledge tools (for example, a knowledge-base search) it used to answer, and the "Sources" line underneath when it has one — click a source to open it, or read its title if it isn't linked.
5. Click "Insert into reply" on any answer you'd like to drop straight into your draft.
6. Reset the panel to start a fresh question thread, or close it when you're done.

**Hands-on:** exercise `AG-05` — see `exercises.md`.

### Suggest-a-reply  ·  11 min  ·  agent+

**Source:** `02-conversations.md` → `## Suggest-a-reply`

**Say:** A one-click AI-drafted reply for the conversation you're currently in, based on the conversation so far and the knowledge base, placed directly in the reply box for you to review before sending — with a "Sources" line showing what it was grounded in.

**Show:** A "Suggest a reply" action above the reply box.

**Walk through:**

1. Open the conversation.
2. Click "Suggest a reply" above the reply box.
3. Wait a moment for the draft to appear in the reply box.
4. Check the Sources line underneath the draft for the knowledge-base articles it drew on.
5. Edit the draft if needed, then send it like any other reply.

**Hands-on:** exercise `AG-06` — see `exercises.md`.

### Summarize conversation  ·  6 min  ·  agent+

**Source:** `02-conversations.md` → `## Summarize conversation`

**Say:** Generates a short summary of the conversation so far — useful for a quick catch-up or for handing a conversation off to another agent.

**Show:** A "Summarize" action above the reply box.

**Walk through:**

1. Open the conversation you want to summarize.
2. Click "Summarize" above the reply box.
3. Wait a moment for the summary to be generated.
4. The summary is inserted into the reply box as a draft, switched to private "Note" mode — it is not posted automatically, so review it and click send yourself to add it to the conversation.
5. Read it before handing the conversation to a teammate, or to refresh your own memory of a long-running case.

### AI auto-draft and suggest-vs-auto mode  ·  5 min  ·  agent+

**Source:** `02-conversations.md` → `## AI auto-draft and suggest-vs-auto mode`

**Say:** The AI assistant can act on incoming customer messages on its own, without an agent asking it to. It always works in one of two modes, set per inbox by an administrator: **suggest mode** (the default), where it drafts a reply as a private note and reopens the conversation for a human to review and send; or **auto mode**, where it sends its reply straight to the customer.

**Show:** Not something an agent turns on or off — it's configured per inbox by an administrator. Agents simply see its effects appear inside conversations.

**Walk through:**

1. Watch for a private note marked as a suggested reply on a conversation the AI has just handled — that means the inbox is in suggest mode.
2. Read the suggested draft, edit it if needed, then send it yourself as a normal reply. The AI never sends this for you.
3. On an inbox running in auto mode, the AI's reply is sent to the customer directly and the conversation stays in **Pending** while the AI keeps handling it — it only moves to **Open** once the AI hands off or an agent steps in.
4. If the AI can't confidently help, it hands the conversation off to a human on its own — reopening it and, if configured, posting a brief acknowledgement to the customer first.

### Contact side panel  ·  5 min  ·  agent+

**Source:** `02-conversations.md` → `## Contact side panel`

**Say:** A panel next to the conversation showing the customer's details, past conversations, and notes, so you don't have to leave the conversation to see who you're talking to.

**Show:** The right side of an open conversation. It now opens by default, rather than needing to be expanded manually each time.

**Walk through:**

1. Open any conversation — the contact panel opens automatically.
2. Scroll through it to see contact details, conversation history, and any existing notes.
3. Collapse or expand it manually if you want more room for the conversation itself; your choice is remembered for next time.

### Resolving, snoozing & transcripts  ·  10 min  ·  agent+

**Source:** `02-conversations.md` → `## Resolving, snoozing & transcripts`

**Say:** The actions that close out a conversation (**Resolved**), pause it for later (**Snoozed**), or let you review and export its full message history (**transcript**).

**Show:** Action buttons in the conversation header, and a menu option for exporting the transcript.

**Walk through:**

1. Once the customer's issue is fully handled, click **Resolve**.
2. If you need to come back to a conversation later — for example, waiting on the customer or a dealer — click **Snooze** and choose when it should reopen.
3. A snoozed conversation reopens automatically at the chosen time, or immediately if the customer replies first.
4. To get a transcript of the conversation, open the conversation's menu and choose the transcript/export option.

**Hands-on:** exercise `AG-07` — see `exercises.md`.

## Module 03 — Contacts  ·  3 topics  ·  20 min

### Contacts list & search  ·  10 min  ·  agent+

**Source:** `03-contacts.md` → `## Contacts list & search`

**Say:** The Contacts area lists everyone who has ever messaged in — across WhatsApp, email, and phone/IVR — as a single directory of customers, separate from the Conversations view's per-channel threads.

**Show:** **Contacts** in the main sidebar.

**Walk through:**

1. Open **Contacts** from the sidebar to see the full customer list.
2. Use the search box to find a customer by name, phone number, or email address.
3. Use the available filters to narrow the list (for example, by the channel a customer last used).
4. Click a customer's row to open their contact profile.

**Hands-on:** exercise `AG-08` — see `exercises.md`.

### Contact profile & history  ·  5 min  ·  agent+

**Source:** `03-contacts.md` → `## Contact profile & history`

**Say:** A contact's profile is a single page showing everything the CRM knows about that customer: their basic details and custom attributes (for example, vehicle model), plus every conversation they've ever had across every channel.

**Show:** Click any customer in the Contacts list, or open the contact side panel from an active conversation and click through to the full profile.

**Walk through:**

1. Open a contact from the Contacts list or from an open conversation's contact panel.
2. Review their contact details and custom attributes at the top of the profile.
3. Scroll the conversation history to see every past conversation with this customer, regardless of channel or status.
4. Click any past conversation in the list to reopen and review it.

### Notes & segments  ·  5 min  ·  agent+

**Source:** `03-contacts.md` → `## Notes & segments`

**Say:** Notes let staff record customer-level context that isn't tied to a single conversation (for example, a standing delivery preference). Segments are saved contact filters — a named, reusable version of a Contacts search you'd otherwise have to rebuild every time.

**Show:** Notes live on the contact's profile; segments are created from the Contacts list's filter/search bar and then appear as a saved view in the Contacts sidebar area.

**Walk through:**

1. To add a note, open the contact's profile and use the notes area to record context about that customer.
2. To create a segment, open Contacts, build a filter (for example, by channel or a custom attribute), and save it with a name.
3. Reopen a saved segment at any time from the Contacts sidebar to reapply the same filter without rebuilding it.

## Module 05 — Cases  ·  4 topics  ·  27 min

### Case categorisation (five fields)  ·  12 min  ·  agent+

**Source:** `05-cases.md` → `## Case categorisation (five fields)`

**Say:** **New since the last edition of this guide.** Every conversation can now carry five separate case-categorisation fields, each a single-select dropdown in the conversation's custom attributes panel, matching the client's RFP taxonomy exactly (RFP 2026_028, Appendix A):

**Show:** The conversation's custom attributes panel, alongside the conversation (the same panel used for any other custom attribute). Case Type and Vehicle Model are new rows in that same panel — before this edition, neither had a custom-attribute definition at all, so those two dropdowns didn't render there yet.

**Walk through:**

1. Open the conversation you want to categorize, and open its custom attributes panel.
2. Choose a value for **Case Type** — `Inquiry`, `Complaint`, or `Compliment & Feedback`. This doesn't affect any other field.
3. Choose a value for **Case Category** (the division — for example `Sales`, `Aftersales`, `Charging`, `Apps`, `Product`, `Network`, `Marketing`, or `Others`).
4. Choose a value for **Case Subcategory** — only Level 1 values belonging to the division you just picked are offered, each shown with its `<Division>: ` prefix.
5. Choose a value for **Case Detail** — only Level 2 (and folded Level 3/4) values belonging to the subcategory you just picked are offered, each shown with its full `<Division>: <Level 1>: ` prefix. If the subcategory has no Level 2 in the source taxonomy, this list is empty and there's nothing further to pick — that's expected, not an error.
6. Choose a value for **Vehicle Model** if the case concerns a specific vehicle (`e.MAS 5`, `e.MAS 7`, `e.MAS 7 PHEV`) or `Not Applicable` otherwise. This also doesn't affect any other field.
7. Changing **Case Category** after Case Subcategory/Case Detail are already set clears both, since they no longer match the new division — reselect them from the narrowed lists. The same happens one level down if you only change Case Subcategory.
8. Save, or move on — most CRM attribute panels save automatically as soon as a value is picked.

**Hands-on:** exercise `AG-09` — see `exercises.md`.

### Case lifecycle & status  ·  5 min  ·  agent+

**Source:** `05-cases.md` → `## Case lifecycle & status`

**Say:** A case doesn't have a lifecycle of its own — its Status column simply shows the underlying conversation's status (for example Open, Pending, Snoozed, or Resolved). Resolving, reopening, or snoozing the conversation does the same thing to the case; there's no separate case state to keep in sync.

**Show:** The Status column in the Cases list; the same status is also shown and changed on the conversation itself (see the Conversations chapter).

**Walk through:**

1. In the Cases list, use the Status filter to see cases in a particular state (for example, only Open cases).
2. Open a case's conversation (click its Case ID) to change its status — resolve it, snooze it, or reopen it, exactly as covered in the Conversations chapter.
3. Return to the Cases list (or refresh it) to see the Status column reflect the change.
4. Watch the Aging (days) column alongside Status — it counts days since the conversation was created regardless of status, so a case can be both "old" and already resolved.

### How cases relate to conversations  ·  5 min  ·  agent+

**Source:** `05-cases.md` → `## How cases relate to conversations`

**Say:** There is no separate "case" record behind the scenes — every conversation is a case. The Cases list is simply a different view of the same conversations shown in the Conversations chapter, built from each conversation's category/subcategory, labels, status, and contact information.

**Show:** Anywhere a conversation lives: the Conversations view, the conversation's custom attributes panel, and the contact's own record all feed what the Cases list displays.

**Walk through:**

1. Treat categorizing a conversation (see Case categories, above) as the same action as categorizing its case — there's nothing extra to do.
2. Make sure the contact's vehicle number and dealer/purchase details are filled in on their profile (see the Contacts chapter), since the Cases list's Car Plate and Purchased From columns are read from there.
3. Once a conversation has these details, it appears correctly in the Cases list the next time the list loads — no separate publishing step.

### Escalation status on a case  ·  5 min  ·  agent+

**Source:** `05-cases.md` → `## Escalation status on a case`

**Say:** A case that's been escalated by email carries the same labels its underlying conversation does, so a supervisor scanning the Cases list can tell an escalation's state without opening it: whether it's simply been escalated (`escalate`), whether the dealer or PIC has since replied (`escalation_replied`), and which dealer it went to (the `Escalated To` column, and any `dealer_<slug>` label).

**Show:** The **Escalated To** column in the Cases list, and the conversation's own labels once you open it — see the Conversations chapter's Labels and Escalation replies sections for the full mechanics.

**Walk through:**

1. In the Cases list, look at **Escalated To** for cases that have already been forwarded somewhere.
2. Open a case whose escalation you want to check on. If the dealer or PIC has replied, the conversation carries the `escalation_replied` label and two private notes — the dealer's reply and an AI-drafted customer reply waiting for review (see the Conversations chapter's Escalation replies section for what those look like and how to act on them).
3. If a case shows `escalate` but no `escalation_replied` after a reasonable wait, that's your signal to follow up with the dealer/PIC directly rather than assume the CRM will surface a reply that hasn't arrived yet.
4. Use the Agent column (above) alongside this to see who's responsible for following up on a stalled escalation.

## Module 10 — AI Assistant Behaviour  ·  6 topics  ·  31 min

### When the AI replies vs. hands off to a human  ·  5 min  ·  agent+

**Source:** `10-ai-behaviour.md` → `## When the AI replies vs. hands off to a human`

**Say:** The AI assistant only acts on a conversation while it is genuinely waiting for a first response — a **Pending** conversation with a new incoming customer message. It ignores its own earlier replies and never touches a conversation that's already **Open**, **Snoozed**, or **Resolved**; that territory belongs to a human. When a burst of messages arrives quickly (a customer typing several lines in a row), the assistant waits a brief moment after the last one before deciding, so the whole burst gets one answer instead of several.

**Show:** Not something an agent switches on or off. Whether the assistant is active at all on a given inbox, and in what mode, is set by an administrator under **Knowledge → Inboxes** (see the Knowledge chapter).

**Walk through:**

1. Watch the conversation list: a conversation the assistant is still working on stays **Pending**; once it hands off, the conversation reopens (moves out of **Pending**) so a human can see it needs attention.
2. If the assistant hands off, it may first post a short acknowledgement message to the customer (an administrator-configured handoff message) before reopening the conversation — don't be surprised to see that message already sent when you open the conversation.
3. When a handoff conversation is reopened, it's often assigned to an agent automatically as part of the handoff, rather than staying unassigned.
4. Treat a conversation the assistant handed off the same way you would any other reopened conversation — read the thread, and reply or take whatever action the customer needs.

### Suggest mode vs. Auto mode  ·  5 min  ·  agent+

**Source:** `10-ai-behaviour.md` → `## Suggest mode vs. Auto mode`

**Say:** Every inbox where the assistant is active runs in one of two modes, chosen by an administrator: **Suggest mode**, where the assistant drafts a reply as a private note and reopens the conversation for a human to review and send; or **Auto mode**, where the assistant sends its reply straight to the customer and the conversation stays **Pending** while it continues handling the conversation on its own.

**Show:** Set per inbox under **Knowledge → Inboxes**, with a tenant-wide default mode under **Knowledge → Settings** that applies to any inbox without its own override (see the Knowledge chapter for both).

**Walk through:**

1. An administrator decides which inboxes should run in Suggest mode (every reply reviewed by a human first) and which can run in Auto mode (the assistant replies directly, useful for high-volume, low-risk questions).
2. Set or change the mode for a specific inbox under **Knowledge → Inboxes**; leave an inbox unset to inherit the tenant-wide default mode from **Knowledge → Settings**.
3. As an agent, you can tell which mode an inbox is running by what you see: a suggested draft arriving as a private note (Suggest mode) versus a reply already sent to the customer with the conversation still Pending (Auto mode).
4. In either mode, if the assistant can't confidently answer, it hands off to a human instead (see the section above) rather than guessing.

### Escalation labels & the escalation email  ·  6 min  ·  agent+

**Source:** `10-ai-behaviour.md` → `## Escalation labels & the escalation email`

**Say:** Applying the **escalate** label to a conversation on an **Email** inbox triggers an automatic two-part escalation email: a short acknowledgement sent to the customer, and a separate forward containing the case details sent to the responsible department PIC and/or dealer group. This only applies to Email-channel conversations — applying the label on a WhatsApp, web chatbot, or phone conversation doesn't send an email, since there's no email thread for it to join. **Label order matters**: the handler reads whatever department/dealer labels are already present the moment `escalate` is applied, so applying `escalate` before the department label means that leg silently doesn't fire for that trigger — apply the department label, then the dealer label if there is one, then `escalate`, in that order.

**Show:** Applied like any other label, from an open conversation's label control (see the Conversations chapter's Labels section).

**Walk through:**

1. Open the Email conversation you need to escalate.
2. Apply a department label first — `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, or `dept_technical`, all six of which now have a PIC configured and route correctly — then the relevant dealer label if one is involved (`dealer_komang_motor` and `dealer_caroline_motor` are both live dealer groups today) — see **Escalation Routing** in the Administration chapter for how these map to a specific PIC or dealer group. You may see a private note suggesting a department first — see the Conversations chapter's AI-suggested escalation department section.
3. Apply the **escalate** label last.
4. The acknowledgement email to the customer and the internal forward to the PIC/dealer group are sent automatically; there's nothing further to click.
5. If you only need the case attributed to a dealer for turnaround reporting (no escalation email needed, or the conversation isn't on an Email inbox), apply just the dealer label — the turnaround clock still starts.
6. Watch the conversation afterward for a dealer/PIC reply — it arrives as a private note, not a new task you have to go find (see the Conversations chapter's Escalation replies section).

### Lifecycle messages  ·  3 min  ·  agent+

**Source:** `10-ai-behaviour.md` → `## Lifecycle messages`

**Say:** The assistant sends a set sequence of automatic, customer-facing messages across a conversation's life, separate from anything it says while answering a question: an opening welcome/disclaimer message when a conversation starts (or, on an Email inbox, a simple acknowledgement of receipt instead), an idle-warning message if the customer goes quiet, a closing message if the conversation is then auto-closed for inactivity, a prompt asking whether the case is resolved, a satisfaction survey (worded differently depending on whether the AI or a human agent handled the conversation), a thank-you after the customer rates it, and a message letting the customer know a human agent is being assigned if the case isn't resolved yet.

**Show:** The wording of each message is set per assistant under **Knowledge → Settings** (see the Knowledge chapter's Messages section), and the timing — how long to wait before warning or closing an idle conversation — is set per inbox under **Administration → Inboxes** (see the Administration chapter), which can also override the wording for that inbox specifically.

**No single procedure to demo:** this section is structured as sub-scenarios rather than one set of steps. Deliver it from the handbook section itself.

**Say out loud:** **What is running on this tenant, as configured.** The idle warning, the automatic close and the "is your case resolved?" prompt are on. The opening welcome/disclaimer message is **off** — a new chat conversation begins with the assistant's answer, not a disclaimer. The assistant's own satisfaction surveys are **off**, both the AI-handled and the agent-handled variants. Ratings still reach the CSAT report, because the CRM's own native satisfaction survey is enabled on every inbox — that is a separate mechanism, configured per inbox rather than per assistant. On the Email inbox the acknowledgement of receipt is sent by the inbox's own greeting rather than by the platform, which means it goes to **every** new email thread — including a dealer replying to an escalation. Ask your administrator before telling a customer that any of these will reach them.

### Voice bot behaviour (the AI-answered part of a call)  ·  6 min  ·  agent+

**Source:** `10-ai-behaviour.md` → `## Voice bot behaviour (the AI-answered part of a call)`

**Say:** Inbound phone calls to Proton's support line are answered by the same AI assistant used on other channels, using a voice conversation rather than text: it can hold a natural back-and-forth, answer vehicle questions from the same knowledge base used on WhatsApp, and ask the caller to rate the call 1–5 at the end. There is no traditional press-1-for-sales phone menu — callers speak naturally and the assistant works out what they need.

**Show:** There is no separate phone/IVR configuration screen inside the CRM. What you see is the result each call leaves behind: a conversation in the Conversations view with the call's transcript as its messages, updating close to real time during the call on tenants configured for it, or appearing all at once at hangup otherwise.

**Walk through:**

1. When a customer calls in, the assistant answers and the call appears as a new conversation in the Conversations view, with the spoken exchange logged as a transcript.
2. Read that transcript conversation the same way you would a WhatsApp or web conversation, including after the call has ended.
3. The assistant is instructed to answer in English, Bahasa Melayu, or Chinese and switch languages mid-call to match the caller — but **reliable Bahasa Melayu on a live call is a known, unresolved issue** specific to this voice pipeline (the text channels don't have this problem). Don't promise a Bahasa-speaking caller the assistant will stay in Bahasa for the whole call; if they get stuck in English, apologize and hand the call to a human rather than calling it fixed.
4. At the end of the call, the caller is asked to rate the interaction 1–5; that rating feeds the CSAT report (see the Reports chapter) the same way a text-channel rating does.
5. **Treat everything beyond the greeting, KB answers, and rating survey as unconfirmed.** Those three were demonstrated live on a real call; live transcript streaming, call classification, call recording, and a real transfer to a human (see the Phone handoff behaviour section, below) are built and code-reviewed but have not yet been run against a real incoming call. If asked, describe them as "should work, unconfirmed," not as proven.

### Phone handoff behaviour (the human side)  ·  6 min  ·  agent+

**Source:** `10-ai-behaviour.md` → `## Phone handoff behaviour (the human side)`

**Say:** What happens when a caller asks for a person, and what's left behind in Chatwoot once a call ends. On tenants where a live transfer is turned on, the assistant tells the caller it's trying to connect them and dials a single support number — the same number for every reason a call gets transferred, including a roadside emergency. Where a live transfer isn't turned on (most tenants today), the assistant tells the caller a specialist will follow up and the call simply continues with the assistant.

**Show:** The same conversation the Voice bot section describes — there's no separate "Phone" inbox or settings screen. Whether a live transfer is even attempted on your tenant is an administrator-level setting, not something an agent switches on.

**Walk through:**

1. If a caller asks for a person and a transfer is attempted, watch for the conversation to reopen with a note recording the handoff — check while the transfer is still ringing, since that's the signal someone is being connected right now.
2. **Don't promise an after-hours caller — roadside emergency or otherwise — an automatic 24/7 transfer.** No such bypass exists; the same business-hours gate applies to every call. Log a roadside case in the RSA Incident Log chapter and follow up directly instead.
3. **If nobody answers the transfer,** the caller hears a short apology and the call ends — it does not return to the assistant, and the conversation is tagged so it's easy to find. **The apology promises a callback that nothing sends automatically** — only a human working that tagged conversation makes the callback happen. Treat an unanswered transfer as an action item still owed to the caller, not a closed loop.
4. The `escalate` label works no differently on a phone-originated conversation than on WhatsApp or the web chatbot: it doesn't notify anyone, since this conversation isn't on the Email inbox. Escalate to a dealer or PIC directly if the case needs one.
5. Once the call has ended, work the resulting conversation like any other — reply, resolve, and check the CSAT rating the same way as the Voice bot section describes.

## Module 11 — End-to-End Scenarios  ·  8 topics  ·  1 h 2 min

### Scenario 1: WhatsApp inquiry to resolution  ·  11 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 1: WhatsApp inquiry to resolution`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer messages Proton's WhatsApp number asking about the price and availability of a test drive for the e.MAS 7. Chatwoot creates a new conversation on the WhatsApp inbox, which is running in Suggest mode (see the AI Assistant Behaviour chapter's Suggest mode vs. Auto mode section).
2. The AI assistant drafts an answer grounded in the knowledge base and posts it as a private note, then reopens the conversation for a human (see the Conversations chapter's AI auto-draft section and the AI Assistant Behaviour chapter).
3. The on-duty agent opens the conversation, reads the suggested draft and its source citations, tweaks the wording slightly, and sends it as their own reply (see the Conversations chapter's Private notes and Suggest-a-reply sections).
4. The customer confirms they'd like to book the test drive; the agent arranges it and, once everything is confirmed, marks the conversation **Resolved** (see the Conversations chapter's Resolving, snoozing & transcripts section).
5. The customer receives the standard resolution prompt and satisfaction survey, and their 1–5 rating shows up later in the CSAT report (see the AI Assistant Behaviour chapter's Lifecycle messages section and the Reports chapter).

**Hands-on:** exercise `AG-10` — see `exercises.md`.

### Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report  ·  11 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer emails in about a recurring charging fault that wasn't fixed at their last service visit. The conversation lands on the Email inbox (see the Conversations chapter's Conversation inbox & views section).
2. The agent handling it decides the case needs the dealer's attention and applies the **`dept_aftersales`** label first, then the relevant dealer's label, then **escalate** last — in that order, since the handler only picks up whichever department/dealer labels are already on the conversation at the moment `escalate` is applied (see the Conversations chapter's Labels section).
3. The CRM automatically sends the customer a short acknowledgement email and forwards the case details to the dealer group's members by email, using the contacts set up in Escalation Routing; the dealer's turnaround clock starts at the same moment (see the AI Assistant Behaviour chapter's Escalation labels & the escalation email section and the Administration chapter's Escalation Routing section).
4. Two days later, the dealer emails back confirming the fault has been repaired. Within a couple of minutes, the agent sees a new private note on the **same** conversation starting `Reply from ` with the dealer's update, and directly beneath it a second note — `Suggested customer reply (draft — review before sending):` — with an AI-drafted update already written (see the Conversations chapter's Escalation replies section).
5. The agent reads the draft, adjusts a couple of words, sends it as their own reply to the customer, and marks the conversation **Resolved**.
6. During the weekly ops review, a supervisor opens the Weekly Report page and checks the Dealer Escalation Turnaround table to see how long that dealer took to close the case, alongside every other escalation from the same week (see the Reports chapter's Weekly Report and Dealer escalation turnaround sections).

**Hands-on:** exercise `AG-11` — see `exercises.md`.

### Scenario 7: A customer replies to their own acknowledgement email  ·  6 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 7: A customer replies to their own acknowledgement email`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer emails in about a delayed part; the agent escalates it with a department label, a dealer label, and **escalate**, and the customer receives the short acknowledgement email (see Scenario 2, above).
2. Two days later, still waiting, the customer hits **Reply** on that same acknowledgement email and asks for an update, without changing the subject line.
3. Chatwoot has no way to thread that reply onto the original case on its own, so it briefly appears as a brand-new conversation on the Email inbox — the agent doesn't need to do anything with this one; the CRM resolves it automatically in the background.
4. **Correction from the previous edition:** on the **original** conversation, the customer's message does **not** appear as a normal incoming message. Chatwoot only accepts a synthetic incoming message on an Api-channel inbox, and this always runs on the Email channel, so that attempt is always rejected. What the agent actually sees is a private note prefixed `Customer's own reply (from <email>, could not be posted inline -- see conversation <id>):`, followed by the customer's own words — and the conversation still reopens by itself, exactly as before (see the Conversations chapter's Escalation replies section).
5. **What to actually do:** treat that private note as the customer's message — it is, verbatim, just delivered as a note rather than an inline bubble — and reply publicly to it the same way as any other reopened case. Don't wait for it to turn into an ordinary message; it won't. Nothing about the escalation itself (the ack, the PIC email, the dealer forward) fires again just because the customer replied.

### Scenario 12: A WhatsApp case looks escalated, but nothing was sent  ·  6 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 12: A WhatsApp case looks escalated, but nothing was sent`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer messages about a repeated delivery delay, and the agent handling it decides the case needs the dealer's attention. Following the same steps as an Email escalation (see Scenario 2, above), the agent adds a `dealer_<slug>` label, then **escalate**, to the WhatsApp conversation.
2. Nothing happens. **Adding the `escalate` label to a WhatsApp conversation by hand does not send an email or a WhatsApp alert to anyone** — the automatic escalation flow only fires on an Email-channel conversation (see the Integration Overview chapter's Email section); WhatsApp conversations don't qualify, no matter what labels are on them.
3. The `dealer_<slug>` label does still do one thing: it stamps the conversation for that dealer's turnaround reporting, the same as it would on any channel. That part works — it's a reporting timestamp, not a notification, and it's easy to mistake one for the other.
4. The only way a PIC gets notified automatically on WhatsApp is if the AI assistant itself judges the customer's message to be a genuine complaint — its own decision, not something an agent can trigger by adding a label (see the AI Assistant Behaviour chapter).
5. **What the agent actually does:** call or email the dealer directly, outside the CRM, and tell the customer only that the right team is being looped in — not that "I've escalated this," since the label alone didn't do anything on this channel.

### Scenario 13: The same limitation on a web chat case  ·  5 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 13: The same limitation on a web chat case`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A website visitor asks about a warranty issue that turns out to need the dealer's attention. The agent applies a `dealer_<slug>` label, then **escalate**, to the Web Chatbot conversation — exactly the same steps as Scenario 12, above.
2. The result is identical: no email fires, because the conversation is on the Web Chatbot inbox, not the Email inbox. The dealer turnaround stamp still applies from the `dealer_<slug>` label; the notification still doesn't happen.
3. Since the visitor may not have given a phone number or email through the widget, the agent may have no address to escalate to inside the CRM even if the flow did fire here — reinforcing why this has to be handled outside the CRM today, the same as WhatsApp.
4. The agent contacts the dealer directly and tells the visitor only that the right team has been looped in, the same wording as Scenario 12.

### Scenario 14: An after-hours breakdown call, and an unanswered transfer  ·  6 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 14: An after-hours breakdown call, and an unanswered transfer`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer calls late one evening reporting a breakdown and asks the assistant to connect them to a person right away.
2. Because every transfer attempt — roadside or otherwise — is gated by the support inbox's normal business hours with no exception, the transfer isn't attempted at all; the call continues with the assistant, which tells the caller a specialist will follow up (see the AI Assistant Behaviour chapter's Phone handoff behaviour section). This is a real correction from the 08-04 guide, which described roadside calls as bypassing business hours — that bypass was never built.
3. The next morning, an agent opens the resulting conversation, reads the transcript, and logs the incident in the RSA Incident Log chapter with the vehicle number and cause, then calls the customer back directly — not assuming any overnight transfer or callback already happened.
4. Later that week, a different caller reaches an agent transfer during business hours, but nobody picks up at the other end. The caller hears a short apology and the call ends without returning to the assistant; the conversation is tagged so agents can find it. The agent on duty sees the tag, calls the customer back personally, since the apology's promised callback doesn't send itself.
5. Neither of these cases can be escalated to a dealer with just the `escalate` label either — a phone conversation isn't on the Email inbox, the same limitation as Scenarios 12 and 13.

### Scenario 15: Categorising a case through all five taxonomy dropdowns  ·  12 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 15: Categorising a case through all five taxonomy dropdowns`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer emails complaining that their new e.MAS 5's delivery has no estimated date. The agent opens the conversation's custom attributes panel (see the Cases chapter's Case categorisation section).
2. They set **Case Type** to `Complaint` — one of three values, and independent of everything else in the panel.
3. They set **Case Category** to `Sales`, one of eight divisions.
4. **Case Subcategory** now only offers Sales' own Level-1 values, each prefixed `Sales: ` — the agent picks `Sales: Delivery`.
5. **Case Detail** now only offers Delivery's own Level-2 values, each prefixed `Sales: Delivery: ` — the agent picks `Sales: Delivery: No Estimated Time Delivery`. Nobody pre-filled this: Case Detail is never set by the AI, only ever picked by hand.
6. They set **Vehicle Model** to `e.MAS 5` — again independent, doesn't touch the other four fields.
7. Later, the agent realizes this is actually a Charging complaint, not Sales, and changes **Case Category** to `Charging`. **Case Subcategory** and **Case Detail** both clear immediately, since `Sales: Delivery` and `Sales: Delivery: No Estimated Time Delivery` no longer match the new division — the agent reselects both from Charging's narrowed lists. **Case Type** and **Vehicle Model** are untouched by this change.

**Hands-on:** exercise `AG-12` — see `exercises.md`.

### Scenario 16: An AI-suggested escalation department — accepted, and ignored  ·  5 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 16: An AI-suggested escalation department — accepted, and ignored`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer emails Proton asking about financing options and a trade-in value before committing to an order. No department label is on the conversation yet.
2. Within moments, a private note appears naming **pre_sales** as the AI-suggested escalation department, explicitly stating it's a suggestion only and that no label has been applied (see the Conversations chapter's AI-suggested escalation department section).
3. The agent agrees with the suggestion, adds `dept_pre_sales` from the label control, then applies `escalate` last — the case routes to the Pre-Sales PIC exactly as if the agent had picked the department unprompted.
4. On a second, unrelated case — a customer asking a routine delivery-status question — the same kind of note suggests `sales`. The agent judges this one is actually an After-Sales matter instead, so they simply don't add `dept_sales`; they apply `dept_aftersales` themselves and escalate with that. The suggestion is never applied automatically either way, so ignoring it costs nothing and requires no extra click.

## Module 12 — Channel Playbooks  ·  10 topics  ·  33 min

### How the channels map to the CRM  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## How the channels map to the CRM`

**Say:** Social channels (Facebook, Instagram) are not connected and are not covered here.

### The toolkit that is identical on every channel  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## The toolkit that is identical on every channel`

**Say:** The four standalone pages in that list — Audit Log, Customer 360, Escalation Routing and SLA Policies — appear only if your role carries the matching permission. Not seeing one is a permissions question for your administrator, not a fault.

### WhatsApp  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## WhatsApp`

**Say:** The most complete channel: the assistant answers, hands off, and closes cases on its own.

**No single procedure to demo:** this section is structured as sub-scenarios rather than one set of steps. Deliver it from the handbook section itself.

**Say out loud:** Your administrator can switch escalation on for every channel. Until they have, treat the label as inert here.

### Web chatbot  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## Web chatbot`

**Say:** The same assistant and the same knowledge base as WhatsApp, so the shape of the work is identical. Two differences matter in practice.

**No single procedure to demo:** this section is structured as sub-scenarios rather than one set of steps. Deliver it from the handbook section itself.

### Voice bot — the AI-answered part of a call  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## Voice bot — the AI-answered part of a call`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**No single procedure to demo:** this section is structured as sub-scenarios rather than one set of steps. Deliver it from the handbook section itself.

**Say out loud:** **Say "should work, not yet confirmed" about anything in this section beyond the basics.** The greeting, knowledge-grounded answers, the satisfaction rating and the conversation created at hangup were demonstrated on a real call. Everything beyond that — live transcript, automatic classification, call recording, a real transfer to a person — is built and switched off, and has never been exercised against a real call. Do not present any of it as demonstrated.

### Phone — the human side of the same call  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## Phone — the human side of the same call`

**Say:** This continues the call the voice bot answered. There is no separate "what the customer does": this is the conversation the CRM is left holding, and what you do with it.

**No single procedure to demo:** this section is structured as sub-scenarios rather than one set of steps. Deliver it from the handbook section itself.

### Email  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## Email`

**Say:** The channel with the most automation behind it, and the only one where the `escalate` label actually sends something.

**No single procedure to demo:** this section is structured as sub-scenarios rather than one set of steps. Deliver it from the handbook section itself.

### One customer, four touchpoints  ·  6 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## One customer, four touchpoints`

**Say:** **The biggest cross-channel gap:** there is no automatic link between a customer and all their prior cases. **Previous Conversations** and Customer 360's search are the whole of what exists, and both depend on the customer being recognisable — the same email, the same phone number — each time.

**Walk the scenario through, in order:**

1. **WhatsApp** — the assistant answers and the case closes.
2. **Web chat** — the assistant here has no awareness the WhatsApp conversation ever happened. Each session is grounded only in what is said in that conversation. It hands off; an agent resolves it.
3. **The call** — same again: the assistant treats it as a fresh enquiry.
4. **As the agent working the transcript** — open the contact record and use **Previous Conversations** to see the WhatsApp and web chat history before replying. **Customer 360** will pull the same history from a phone number in one search.
5. **Escalating to the dealer** — the call's conversation is not on the Email inbox, so it cannot escalate. Open an Email conversation with the customer and run the Email playbook's Scenario C there.

**Say out loud:** A customer messages WhatsApp about a delivery delay and gets a routine answer. A week later they open the website chat with a follow-up the assistant cannot resolve, and an agent takes over. Two weeks after that they call in for a status update. The agent who picks up the transcript decides the dealer needs to see it.

### Quick reference  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## Quick reference`

**Say:** ---

### Known limitations, and what to tell the customer  ·  3 min  ·  agent+

**Source:** `12-channel-playbooks.md` → `## Known limitations, and what to tell the customer`

**Say:** _(reference table of 12 rows — hand it out rather than present it)_

## Module 14 — Glossary  ·  1 topics  ·  3 min

### Terms  ·  3 min  ·  agent+

**Source:** `14-glossary.md` → `## Terms`

**Say:** _(reference table of 25 rows — hand it out rather than present it)_

## What this curriculum cannot teach yet

1 topic this role would be expected to cover is absent from the handbook source, or present only as the tenant's current behaviour. They are listed here rather than written from a specification, because a curriculum that teaches an unbuilt page loses its cohort on day one.

| Topic | Why it is not here | Unblocked by |
|---|---|---|
| Hands-on voice and phone practice | The voice and phone topics are taught from the handbook, but no real Twilio call has ever been placed (risk R10) and every `PHONE_*` capability switch is off on the tenant. The channel topics are therefore presentation-only: there is no exercise for them, and inventing one would be a lab that cannot run. | R10 · one real call, then a sandbox phone number |

See `../delivery-plan.md` for the session schedule, prerequisites, the sandbox reset between cohorts and the refresher cadence.
