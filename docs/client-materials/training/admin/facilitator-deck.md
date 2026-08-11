<!-- GENERATED FILE — do not edit by hand.
     Source: docs/client-materials/feature-guide-src-v3/ (the operator handbook)
     Regenerate: python3 docs/client-materials/build_crm_feature_guide.py --curricula
     Drift check: python3 docs/client-materials/build_crm_feature_guide.py --check
-->

# Administrator curriculum — facilitator deck

> **Generated from the operator handbook — do not edit.** Every line below is rendered from `feature-guide-src-v3/`; an edit here is overwritten by the next run. To change what a cohort is taught, change the handbook section this points at, or its `<!-- TRAINING: ... -->` marker, and regenerate.

**Audience:** Administrator · **Topics:** 108 of 108 handbook sections · **Hands-on exercises:** 32

**Rule-derived length:** 12 h 29 min. **Design target (spec §3.1):** 4 h. **Difference: +8 h 29 min.**

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

**Hands-on:** exercise `AD-01` — see `exercises.md`.

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

**Hands-on:** exercise `AD-02` — see `exercises.md`.

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

**Hands-on:** exercise `AD-03` — see `exercises.md`.

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

**Hands-on:** exercise `AD-04` — see `exercises.md`.

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

**Hands-on:** exercise `AD-05` — see `exercises.md`.

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

**Hands-on:** exercise `AD-06` — see `exercises.md`.

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

**Hands-on:** exercise `AD-07` — see `exercises.md`.

## Module 03 — Contacts  ·  4 topics  ·  24 min

### Contacts list & search  ·  10 min  ·  agent+

**Source:** `03-contacts.md` → `## Contacts list & search`

**Say:** The Contacts area lists everyone who has ever messaged in — across WhatsApp, email, and phone/IVR — as a single directory of customers, separate from the Conversations view's per-channel threads.

**Show:** **Contacts** in the main sidebar.

**Walk through:**

1. Open **Contacts** from the sidebar to see the full customer list.
2. Use the search box to find a customer by name, phone number, or email address.
3. Use the available filters to narrow the list (for example, by the channel a customer last used).
4. Click a customer's row to open their contact profile.

**Hands-on:** exercise `AD-08` — see `exercises.md`.

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

### Customer 360  ·  4 min  ·  supervisor+

**Source:** `03-contacts.md` → `## Customer 360`

**Say:** Customer 360 is a single search box that looks a customer up by phone number or vehicle number and brings together, on one page, what the CRM already knows about them: their matching contact record, every conversation they've had across channels, any roadside-assistance (RSA) incidents tied to their vehicle, and — where the DMS/TSP integration is configured (see the Administration chapter's Integrations section) — a vehicle and service-history block. It's read-only: nothing here can be created, edited, or deleted; contact details are still edited from the contact's own profile, and RSA incidents from the RSA Incident Log chapter's page.

**Show:** **Customer 360** in the main sidebar. This page is only visible to administrators who have also been granted the Customer 360 permission (see the Administration chapter's Roles & Permissions section) — an administrator without that permission won't see it in their sidebar.

**Walk through:**

1. Open **Customer 360** from the sidebar.
2. Enter a phone number or a vehicle number in the search box (at least two characters).

## Module 04 — Knowledge  ·  8 topics  ·  1 h 10 min

### FAQs  ·  11 min  ·  admin+

**Source:** `04-knowledge.md` → `## FAQs`

**Say:** FAQs is the live, editable question-and-answer knowledge base the AI assistant is grounded on. Each entry pairs a customer question with the exact answer to give, plus optional keywords and tags to help matching. Unlike the Documents corpus (see the next section), FAQ entries are edited directly in the CRM and take effect immediately — there is no separate publishing step.

**Show:** In the left-hand navigation, open **Knowledge** and select **FAQs**.

**Walk through:**

1. Open **Knowledge → FAQs**. The page lists every entry with its question, answer, keywords, tags, and whether it is active.
2. To add an entry, click **+ New entry**, fill in **Question** and **Answer** (both required), and optionally **Keywords** and **Tags** as comma-separated lists. Leave **Active** checked so the entry is usable for grounding, then save.
3. To change an entry, click **Edit** on its row, update the fields, and save.
4. To remove an entry, click **Delete** on its row and confirm.
5. Use the filter box at the top to search by question, keyword, or tag across all entries.
6. To add many entries at once, click **Bulk upload (CSV)**, choose a `.csv` file, and wait for the confirmation message. It reports how many entries were created and, if any rows were skipped, which row numbers and why.

**Say out loud:** Entries created through the bulk upload are always saved as active — if you need an imported entry to start out inactive, edit it afterwards and uncheck **Active**.

**Hands-on:** exercise `AD-09` — see `exercises.md`.

### Documents  ·  10 min  ·  admin+

**Source:** `04-knowledge.md` → `## Documents`

**Say:** Documents covers the larger, bulk knowledge corpus that also grounds the assistant's answers, alongside FAQs. It has two related views in the left-hand navigation:

**Show:** In the left-hand navigation, open **Knowledge** and select **Documents** for the read-only corpus listing, or **Uploads** to add new material.

**Walk through:**

1. Open **Knowledge → Documents**. Each row shows a document's title, a link to its source (if available), and a short snippet.
2. Use the filter box to search by title, link, or snippet.
3. Click **Refresh** to reload the list after new material has finished indexing elsewhere.

**Hands-on:** exercise `AD-10` — see `exercises.md`.

### Assistants  ·  6 min  ·  admin+

**Source:** `04-knowledge.md` → `## Assistants`

**Say:** An Assistant is a named AI persona: its own name, description, and product context, with its own instructions, temperature, guardrails, and tool access, configured on the Settings and Tools pages. Most tenants only need one, and a **Default Assistant** always exists so nothing has to be configured before the AI works. Larger tenants can create additional assistants — for example, one per product line or department — and pick which one answers on which inbox (see Inboxes).

**Show:** In the left-hand navigation, open **Knowledge** and select **Assistants**.

**Walk through:**

1. Open **Knowledge → Assistants** to see every assistant, with a **default** badge on the one used when no other assistant is assigned.
2. To create one, click **+ New assistant**, enter a **Name** (required), optional **Description** and **Product name**, and save.
3. To change one, click **Edit** on its row, update the fields, and save.
4. To remove one, click **Delete** on its row and confirm — the default assistant cannot be deleted; make another assistant the default first if you need to retire it.
5. Once an assistant exists, pick it from the **Assistant** selector shown at the top of the Scenarios, Playground, Tools, and Settings pages to configure or test that specific persona.

### Scenarios  ·  6 min  ·  admin+

**Source:** `04-knowledge.md` → `## Scenarios`

**Say:** A Scenario is a named playbook — a block of instructions, scoped to one assistant, that gets added to that assistant's behaviour when the scenario is turned on. Use it to give the assistant extra guidance for a specific situation (for example, how to handle a recall notice or a warranty claim) without rewriting its whole persona. A scenario can also be tied to specific tools so the assistant knows which ones to reach for in that situation.

**Show:** In the left-hand navigation, open **Knowledge** and select **Scenarios**.

**Walk through:**

1. Open **Knowledge → Scenarios** and pick the assistant you want to manage scenarios for, using the **Assistant** selector at the top.
2. To create one, click **+ New scenario**, enter a **Title** (required), an optional short **Description**, and the **Instruction** text the assistant should follow when this scenario applies.
3. Optionally tick which **Tools** the assistant should have available for this scenario, from the built-in and custom tools already registered under Tools.
4. Leave **Enabled** checked so the scenario is active, then save.
5. To turn a scenario on or off without editing it, use the toggle switch in its row.
6. To change or remove a scenario, click **Edit** or **Delete** on its row.

### Playground  ·  11 min  ·  admin+

**Source:** `04-knowledge.md` → `## Playground`

**Say:** Playground is a sandbox for trying out an assistant outside any real conversation. It behaves like the Ask Copilot panel in a conversation — you type a question, the assistant answers using its configured knowledge and tools, and the reply shows which tools it used and any sources it drew on — but nothing here reaches a customer.

**Show:** In the left-hand navigation, open **Knowledge** and select **Playground**.

**Walk through:**

1. Open **Knowledge → Playground** and pick the assistant to test with the **Assistant** selector at the top. Switching assistants starts a fresh conversation.
2. Type a question in the box at the bottom and press **Enter** (use **Shift+Enter** for a new line) or click **Send**.
3. Read the reply. If the assistant used a tool, a **Looked at:** line names it; if it cited sources, they appear as clickable links underneath.
4. Continue the back-and-forth as needed — each new message includes the full thread so far.
5. Click **Reset** to clear the conversation and start over.
6. Optionally expand **Advanced** to supply a real conversation ID, which lets you exercise tools that look up customer or ticket context as if you were inside that conversation.

**Hands-on:** exercise `AD-11` — see `exercises.md`.

### Tools  ·  9 min  ·  admin+

**Source:** `04-knowledge.md` → `## Tools`

**Say:** Tools are the actions an assistant is allowed to take beyond answering from text — looking things up or calling out to another system. There are two kinds:

**Show:** In the left-hand navigation, open **Knowledge** and select **Tools**.

**Walk through:**

1. Open **Knowledge → Tools**. The **Built-in tools** table lists every built-in tool with an editable description and an **Enabled** checkbox; click **Save** on a row after changing it.
2. To add a custom tool, click **+ New custom tool** in the **Custom tools** section and fill in:
3. **Title** and **Description** (shown to the assistant).
4. **Endpoint URL** — must start with `https://`.
5. **HTTP method** — GET or POST.
6. **Auth type** — none, bearer token, basic (username/password), or an API key header; the corresponding secret fields appear once you pick one, and are never shown again after saving (leave them blank on an edit to keep the existing secret).
7. Optionally a **param_schema** (a JSON description of the tool's inputs), a **request template**, and a **response template**.
8. **Enabled**, so the tool is available to be assigned to an assistant.
9. Save. Custom tools are capped at a fixed number per tenant, shown as a count next to the section heading.
10. To change or remove a custom tool, click **Edit** or **Delete** on its row.
11. Scroll to **Per-assistant enablement**, pick an assistant with the **Assistant** selector, tick which built-in and custom tools that assistant may call, and click **Save enablement**.

### Inboxes (assignment)  ·  5 min  ·  admin+

**Source:** `04-knowledge.md` → `## Inboxes (assignment)`

**Say:** The Inboxes page controls which assistant answers for each Chatwoot inbox, and in what mode. Every inbox that is not explicitly assigned here falls back to the tenant's default assistant and default mode, shown with a **default** badge; an inbox with its own assignment shows an **override** badge instead.

**Show:** In the left-hand navigation, open **Knowledge** and select **Inboxes**.

**Walk through:**

1. Open **Knowledge → Inboxes** to see every Chatwoot inbox with its channel type, currently assigned assistant, mode, and source badge.
2. To assign an assistant to an inbox, use the **Assistant** dropdown on its row and pick one — choosing **— default —** clears the override and goes back to inheriting the tenant default.
3. To change how the assistant behaves on that inbox, use the **Mode** dropdown: **Off** (the assistant does not answer on this inbox), **Suggest** (it drafts privately for a human to review and send), or **Auto** (it can reply directly).
4. Changes save automatically as soon as you pick a new value — there is no separate save button, and the row's badge switches to **override** once you have set anything explicitly.

### Settings (persona, language, lifecycle messages, guardrails)  ·  12 min  ·  admin+

**Source:** `04-knowledge.md` → `## Settings (persona, language, lifecycle messages, guardrails)`

**Say:** Settings is where an assistant's persona and tone are configured, along with a smaller set of tenant-wide operational knobs. It has two parts: an **Assistant persona** panel, scoped to whichever assistant is selected at the top, and a **Tenant settings** panel that applies across the whole workspace. Leaving any field empty keeps today's default behaviour — nothing here needs to be filled in for the assistant to keep working as it already does.

**Show:** In the left-hand navigation, open **Knowledge** and select **Settings**.

**Walk through:**

1. Open **Knowledge → Settings** and pick an assistant with the **Assistant** selector at the top to edit its persona.
2. Under **Basic**, set the assistant's **Name**, **Description**, **Product name**, and **Language**. The assistant always mirrors the language the customer actually writes in — this field never overrides that. It only acts as a tie-breaker preference for when the customer's language is unclear; leave it empty to let the assistant fall back to whatever language it judges best in that case, or set it (for example, to Bahasa Melayu) to tell it which language to prefer as that fallback.
3. Under **System**, write **System instructions** describing who the assistant is and how it should behave — this is the core of its persona. Adjust **Temperature** from precise (0) to creative (1); leaving it as-is keeps the current balance.
4. Under **Guardrails**, add short rules the assistant must never break (for example, "never quote a price that is not in the price list"). These are enforced on top of the system instructions.
5. Under **Response guidelines**, add style and tone preferences (for example, "always reply in short paragraphs" or "always end with a follow-up question").
6. Under **Messages**, fill in the wording the assistant sends at key moments of a conversation: a **Welcome message** when it starts and a **Handoff message** when it hands off to a human agent — plus five more lifecycle messages: an **Idle warning message** (sent after a period of inactivity), an **Idle close message** (sent if the conversation is then closed for inactivity), a **Resolution prompt message** (asking whether the issue is resolved), and CSAT survey prompts split by who handled the chat — a **Survey AI message** and a **Survey agent message** — plus a **Thanks message** after a rating and an **Assign agent message** when a human is assigned. Leaving any of these blank keeps the platform's built-in default wording for that moment. A **Resolution message** field is also available on this page, but it is saved for future use only — nothing in the current build sends it to a customer.
7. Under **Features**, toggle whether this assistant uses knowledge-base/FAQ grounding, conversation memory context, source citations in its answers, and contact-attribute context.
8. Click **Save assistant** to apply the persona changes.
9. Below the persona panel, **Tenant settings** lets an administrator override a fixed set of workspace-wide values, each shown with whether it is currently on the platform's default (**env**) or has been overridden (**override**), and a **Reset** to return it to default individually:
10. **Assist Gemini model** — which model Suggest-a-reply uses.
11. **Copilot Gemini model** — which model the Ask Copilot panel uses.
12. **Copilot max tool iterations** — how many tool calls Copilot may make while answering a single question.
13. **AI assist enabled**, **Copilot enabled**, **AI drafts enabled** — tenant-wide toggles for Suggest-a-reply, the Ask Copilot panel, and AI auto-drafted replies respectively.
14. **Default mode** — the fallback Suggest/Auto mode used for any inbox that does not have its own explicit mode set under Inboxes.
15. **Debounce seconds** — how long the AI waits after a customer's message before answering, so a quick burst of messages is answered once instead of many times.
16. **Inbound auto-acknowledgement template** — the message sent the first time a new email thread arrives, when the inbound auto-acknowledgement is turned on for the tenant.
17. **Escalation acknowledgement template** — the message sent to the customer as part of the two-thread escalation email (see the AI Assistant Behaviour chapter's Escalation labels & the escalation email section).

## Module 05 — Cases  ·  5 topics  ·  38 min

### Case list  ·  11 min  ·  supervisor+

**Source:** `05-cases.md` → `## Case list`

**Say:** The Cases list is a single filterable table that shows every conversation as a "case" — Division, Concern, Purchased From, Escalated To, Agent, Car Plate, Aging (in days), and Status — so a supervisor can scan the whole book of work without opening conversations one at a time. It doesn't add a separate case record: it reads the same conversations shown in the Conversations chapter and displays their category, contact, assignment, and status information as table columns. Conversations that predate this vocabulary, or were never categorized, simply show a dash in the columns that don't apply — an unassigned case shows a dash in the Agent column the same way.

**Show:** **Cases** in the main sidebar. This page requires the same permission as Customer 360 (see the Contacts chapter) — an administrator who hasn't been granted that permission won't see it.

**Walk through:**

1. Open **Cases** from the sidebar.
2. Use the Division, Case type, Status, Channel, and Dealer filters at the top to narrow the list, or use **Reset filters** to clear them.
3. Read the table: Case ID, Division, Concern, Purchased From, Escalated To, Agent, Car Plate, Aging (days), and Status. **Agent** shows the name of whoever the underlying conversation is assigned to, or a dash if it's unassigned.
4. Click a Case ID to open the underlying conversation.
5. If a banner appears saying the list is showing only the first N of a larger total, the list has reached its display limit — the filters and totals shown no longer reflect every case in the account.

**Hands-on:** exercise `AD-12` — see `exercises.md`.

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

**Hands-on:** exercise `AD-13` — see `exercises.md`.

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

## Module 06 — RSA Incident Log  ·  3 topics  ·  22 min

### Logging an RSA incident  ·  11 min  ·  supervisor+

**Source:** `06-rsa.md` → `## Logging an RSA incident`

**Say:** The RSA Incident Log is a standalone page for recording roadside-assistance (RSA) incidents — breakdowns, accidents, and similar callouts — as they're reported. Each incident is its own record with the vehicle, the reported cause, and where and when it happened. It's manual data entry: logging an incident here doesn't dispatch a tow truck or notify anyone by itself.

**Show:** **RSA Incident Log** in the main sidebar. In the current release this page shares its visibility with the SLA Policies page — an administrator needs the same permission that controls SLA Policies (see the Administration chapter) to see it.

**Walk through:**

1. Open **RSA Incident Log** from the sidebar.
2. Review the **Cases by cause** and **Cases by dealer** summary at the top of the page for a running total of incidents logged so far.
3. Under **Log an incident**, fill in **Incident date**, **Vehicle no.**, and **Cause** (all required).
4. Fill in whichever of the optional fields you already know: Vehicle model, Purchased from, Breakdown location, Arrived location, Customer called-in time, Towing assigned time, Time arrived breakdown area, Time arrived outlet, Total km, Late reason, and Remarks.
5. Click **Log incident** to save it. It appears in the incidents table below, and the cause/dealer summary at the top updates immediately.

**Hands-on:** exercise `AD-14` — see `exercises.md`.

### Incident statuses & updates  ·  6 min  ·  supervisor+

**Source:** `06-rsa.md` → `## Incident statuses & updates`

**Say:** Rather than a single status label, an incident's progress is tracked through the timestamp fields captured when it was logged — when the customer called in, when towing was assigned, when the tow arrived at the breakdown location, and when the vehicle arrived at the outlet — plus free-text Late reason and Remarks fields for anything that needs explaining. As new information comes in, staff update the same incident record rather than creating a new one.

**Show:** The incidents table at the bottom of the RSA Incident Log page; each row has **Edit** and **Delete** actions.

**Walk through:**

1. Find the incident in the table and click **Edit**.
2. Fill in each stage's field as it happens — for example, Towing assigned time once a tow truck is dispatched, then Time arrived breakdown area and Time arrived outlet as the vehicle progresses.
3. Add Total km and a Late reason if relevant.
4. Click **Save** to update the record, or **Cancel** to discard the changes.
5. Use **Delete** to remove a record entered in error — this cannot be undone.

### RSA in Customer 360 & reports  ·  5 min  ·  supervisor+

**Source:** `06-rsa.md` → `## RSA in Customer 360 & reports`

**Say:** Incidents logged here also surface elsewhere: an operator searching Customer 360 by a vehicle number sees any matching incidents alongside that customer's contact and conversations, and the same incident data feeds Proton's wider reporting — the Cases by cause/Cases by dealer summary on this page is a lightweight version of that same reporting.

**Show:** The RSA incidents table inside a Customer 360 lookup (see the Contacts chapter); the Cases by cause/Cases by dealer summary on this page; and the Departments & PIC and Case Lifecycle reports (see the Reports chapter).

**Walk through:**

1. Go to **Customer 360** and search by the vehicle's number.
2. Review the **RSA incidents** table in the results — it lists the same fields as this page's incident table.
3. Alternatively, stay on the RSA Incident Log page and use the Cases by cause/Cases by dealer summary for a running total without doing a per-vehicle lookup.

## Module 07 — Reports  ·  7 topics  ·  55 min

### Standard reports (Overview, Conversation, CSAT, Agent, Label, Inbox, Bot)  ·  6 min  ·  supervisor+

**Source:** `07-reports.md` → `## Standard reports (Overview, Conversation, CSAT, Agent, Label, Inbox, Bot)`

**Say:** Chatwoot's built-in reporting pages — Overview, Conversation, CSAT, Agent, Label, Inbox, and Bot — give numbers on conversation volume, response and resolution times, customer satisfaction, and per-agent, per-label, and per-inbox performance. On top of a few of these native pages, Proton adds its own extra section that pulls **cross-channel** numbers (every channel combined, not just one inbox at a time) from the CRM's reporting warehouse:

**Show:** **Reports** in the left sidebar.  The Proton sections above appear at the bottom of their respective native report page — no extra navigation is needed to see them.

**Walk through:**

1. Open **Reports** from the sidebar.
2. Pick the report tab you need (Overview, Conversation, CSAT, Agent, Label, Inbox, or Bot).
3. Set the date range and any filters the page offers (agent, inbox, label).
4. On the Agent, Bot, or CSAT report, scroll to the bottom to see the added Proton section — it loads independently of the filters above it.
5. Use the native download/export option, where the page offers one, to save the numbers for offline reporting.

### Anomaly report  ·  11 min  ·  supervisor+

**Source:** `07-reports.md` → `## Anomaly report`

**Say:** A page that flags channels whose recent conversation volume looks unusual — spiking or dropping well beyond that channel's own recent baseline — so an operator can catch a possible outage, campaign side-effect, or data problem early instead of noticing it days later in a monthly total.

**Show:** **Reports → Anomaly** in the left sidebar.

**Walk through:**

1. Open **Reports → Anomaly**.
2. Check the **Flagged Channels** count at the top — this is how many channels currently show anomalous volume.
3. Read the table below it: each row is a channel with its current volume, its normal baseline (mean and standard deviation), and a deviation score.
4. Treat a deviation badge in the yellow or red range as worth investigating — it means the channel's current volume sits well outside its usual pattern.
5. If a channel is flagged, cross-check the relevant Inbox report or ask the on-duty agents whether something changed (a broken integration, a marketing blast, a public holiday).

**Hands-on:** exercise `AD-15` — see `exercises.md`.

### Departments & PIC report  ·  6 min  ·  supervisor+

**Source:** `07-reports.md` → `## Departments & PIC report`

**Say:** A page that breaks conversation volume and performance down by department and by the person-in-charge (PIC) handling each case, plus how often resolved cases get reopened, and how case categories line up against vehicle models.

**Show:** **Reports → Departments & PIC** in the left sidebar.

**Walk through:**

1. Open **Reports → Departments & PIC**.
2. Review the **Department / PIC Performance** table — cases, average first response, average resolution, and resolution rate per department/PIC pair.
3. Check **Top Departments by Cases** for a quick visual of where volume concentrates.
4. Review **Reopen / Case Reopen Rate (CRR)** to see which dealer/department/PIC combinations have the most cases reopened after resolution — a high reopen rate can point to a quality problem, not just a busy team.
5. Use **Category × Vehicle Model** to see which complaint or inquiry categories cluster around specific vehicle models.

### Case Lifecycle report  ·  6 min  ·  supervisor+

**Source:** `07-reports.md` → `## Case Lifecycle report`

**Say:** A page showing how long cases take to resolve and how cases move through their statuses over time, plus a running list of recent cases and, further down the same page, a work-in-progress / case-aging view of cases still open.

**Show:** **Reports → Case Lifecycle** in the left sidebar.

**Walk through:**

1. Open **Reports → Case Lifecycle**.
2. Review **Resolution-Time Distribution** to see how cases bucket across resolution-speed bands, from under 30 minutes up to more than 24 hours.
3. Review **Case State Trend** to see how the mix of case statuses has moved month over month.
4. Scroll to **Recent Cases** for a per-conversation table (channel, department, dealer, status, resolution time) for the most recently created cases.
5. Scroll further to **Work-in-Progress / Case Aging** for currently open or pending cases sorted by how many days old they are, bucketed so aging cases are easy to spot.

### Weekly Report  ·  11 min  ·  supervisor+

**Source:** `07-reports.md` → `## Weekly Report`

**Say:** A single page that reconciles the numbers in Proton's own weekly reporting routine — case volume, case status trend, department/PIC performance, call-centre and SLA performance, dealer escalation turnaround, SLA compliance, work-in-progress aging, and a per-case detail table — all against a chosen 7-day window, so a weekly reporting habit doesn't require collating several report pages by hand.

**Show:** **Reports → Weekly Report** in the left sidebar.

**Walk through:**

1. Open **Reports → Weekly Report**.
2. Use the week picker at the top to choose the window you're reporting on. It defaults to the current Monday–Sunday week, but its start date can be set to any day — useful if your own weekly routine runs on a different 7-day cycle (for example Friday-to-Thursday).
3. Read **Case Volume** for total cases in the window, the week-over-week change, and a breakdown by channel and by case type/division.
4. Read **Case Status Trend** for how cases split across statuses within the window.
5. Scroll through **Inquiry / Complaint / Feedback Detail — Departments & PIC**, **Call Centre & SLA Performance**, **Work-in-Progress / Case Aging**, and **Dealer Escalation Turnaround** — each section carries a small badge saying whether it's scoped to "This week" or "All time", so it's clear which numbers are windowed and which are running totals.
6. Check **Per-Case Detail** at the bottom for the individual conversations behind the week's numbers.

**Say out loud:** The page notes that Per-Case Detail (read live from current conversations) and the Case Volume total above (read from the reporting warehouse) can legitimately show slightly different counts, since they come from two different data sources — this is expected, not a bug.

**Hands-on:** exercise `AD-16` — see `exercises.md`.

### SLA reports  ·  10 min  ·  supervisor+

**Source:** `07-reports.md` → `## SLA reports`

**Say:** Chatwoot's native SLA report, showing which conversations met or missed their assigned SLA policy, plus a Proton section beneath it that rolls SLA achievement up across every channel and shows SLA compliance broken down into time buckets. This is a different page from **SLA Policies** under Administration, which is where SLA rules are configured rather than reported on.

**Show:** **Reports → SLA** in the left sidebar.

**Walk through:**

1. Open **Reports → SLA**.
2. Review the native SLA table for individual conversations that hit or missed their SLA.
3. Scroll down to **Cross-Channel SLA Achievement** for the overall met/missed percentage across every channel, plus a per-channel breakdown chart.
4. Continue to **SLA Compliance by Bucket** for a chart of how many cases fall into each SLA time bucket, split by case type.

**Hands-on:** exercise `AD-17` — see `exercises.md`.

### Dealer escalation turnaround  ·  5 min  ·  supervisor+

**Source:** `07-reports.md` → `## Dealer escalation turnaround`

**Say:** A measurement of how long a dealer takes to act on a case escalated to it. The clock starts the first time a case is marked as escalated to a specific dealer — not from when the case was originally created — and stops when the case is resolved. This keeps time spent before escalation from unfairly counting against the dealer.

**Show:** The **Dealer Escalation Turnaround** table appears on the SLA reports page and again on the Weekly Report, both showing the same underlying numbers.

**Walk through:**

1. Open the SLA reports page or the Weekly Report.
2. Find the Dealer Escalation Turnaround table: cases escalated, average turnaround, and P50/P90 turnaround in days, per dealer.
3. Check the **Slowest cases** sub-table for the specific conversations taking the longest to turn around — these are the ones worth following up individually.
4. Compare dealers to spot ones consistently slower than the group, and raise it with that dealer's PIC via Escalation Routing (see Administration).

## Module 08 — Campaigns & Help Center  ·  2 topics  ·  12 min

### Campaigns  ·  6 min  ·  admin+

**Source:** `08-campaigns-helpcenter.md` → `## Campaigns`

**Say:** Chatwoot's native campaigns feature, used to proactively message contacts rather than only waiting for them to write in. Campaigns come in two kinds: **one-off** campaigns, sent once to a target audience over a channel such as WhatsApp or SMS, and **ongoing** campaigns, which trigger automatically for website-chat visitors who match a condition (for example, visiting a specific page).

**Show:** **Campaigns** in the left sidebar.

**Walk through:**

1. Open **Campaigns** from the sidebar.
2. Choose whether to create a **one-off** or an **ongoing** campaign.
3. For a one-off campaign, pick the inbox/channel, write the message, and select or upload the audience to send it to.
4. For an ongoing campaign, pick the website inbox, set the trigger condition (page URL, time on page, and similar), and write the message shown to matching visitors.
5. Save and activate the campaign, then monitor delivery and responses from the campaign's detail page.

**Say out loud:** Campaigns may not be enabled or visible for every Proton deployment — confirm on the live tenant whether this menu is present for your account before relying on it in training material.

### Help Center portal  ·  6 min  ·  admin+

**Source:** `08-campaigns-helpcenter.md` → `## Help Center portal`

**Say:** Chatwoot's native Help Center (knowledge-base portal) feature for publishing self-service articles that customers can read without contacting an agent, organized into categories.

**Show:** **Help Center** in the left sidebar, or under account settings depending on the tenant.

**Walk through:**

1. Open **Help Center** from the sidebar (or Settings).
2. Create or select a portal, then add **categories** to group related articles.
3. Create an article inside a category and write its content; it starts as a draft.
4. Preview the article, then **publish** it so it becomes visible on the public portal.
5. Keep articles up to date as procedures change, and unpublish or archive ones that are no longer accurate.

**Say out loud:** The Help Center menu may not be enabled for every Proton deployment — confirm on the live tenant whether this feature is active before relying on it in training material.

## Module 09 — Administration (Settings)  ·  14 topics  ·  1 h 55 min

### Agents  ·  10 min  ·  admin+

**Source:** `09-administration.md` → `## Agents`

**Say:** The list of everyone who can log in to this CRM as staff, together with their role (agent or administrator) and which teams/inboxes they belong to.

**Show:** Open **Settings** (the gear icon) and select **Agents**.

**Walk through:**

1. Open **Settings → Agents** to see everyone with access to this CRM.
2. To add someone, click **Add Agent**, enter their name and email, and pick a role (**Agent** or **Administrator**). An invitation is sent to that email address.
3. To change someone's role or the inboxes/teams they can see, click on their row and update the settings, then save.
4. To remove someone's access, open their row and choose the remove/deactivate option, then confirm.

**Hands-on:** exercise `AD-18` — see `exercises.md`.

### Teams  ·  5 min  ·  admin+

**Source:** `09-administration.md` → `## Teams`

**Say:** A named group of agents (for example, one team per department) used to route and assign conversations as a group rather than to individuals one by one.

**Show:** Open **Settings** (the gear icon) and select **Teams**.

**Walk through:**

1. Open **Settings → Teams** to see the existing teams.
2. To add a team, click **Add Team**, give it a name and description, and choose whether conversations assigned to the team round-robin between its members automatically.
3. Add or remove agents from the team on its detail page.
4. To retire a team, open it and use the remove/delete option, then confirm.

### Inboxes (incl. inactivity timing)  ·  12 min  ·  admin+

**Source:** `09-administration.md` → `## Inboxes (incl. inactivity timing)`

**Say:** An inbox is one connected communication channel — a WhatsApp number, an email address, the website widget, and so on. Each inbox has its own settings: which agents can see it, its working hours, and (Proton addition) how long an idle conversation waits before the customer is warned and the conversation is auto-closed.

**Show:** Open **Settings** (the gear icon) and select **Inboxes**, then choose the inbox you want to configure.

**Walk through:**

1. Open **Settings → Inboxes** and select the inbox to configure.
2. Use the inbox's general settings to control its name, the agents/teams assigned to it, and greeting/away messages.
3. Open the tab that hosts business hours to set working hours for the inbox.
4. On the same tab, scroll to **Inactivity & auto-close**. Toggle **Enable inactivity & auto-close for this inbox** on or off.
5. Set **Warn after idle (min)**, **Close grace — in business hours (min)**, **Close grace — out of hours (min)**, and **Resolution-confirm grace (min)**. Leave any of these blank to inherit the tenant-wide default instead of setting a per-inbox value.
6. Optionally customize the wording of the **Idle warning message**, **Chat closed message**, **Resolution prompt**, **Assign-to-agent message**, **AI rating survey**, **Agent rating survey**, and **Thank-you message** sent to the customer at each stage. Leave a field blank to use the default wording.
7. Click **Update** to save — this single action saves both the business hours and the inactivity-timing settings together.

**Hands-on:** exercise `AD-19` — see `exercises.md`.

### Labels  ·  5 min  ·  admin+

**Source:** `09-administration.md` → `## Labels`

**Say:** Labels are short, colour-coded tags administrators define for use across conversations — including the `escalate` label and the dealer labels that drive escalation notifications (see the Conversations and AI Behaviour chapters).

**Show:** Open **Settings** (the gear icon) and select **Labels**.

**Walk through:**

1. Open **Settings → Labels** to see every label defined for the account.
2. Click **Add Label**, give it a name, description, and colour, then save.
3. To change a label's name, description, or colour, click on it and update the fields.
4. To remove a label, use its delete option and confirm. This removes the label from any conversation it was applied to.

### Custom Attributes  ·  10 min  ·  admin+

**Source:** `09-administration.md` → `## Custom Attributes`

**Say:** Custom Attributes let administrators define extra fields on conversations or contacts beyond what the CRM ships with by default — for example, the five case-categorisation fields used by Cases (see the Cases chapter's Case categorisation section): **Case Type**, **Case Category**, **Case Subcategory**, **Case Detail**, and **Vehicle Model**, all rendered as single-select ("List") conversation attributes. **Case Type and Vehicle Model are new since the last edition of this guide** — before this edition, neither had a definition here at all, so neither dropdown rendered in the conversation sidebar. Case Category/Case Subcategory/Case Detail values are matched to the client's RFP 2026_028 Appendix A taxonomy exactly, including Case Detail's em-dash convention for folding in the RFP's Level 3/4 rows (see the Cases chapter for the full explanation) — these lists are large (246 Case Detail values alone) and are provisioned from that source file, not hand-typed one at a time.

**Show:** Open **Settings** (the gear icon) and select **Custom Attributes**.

**Walk through:**

1. Open **Settings → Custom Attributes**.
2. Click **Add Custom Attribute**, choose whether it applies to conversations or contacts, give it a name and a type (text, number, list, checkbox, etc.), and save.
3. To edit an attribute's options, click on it and update the fields. For the five case-categorisation attributes specifically, treat this as a last resort rather than routine maintenance — Case Detail alone carries 246 values matched one-for-one against the RFP source, and a hand edit here won't update that source file, so the two can drift apart.
4. To remove an attribute that is no longer needed, use its delete option and confirm.

**Hands-on:** exercise `AD-20` — see `exercises.md`.

### Automation  ·  6 min  ·  admin+

**Source:** `09-administration.md` → `## Automation`

**Say:** Automation lets administrators define "when X happens, do Y" rules that run without an agent having to act — for example, applying a label automatically when a conversation matches certain conditions.

**Show:** Open **Settings** (the gear icon) and select **Automation**.

**Walk through:**

1. Open **Settings → Automation** to see the existing rules.
2. Click **Add Automation Rule**, choose an event to trigger on (for example, a conversation being created), add one or more conditions, and choose the action(s) to run (add a label, assign a team, send a message, etc.).
3. Save the rule. It applies to matching conversations going forward.
4. To adjust a rule, click on it and edit its conditions or actions.
5. To stop a rule from running, disable or delete it.

### Macros  ·  6 min  ·  admin+

**Source:** `09-administration.md` → `## Macros`

**Say:** Macros bundle a sequence of actions — such as adding a label, assigning a team, sending a reply, and resolving — into one saved, one-click action. This page is where those macro steps are created and edited; running a saved macro from an open conversation is covered in the Conversations chapter.

**Show:** Open **Settings** (the gear icon) and select **Macros**.

**Walk through:**

1. Open **Settings → Macros** to see the existing macros.
2. Click **Add Macro**, give it a name, and add the steps it should run in order (for example: apply a label, then send a reply, then resolve).
3. Save the macro. It becomes available to agents from the macro menu inside any open conversation.
4. To change a macro's steps, click on it, edit the step list, and save.
5. To retire a macro, delete it — this does not affect conversations it was already run on.

### Canned Responses  ·  5 min  ·  admin+

**Source:** `09-administration.md` → `## Canned Responses`

**Say:** Canned Responses are reusable message templates agents can insert into a reply instead of typing the same answer out each time.

**Show:** Open **Settings** (the gear icon) and select **Canned Responses**.

**Walk through:**

1. Open **Settings → Canned Responses** to see the existing templates.
2. Click **Add Canned Response**, give it a short code/shortcut and the full message text, then save.
3. To edit a template's text or shortcut, click on it and update the fields.
4. To remove a template that is no longer needed, use its delete option and confirm.

### Integrations (incl. DMS / TSP connection)  ·  7 min  ·  admin+

**Source:** `09-administration.md` → `## Integrations (incl. DMS / TSP connection)`

**Say:** This covers two related areas: the native **Settings → Integrations** page for connecting third-party apps and webhooks to the CRM, and a separate, permission-gated **Integrations** page in the main left-hand navigation where the Dealer Management System / Telematics Service Provider (DMS/TSP) connection is configured. The DMS/TSP connection is what lets the Customer 360 lookup (see the Contacts chapter) show a customer's vehicle and service history.

**Show:** For general integrations/webhooks, open **Settings** (the gear icon) and select **Integrations**. For the DMS/TSP connection, look for **Integrations** in the main left-hand navigation (visible only if your role has been granted the "Manage DMS/TSP integration settings" permission — see **Roles & Permissions** below), then open the **DMS / TSP** card.

**Walk through:**

1. From the main left-hand navigation, open **Integrations**, then click the **DMS / TSP** card. Its status badge shows **Not connected**, **Enabled**, or **Status unavailable** — it never claims to be actively connected, since that can only be confirmed with the **Test connection** button described below.
2. Check **Enabled** to turn the connection on.
3. Fill in a **Provider label** (a friendly name for your own reference), the **Auth type** (Bearer token, Basic auth, or API key header), and the **Base URL** of the DMS/TSP system (must start with `https://`).
4. Click **Replace** next to **Credential** to enter the API key/secret for this connection. Once saved, the credential always shows as masked (`••••••••`) and is never displayed again — click **Replace** again later to change it.
5. Optionally set an **Extra header name** and **Extra header value** if the DMS/TSP system requires an additional custom header. Unlike Credential, the extra header value is stored and shown in plain text, so avoid putting a second secret there.
6. Set the **Timeout (seconds)** the Customer 360 lookup should wait for a response before giving up and showing CRM data only (between 0.1 and 30 seconds).
7. Click **Save**, then click **Test connection** to confirm the saved configuration can actually reach the DMS/TSP system.

### SLA Policies  ·  12 min  ·  supervisor+

**Source:** `09-administration.md` → `## SLA Policies`

**Say:** SLA Policies define the response and resolution time targets the CRM tracks per conversation — either a single tenant-wide default, or an override for a specific inbox — plus two related timing settings: how long an unresolved breach waits before re-alerting a second time (**Tier-2**), and how much advance warning a case gets before it actually breaches. These targets are also what the always-on SLA breach engine checks a conversation against: when a case on the Email inbox goes past its target, the CRM posts a private note on it and emails the department's PIC group — see the Conversations chapter's SLA breach alerts section for what that looks like from an agent's side.

**Show:** In the main left-hand navigation, select **SLA Policies** (visible only if your role has been granted the "Manage SLA policies" permission — see **Roles & Permissions** below).

**Walk through:**

1. Open **SLA Policies**. Use the **Scope** dropdown to choose **Tenant default** or a specific inbox.
2. Set the **Response window (hours)** — how quickly a first reply is expected — and the **Resolution window (hours)** — how quickly the conversation is expected to be resolved.
3. Set **Tier-2 re-alert after (hours)** if you want a second, level-2 alert to fire when a case is still unresolved that many hours after its first breach — leave it blank to inherit the deployed default.
4. Set **Warn before breach (minutes)** to have the CRM raise an early warning that many minutes before a case is about to breach its resolution target, rather than waiting for the breach itself — leave it blank to inherit the deployed default.
5. Optionally set **Per-channel ACK minutes (JSON)** for a channel-specific acknowledgement target, for example `{"whatsapp": 15}`, and a **PIC WhatsApp number** to notify.
6. Leave any field empty on an inbox's policy to inherit the tenant default (or the deployed default, for Tier-2/warning) instead of setting an inbox-specific value.
7. Click **Save**. The policy applies to conversations on that scope going forward; it does not re-evaluate already-closed conversations.

**Hands-on:** exercise `AD-21` — see `exercises.md`.

### Audit Log  ·  10 min  ·  admin+

**Source:** `09-administration.md` → `## Audit Log`

**Say:** A read-only, filterable log of case/ticket status changes — who changed a case from one status to another, and when — used to review what happened on a case after the fact.

**Show:** In the main left-hand navigation, select **Audit Log** (visible only if your role has been granted the "View the audit log" permission — see **Roles & Permissions** below).

**Walk through:**

1. Open **Audit Log** to see every recorded status change, newest first. Each row shows the timestamp, the ticket/case, the actor who made the change, the transition (from status to status), and any remark left with it.
2. To narrow the list, enter an **Actor** to filter by who made the change, and/or a **From**/**To** date range.
3. Click **Filter** to apply the filters.

**Hands-on:** exercise `AD-22` — see `exercises.md`.

### Roles & Permissions  ·  10 min  ·  admin+

**Source:** `09-administration.md` → `## Roles & Permissions`

**Say:** This page controls which capabilities each role grants: both Chatwoot's own native access controls (which conversations a role can see, and whether it can manage contacts, reports, or the knowledge base) and the Proton-specific administrative permissions (SLA Policies, Audit Log, Roles & Permissions itself, Escalation Routing, Customer 360, and DMS/TSP integration management). It replaces the need to edit any configuration file to change what a role can do.

**Show:** In the main left-hand navigation, select **Roles & Permissions** (visible only if your role has been granted the "Manage roles and permission assignments" permission).

**Walk through:**

1. Open **Roles & Permissions**. Pick an existing role from the **Role** dropdown, or create a new one by entering a role id and name under **New role id** / **Name** and clicking **Create role**.
2. Under **Chatwoot access**, choose one conversation-visibility option for the role — **Manage all conversations**, **Unassigned conversations only**, or **My conversations only** — and toggle whether the role can manage **Contacts**, **Reports**, and/or **Knowledge base**.
3. Under **Permissions**, check or uncheck any of the Proton administrative permissions to grant or remove them for this role (for example, "Manage SLA policies" or "View the Customer 360 lookup").
4. Under **Assigned users**, enter a Chatwoot user id and click **Assign** to give a specific person this role, or click **Remove** next to a listed user id to take it away.

**Hands-on:** exercise `AD-23` — see `exercises.md`.

### Escalation Routing  ·  12 min  ·  admin+

**Source:** `09-administration.md` → `## Escalation Routing`

**Say:** Escalation Routing maintains two directories used to route notifications: which person-in-charge (PIC) to notify for each department, and which **dealer group** — a named group with one or more member email addresses — represents each dealer, for SLA breaches and escalated conversations. Every member of a dealer group receives the escalation forward, not just one address.

**Show:** In the main left-hand navigation, select **Escalation Routing** (visible only if your role has been granted the "Manage PIC/dealer escalation routing" permission).

**Walk through:**

1. Open **Escalation Routing**. It has two sections: **Department PICs** and **Dealer groups**.
2. To add a department's PIC, fill in **Department**, **PIC name**, **PIC email**, optionally **PIC WhatsApp**, and optionally **Members (CC)** (comma-separated email addresses to CC on that department's escalation email), then click **Add PIC**.
3. To change an existing PIC, click **Edit** on its row, update the fields — including adding or removing addresses from **Members (CC)** — and click **Save**; or click **Delete** and confirm to remove it.
4. To add a dealer group, fill in **Group name** and **Members** (one or more email addresses, comma-separated — everyone listed is forwarded the case) under the Dealer groups section, then click **Add dealer**.
5. To change a dealer group's membership — adding a new staff member or removing someone who's left — click **Edit** on its row, update the comma-separated **Members** list, and click **Save**; or click **Delete** and confirm to remove the whole group.
6. Changes take effect immediately for new escalations — there is no redeploy or waiting period. Any department or dealer not yet edited here falls back to the defaults configured when the tenant was set up.
7. To confirm every department actually routes before relying on it, scroll **Department PICs** and check that all six rows — `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, and `dept_technical` — show a PIC email, and that **Dealer groups** lists both `dealer_komang_motor` and `dealer_caroline_motor` with at least one member each. A row with no PIC/members is exactly the silent-dead-end case described above.

**Hands-on:** exercise `AD-24` — see `exercises.md`.

### Account settings  ·  5 min  ·  admin+

**Source:** `09-administration.md` → `## Account settings`

**Say:** Account-wide settings that are not specific to any one inbox, team, or agent — for example the account name, default timezone, and other platform-level preferences.

**Show:** Open **Settings** (the gear icon) and select **Account Settings**.

**Walk through:**

1. Open **Settings → Account Settings**.
2. Update the account name, timezone, or other available preferences as needed.
3. Save your changes.

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

## Module 11 — End-to-End Scenarios  ·  17 topics  ·  2 h 20 min

### Scenario 1: WhatsApp inquiry to resolution  ·  11 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 1: WhatsApp inquiry to resolution`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer messages Proton's WhatsApp number asking about the price and availability of a test drive for the e.MAS 7. Chatwoot creates a new conversation on the WhatsApp inbox, which is running in Suggest mode (see the AI Assistant Behaviour chapter's Suggest mode vs. Auto mode section).
2. The AI assistant drafts an answer grounded in the knowledge base and posts it as a private note, then reopens the conversation for a human (see the Conversations chapter's AI auto-draft section and the AI Assistant Behaviour chapter).
3. The on-duty agent opens the conversation, reads the suggested draft and its source citations, tweaks the wording slightly, and sends it as their own reply (see the Conversations chapter's Private notes and Suggest-a-reply sections).
4. The customer confirms they'd like to book the test drive; the agent arranges it and, once everything is confirmed, marks the conversation **Resolved** (see the Conversations chapter's Resolving, snoozing & transcripts section).
5. The customer receives the standard resolution prompt and satisfaction survey, and their 1–5 rating shows up later in the CSAT report (see the AI Assistant Behaviour chapter's Lifecycle messages section and the Reports chapter).

**Hands-on:** exercise `AD-25` — see `exercises.md`.

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

**Hands-on:** exercise `AD-26` — see `exercises.md`.

### Scenario 3: RSA call to Customer 360 follow-up  ·  6 min  ·  supervisor+

**Source:** `11-scenarios.md` → `## Scenario 3: RSA call to Customer 360 follow-up`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer calls Proton's support line reporting a breakdown on the toll road. The call is answered by the AI assistant, and the conversation appears in the Conversations view with the call transcript (see the AI Assistant Behaviour chapter's Voice bot behaviour section).
2. Because it's a roadside situation, an administrator with access to the RSA Incident Log page logs a new entry with the vehicle number, cause, and breakdown location (see the RSA Incident Log chapter's Logging an RSA incident section).
3. As towing is arranged and the vehicle reaches the dealer's outlet, the same administrator updates the incident record with the relevant timestamps (see the RSA Incident Log chapter's Incident statuses & updates section).
4. A few weeks later, the same dealer calls asking about that customer's vehicle history. An administrator with Customer 360 access opens **Customer 360**, searches by the vehicle number, and sees the completed RSA incident together with the customer's conversations in one place (see the Contacts chapter's Customer 360 section and the RSA Incident Log chapter's RSA in Customer 360 & reports section).
5. Where the DMS/TSP connection is configured, the same Customer 360 result also shows that vehicle's service history alongside the RSA incident (see the Contacts chapter's Customer 360 section and the Administration chapter's Integrations section).

### Scenario 4: FAQ batch import to live bot answer  ·  11 min  ·  admin+

**Source:** `11-scenarios.md` → `## Scenario 4: FAQ batch import to live bot answer`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. Ahead of an e.MAS 7 launch event, Proton's service team compiles a spreadsheet of 40 frequently asked warranty questions.
2. An administrator exports the spreadsheet as a CSV file and uses **Bulk upload (CSV)** under **Knowledge → FAQs** to import all 40 entries in one go (see the Knowledge chapter's FAQs section).
3. Before the launch, the administrator opens **Knowledge → Playground** and asks a few of the same questions a customer might ask, to confirm the assistant answers correctly using the newly imported entries (see the Knowledge chapter's Playground section).
4. Satisfied with the answers, the administrator leaves the entries active. When the launch event starts and a real customer asks one of those questions on WhatsApp, the AI assistant answers it live, grounded in the newly imported FAQ (see the Conversations chapter's Suggest-a-reply and AI auto-draft sections, and the AI Assistant Behaviour chapter).
5. Any question the imported FAQs don't cover still falls back to a normal handoff to a human agent, the same as any other unanswerable question (see the AI Assistant Behaviour chapter's When the AI replies vs. hands off to a human section).

**Hands-on:** exercise `AD-27` — see `exercises.md`.

### Scenario 5: Weekly reporting routine  ·  11 min  ·  supervisor+

**Source:** `11-scenarios.md` → `## Scenario 5: Weekly reporting routine`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. Every Friday afternoon, Proton's operations lead opens **Reports → Weekly Report** and sets the week picker to the period just finishing (see the Reports chapter's Weekly Report section).
2. They read **Case Volume** for the week's total and its channel/division breakdown, then **Case Status Trend** for how cases split across statuses during the window.
3. They scroll through **Inquiry / Complaint / Feedback Detail — Departments & PIC**, **Call Centre & SLA Performance**, and **Work-in-Progress / Case Aging** to note anything that needs follow-up before the client call.
4. They check **Dealer Escalation Turnaround** for any dealer whose average turnaround has crept up, ready to raise it on the call (see the AI Assistant Behaviour chapter's Escalation labels & the escalation email section for how that clock starts).
5. Figures from the page are copied into the weekly client deck; if the client asks for a deeper cut of the data than the page shows, the operations lead asks their CRM administrator to arrange a bulk export (see the Integration Overview chapter's BI/reporting exports section).

**Hands-on:** exercise `AD-28` — see `exercises.md`.

### Scenario 6: New agent onboarding  ·  11 min  ·  admin+

**Source:** `11-scenarios.md` → `## Scenario 6: New agent onboarding`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. Proton hires a new customer-service agent, Dian, to cover the After-Sales WhatsApp and email inboxes.
2. An administrator opens **Settings → Agents**, adds Dian with the **Agent** role, and an invitation is sent to her email address (see the Administration chapter's Agents section).
3. The administrator assigns her to the **After-Sales** team under **Settings → Teams**, so conversations can be routed to the team as a whole rather than to her individually at first (see the Administration chapter's Teams section).
4. The administrator makes sure she has access to the relevant WhatsApp and email inboxes under **Settings → Inboxes** (see the Administration chapter's Inboxes section).
5. Dian signs in for the first time using the credentials from her invitation, lands on the Conversations view, and — since her role is Agent — sees Conversations, Contacts, and Knowledge, but none of the administrator-only pages (see the Introduction chapter's Logging in and Roles: agent vs administrator sections).

**Hands-on:** exercise `AD-29` — see `exercises.md`.

### Scenario 7: A customer replies to their own acknowledgement email  ·  6 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 7: A customer replies to their own acknowledgement email`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer emails in about a delayed part; the agent escalates it with a department label, a dealer label, and **escalate**, and the customer receives the short acknowledgement email (see Scenario 2, above).
2. Two days later, still waiting, the customer hits **Reply** on that same acknowledgement email and asks for an update, without changing the subject line.
3. Chatwoot has no way to thread that reply onto the original case on its own, so it briefly appears as a brand-new conversation on the Email inbox — the agent doesn't need to do anything with this one; the CRM resolves it automatically in the background.
4. **Correction from the previous edition:** on the **original** conversation, the customer's message does **not** appear as a normal incoming message. Chatwoot only accepts a synthetic incoming message on an Api-channel inbox, and this always runs on the Email channel, so that attempt is always rejected. What the agent actually sees is a private note prefixed `Customer's own reply (from <email>, could not be posted inline -- see conversation <id>):`, followed by the customer's own words — and the conversation still reopens by itself, exactly as before (see the Conversations chapter's Escalation replies section).
5. **What to actually do:** treat that private note as the customer's message — it is, verbatim, just delivered as a note rather than an inline bubble — and reply publicly to it the same way as any other reopened case. Don't wait for it to turn into an ordinary message; it won't. Nothing about the escalation itself (the ack, the PIC email, the dealer forward) fires again just because the customer replied.

### Scenario 8: Maintaining a dealer group  ·  11 min  ·  admin+

**Source:** `11-scenarios.md` → `## Scenario 8: Maintaining a dealer group`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. Dealer Kelapa Gading adds a second service advisor, Pak Rudi, who should also see escalated cases forwarded to that dealer, alongside the existing contact.
2. An administrator with the escalation-routing permission opens **Escalation Routing** and finds the **Dealer groups** section (see the Administration chapter's Escalation Routing section).
3. They click **Edit** on the Kelapa Gading dealer group's row, add Pak Rudi's email address to the comma-separated **Members** field alongside the existing address, and click **Save**.
4. From that point on, every escalation forwarded to Kelapa Gading — including one already mid-conversation — goes to both addresses, with no redeploy or waiting period.
5. Months later, the original contact leaves the dealership. The same administrator edits the group again, removes that address from **Members**, and saves — future escalations stop reaching the old address immediately.

**Hands-on:** exercise `AD-30` — see `exercises.md`.

### Scenario 9: An SLA breach reaches the PIC group and the case  ·  6 min  ·  supervisor+

**Source:** `11-scenarios.md` → `## Scenario 9: An SLA breach reaches the PIC group and the case`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. An email case sits open overnight with no first agent reply, past the Email inbox's configured Response window (see the Administration chapter's SLA Policies section).
2. Once the next SLA scan runs, the CRM posts a private note on the conversation starting `⚠️ SLA breach`, naming which target was missed (see the Conversations chapter's SLA breach alerts section).
3. At the same moment, the department's PIC group (resolved from the conversation's own department label) receives an email with the breach details and a link back to the case.
4. The next morning, the on-duty agent opens Conversations, spots the breach note on a case in their queue, and replies immediately — already aware the PIC group has been notified too, so a follow-up from that side may already be in motion.
5. A supervisor checks later in the day and confirms no second alert fired for the same breach — the CRM only alerts once per breach per case.

### Scenario 10: Adjusting SLA thresholds ahead of a launch event  ·  11 min  ·  supervisor+

**Source:** `11-scenarios.md` → `## Scenario 10: Adjusting SLA thresholds ahead of a launch event`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. Ahead of an e.MAS 7 launch event, Proton expects a spike in email inquiries and wants a tighter response target on the Email inbox for the week, plus more advance warning before anything breaches.
2. An administrator with the SLA-management permission opens **SLA Policies**, sets **Scope** to the Email inbox, and lowers the **Response window (hours)** from 1 to 0.5 (see the Administration chapter's SLA Policies section).
3. They also set **Warn before breach (minutes)** to 30, so the team gets an early nudge with half an hour of runway rather than finding out only once a case has already breached.
4. They leave **Tier-2 re-alert after (hours)** blank, since the deployed default re-alert timing is fine for this event.
5. They click **Save** — the tighter thresholds apply to Email-inbox cases from that moment on. The following week, once the event traffic has settled, the administrator returns to the same page and clears the Response window and warning fields back to blank, restoring the tenant default.

**Hands-on:** exercise `AD-31` — see `exercises.md`.

### Scenario 11: Editing a customer-facing email template  ·  6 min  ·  admin+

**Source:** `11-scenarios.md` → `## Scenario 11: Editing a customer-facing email template`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. Proton's brand team wants the escalation acknowledgement email to sound warmer than the built-in default wording.
2. An administrator opens **Knowledge → Settings**, scrolls to **Tenant settings**, and finds the **Escalation acknowledgement template** field (see the Knowledge chapter's Settings section).
3. They rewrite the wording, leave every other field on the panel untouched, and click **Save settings**.
4. The next time an agent escalates an Email case, the customer receives the new wording instead of the platform default — nothing else about the escalation flow changes.
5. The administrator also looks at the **Inbound auto-acknowledgement template** field just above it and edits that wording too, for consistency — but is careful to check with their CRM contact first, since on this tenant the inbound auto-acknowledgement is currently switched off. Editing the template alone doesn't turn the feature on; nothing will actually send until an administrator enables it.

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

**Hands-on:** exercise `AD-32` — see `exercises.md`.

### Scenario 16: An AI-suggested escalation department — accepted, and ignored  ·  5 min  ·  agent+

**Source:** `11-scenarios.md` → `## Scenario 16: An AI-suggested escalation department — accepted, and ignored`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. A customer emails Proton asking about financing options and a trade-in value before committing to an order. No department label is on the conversation yet.
2. Within moments, a private note appears naming **pre_sales** as the AI-suggested escalation department, explicitly stating it's a suggestion only and that no label has been applied (see the Conversations chapter's AI-suggested escalation department section).
3. The agent agrees with the suggestion, adds `dept_pre_sales` from the label control, then applies `escalate` last — the case routes to the Pre-Sales PIC exactly as if the agent had picked the department unprompted.
4. On a second, unrelated case — a customer asking a routine delivery-status question — the same kind of note suggests `sales`. The agent judges this one is actually an After-Sales matter instead, so they simply don't add `dept_sales`; they apply `dept_aftersales` themselves and escalate with that. The suggestion is never applied automatically either way, so ignoring it costs nothing and requires no extra click.

### Scenario 17: Verifying escalation now reaches all six departments and a dealer group  ·  5 min  ·  admin+

**Source:** `11-scenarios.md` → `## Scenario 17: Verifying escalation now reaches all six departments and a dealer group`

**Say:** _(the handbook section has no summary paragraph — the steps below are the whole of it)_

**Walk the scenario through, in order:**

1. Ahead of relying on escalation routing for a live launch event, a supervisor with the escalation-routing permission opens **Escalation Routing** (see the Administration chapter's Escalation Routing section) and checks that all six department rows — `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, and `dept_technical` — show a PIC email, and that both dealer groups, `dealer_komang_motor` and `dealer_caroline_motor`, list at least one member.
2. Satisfied, they escalate one test case per department label in turn — `dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, `dept_technical`, each followed by `escalate` — and confirm each PIC actually receives the forward.
3. They escalate one more test case with `dealer_komang_motor` added alongside a department label, and confirm every member of that dealer group receives it too.
4. Before this edition, most of these department labels had no PIC configured — escalating with them stamped the conversation and looked identical to a working escalation, but sent no mail to anyone, with nothing in the CRM to say so. That gap is closed; the supervisor documents that all seven destinations (six departments plus the two dealer groups) are now verified live, and tells the team they no longer need to double-check with a phone call after escalating.

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

## Module 13 — Integration Overview  ·  9 topics  ·  51 min

### WhatsApp  ·  6 min  ·  admin+

**Source:** `13-integrations.md` → `## WhatsApp`

**Say:** Proton's WhatsApp Business number connected to the CRM as a channel, so customer WhatsApp messages arrive and are answered from inside the same platform as every other conversation.

**Show:** Appears as a WhatsApp inbox in the Conversations view (see the Conversations chapter's Conversation inbox & views section).

**Walk through:**

1. A customer messages Proton's WhatsApp number; the message arrives as a new (or continuing) conversation in the WhatsApp inbox.
2. Handle it like any other conversation — read, reply, and use AI-assist features exactly as covered in the Conversations chapter.
3. **If the case needs escalating, don't rely on the `escalate` label here.** Adding it to a WhatsApp conversation by hand does not send an email or a WhatsApp alert to anyone — the automatic escalation flow only fires on an Email-channel conversation (see this chapter's Email section, below). The only automatic PIC notification on WhatsApp is the AI assistant's own judgement that a message is a genuine complaint, which an agent can't trigger by hand. **What to actually do:** contact the dealer or PIC directly (phone, a direct email), and don't tell the customer "I've escalated this" on the strength of the label alone. A `dealer_<slug>` label still records the case against that dealer for turnaround reporting — that part works on every channel — but it's a reporting stamp, not a notification.
4. Voice notes and photos aren't a reliable way to get information to the assistant yet — this has not been tested end to end on a real WhatsApp number. Describe it as untested if a customer or colleague asks, not as working.
5. If WhatsApp messages stop arriving, or replies aren't reaching customers, report it to your CRM administrator or Devoteam support — this is usually a problem with the WhatsApp Business connection itself, not something fixable from inside the CRM.

### Web chatbot  ·  6 min  ·  admin+

**Source:** `13-integrations.md` → `## Web chatbot`

**Say:** The website's live chat widget, connected as a channel so a visitor's typed questions are answered by the same AI assistant used on WhatsApp. Two practical differences from WhatsApp: replies go out as plain text (no chat-style formatting conversion), and a visitor isn't required to give a name, phone number, or email unless the widget's own pre-chat form has been set up to ask for one.

**Show:** Appears as a Web Chatbot inbox in the Conversations view, wherever the widget has been embedded on the website and the AI assistant has been assigned to that inbox — confirm both are set up with an administrator before promising a visitor the bot will answer.

**Walk through:**

1. A visitor opens the chat widget on the website and types a question; it arrives as a new (or continuing) conversation in the Web Chatbot inbox.
2. Handle it like any other conversation — read, reply, and use AI-assist features exactly as covered in the Conversations chapter. Replies sent from here reach the visitor as plain text.
3. Because a visitor may not have given any contact details, don't assume a web-chat contact and a later phone or email contact are the same person just because the story sounds similar — check the contact panel's own record before treating them as linked (see the Contacts chapter).
4. **The `escalate` label has the same limitation here as on WhatsApp:** adding it to a Web Chatbot conversation by hand doesn't send an email or notify anyone — only an Email-channel conversation triggers the automatic escalation flow. **What to actually do:** contact the dealer or PIC directly, outside the CRM, the same as on WhatsApp.
5. If the widget stops loading, or messages aren't arriving, report it to your CRM administrator or Devoteam support.

### Voice bot  ·  6 min  ·  admin+

**Source:** `13-integrations.md` → `## Voice bot`

**Say:** The AI-answered part of an inbound call to Proton's support line: a live voice conversation (not a keypad menu) that greets the caller, answers questions from the same knowledge base as other channels, and asks for a 1–5 rating before the call ends.

**Show:** No settings screen inside the CRM. What you see is what each call leaves behind: a conversation in the Conversations view carrying the call's transcript, appearing either live during the call or all at once at hangup, depending on how your tenant is configured.

**Walk through:**

1. A customer calls Proton's support line; the assistant answers, and the call appears as a conversation with the spoken exchange logged as a transcript.
2. The assistant is meant to answer in English, Bahasa Melayu, or Chinese and switch mid-call to match the caller — but **reliable Bahasa Melayu on a live call is a known, unresolved issue.** Don't promise a Bahasa-speaking caller the assistant will stay in Bahasa for the whole call; if they get stuck in English, apologize and hand the call to a human rather than calling it fixed.
3. At the end of the call, the caller is asked to rate the interaction 1–5; that rating feeds the CSAT report the same way a text-channel rating does.
4. If the caller asks for a person, whether a transfer is even attempted depends on your tenant's configuration — see the Phone section below for what happens next once one is.
5. **Read this before promising anything else about this channel:** the greeting, KB answers, and rating survey were confirmed on a real, live call. Live transcript streaming, call classification, recording, and a real agent transfer are built and code-reviewed but, as of this writing, have never been run against a real phone call — describe them as "should work, unconfirmed" if a client asks, not as proven.

### Phone  ·  7 min  ·  admin+

**Source:** `13-integrations.md` → `## Phone`

**Say:** The human side of the same call the Voice bot section covers: what's left behind in Chatwoot once a call ends or is handed to a person, and — on tenants where it's turned on — an attempted live transfer to an agent mid-call.

**Show:** The same conversation the Voice bot section describes — there's no separate "Phone" inbox or settings screen; it's what an agent does with that conversation.

**Walk through:**

1. Open the resulting conversation like any other and read the transcript.
2. **If a transfer to a human is attempted:** the assistant tells the caller it's trying to connect them, then dials a single support number — the same number for every reason a call gets transferred; there's no separate always-on line for any particular kind of call.
3. **A real correction worth knowing:** every transfer attempt is gated by the support inbox's normal business hours, with no exception for accident or roadside calls. **Don't tell a caller reporting an accident after hours that they'll be automatically connected to a 24/7 line — that isn't built.** Log the incident in the RSA Incident Log chapter and handle the follow-up manually instead.
4. If someone answers, the conversation reopens with a note recording the handoff — check it while the transfer is still ringing, not after everyone's hung up.
5. **If nobody answers,** the caller hears a short apology and the call simply ends — it does not return to the assistant. The conversation is tagged so it's easy to find in your queue. **The apology promises a callback, but nothing sends that callback automatically** — only a human working that tagged conversation makes it happen, so treat an unanswered transfer as an open action item, not a closed loop.
6. **The `escalate` label has the same limitation here as on WhatsApp and the web chatbot:** a phone-originated conversation isn't on the Email inbox, so applying `escalate` by hand doesn't send anyone an email. Escalate to a dealer/PIC manually, the same as the other two channels.
7. If a caller reports call quality issues, dropped transfers, or a transfer that should have connected but didn't, report it to your CRM administrator or Devoteam support — most of this behaviour is still being confirmed against real calls.

### Email (incl. escalation emails)  ·  6 min  ·  admin+

**Source:** `13-integrations.md` → `## Email (incl. escalation emails)`

**Say:** Proton's support email address(es) connected as an Email channel, plus the two-thread escalation email flow that fires when an agent applies the `escalate` label to an Email-channel conversation, and the reply loop that links a dealer's, PIC's, or customer's reply back onto the case that sent it. This is the one channel where escalation labels actually send email — see the WhatsApp, Web chatbot, and Phone sections above for what to do on the others.

**Show:** Appears as an Email inbox in the Conversations view. The escalation email flow itself has no separate settings screen — it's triggered by applying a label (see the AI Assistant Behaviour chapter's Escalation labels & the escalation email section).

**Walk through:**

1. A customer emails Proton's support address; a new conversation is created on the Email inbox. If your tenant has the inbound auto-acknowledgement turned on, the customer also receives an automatic acknowledgement of receipt — check with your administrator whether it's on for you, since it's an editable, opt-in setting rather than something every tenant has running (see the Knowledge chapter's Settings section).
2. Handle the conversation like any other email conversation.
3. If the case needs escalating, apply a department label, then a dealer label if one applies, then the `escalate` label, in that order — the customer acknowledgement and the internal PIC/dealer-group forward emails are sent automatically (see the AI Assistant Behaviour chapter).
4. Watch the conversation for the dealer or PIC's reply — it's linked back onto this same conversation as a private note, with an AI-drafted customer reply beside it, rather than arriving as a separate email you have to go find (see the Conversations chapter's Escalation replies section). A customer who replies to their own acknowledgement rejoins the conversation the same way, as a normal incoming message.
5. If an expected escalation email, acknowledgement, or reply doesn't arrive, first check the recipients set up in Escalation Routing (see the Administration chapter); if it's still not working, report it to your CRM administrator or Devoteam support.

### Gemini AI  ·  5 min  ·  admin+

**Source:** `13-integrations.md` → `## Gemini AI`

**Say:** Google's Gemini AI model, which powers every AI-assist feature in the CRM: AI auto-drafted or auto-sent replies, Suggest-a-reply, the Ask Copilot panel, Summarize, and Playground testing.

**Show:** Not a page of its own — it's the engine behind the AI-assist buttons and automatic behaviour covered in the Conversations, Knowledge, and AI Assistant Behaviour chapters.

**Walk through:**

1. Use it indirectly through the Conversations chapter's Suggest a reply, Ask Copilot, and Summarize actions, or let it act automatically through AI auto-draft (see the AI Assistant Behaviour chapter).
2. Administrators tune how it behaves — its persona, guardrails, and response style — under **Knowledge → Settings** (see the Knowledge chapter).
3. Test how it will answer before customers see it, using **Knowledge → Playground**.
4. If AI-assist features stop responding, answer in the wrong language, or seem to ignore a guardrail, first check the assistant's configuration under Knowledge → Settings; if the problem continues, report it to your CRM administrator or Devoteam support.

### DMS / TSP  ·  5 min  ·  admin+

**Source:** `13-integrations.md` → `## DMS / TSP`

**Say:** An optional connection to a dealer's own Dealer Management System or Telematics Service Provider, which lets the Customer 360 lookup show a customer's vehicle and service history alongside their CRM conversations.

**Show:** Configured under **Administration → Integrations**, on the **DMS / TSP** card (visible only to administrators with the matching permission — see the Administration chapter's Integrations and Roles & Permissions sections). Its results appear inside a **Customer 360** lookup (see the Contacts chapter).

**Walk through:**

1. An administrator with the right permission configures the connection under **Administration → Integrations → DMS / TSP** — provider label, authentication, base URL, and credential — and clicks **Test connection** to confirm it's reachable (see the Administration chapter).
2. Once enabled, any Customer 360 search that matches a vehicle shows a **DMS / TSP** section with that vehicle's service history.
3. A **Not connected** notice means the connection isn't configured or isn't reachable; a **Mock data** notice means the results shown are demo data rather than a live system. Either way, Customer 360 still shows the CRM's own contact, conversation, and RSA data.
4. If the connection reports an error or shows unexpected data, first check its status and **Test connection** result under Administration → Integrations; if it still doesn't work, report it to your CRM administrator or Devoteam support, since the fault may be on the dealer's own system.

### Knowledge base (Vertex corpus)  ·  5 min  ·  admin+

**Source:** `13-integrations.md` → `## Knowledge base (Vertex corpus)`

**Say:** The combined FAQ and document corpus the AI assistant is grounded on: editable FAQ entries, an indexed document corpus, and operator-uploaded material, all covered in full in the Knowledge chapter.

**Show:** **Knowledge → FAQs**, **Knowledge → Documents**, and **Knowledge → Uploads** (see the Knowledge chapter).

**Walk through:**

1. Maintain question-and-answer entries under **Knowledge → FAQs**, including bulk CSV import for adding many at once (see the Knowledge chapter's FAQs section).
2. Browse the larger indexed document corpus under **Knowledge → Documents**, and add operator-authored material under **Knowledge → Uploads** (see the Knowledge chapter's Documents section).
3. Test how the assistant answers using this material before customers see it, in **Knowledge → Playground**.
4. If an AI answer looks wrong, outdated, or missing a source, check whether the relevant FAQ entry or document is active/indexed first; if material that should be indexed is stuck as **failed** or missing, report it to your CRM administrator or Devoteam support.

### BI / reporting exports  ·  5 min  ·  admin+

**Source:** `13-integrations.md` → `## BI / reporting exports`

**Say:** A way to get reporting figures out of the CRM for use in external BI/analysis tools, beyond what a given report page shows on screen.

**Show:** Some report pages offer their own download/export option directly on the page (see the Reports chapter); a larger, tenant-wide export is arranged through your CRM administrator rather than a self-service button on every report.

**Walk through:**

1. First check whether the report you need already offers a download or export option on its own page (see the Reports chapter).
2. If you need more data than that page shows — for example, a full dataset behind the Weekly Report for a client business review — ask your CRM administrator to arrange a bulk export.
3. Use the exported figures in your external BI/reporting tool as needed.

## Module 14 — Glossary  ·  1 topics  ·  3 min

### Terms  ·  3 min  ·  agent+

**Source:** `14-glossary.md` → `## Terms`

**Say:** _(reference table of 25 rows — hand it out rather than present it)_

## What this curriculum cannot teach yet

9 topics this role would be expected to cover are absent from the handbook source, or present only as the tenant's current behaviour. They are listed here rather than written from a specification, because a curriculum that teaches an unbuilt page loses its cohort on day one.

| Topic | Why it is not here | Unblocked by |
|---|---|---|
| Agent availability and the workforce dashboard | Held out of the handbook source on 2026-08-09 because fork patches `0053`/`0054` have never been built into an image — "My status" and "Workforce" do not appear in the deployed JS bundle. The written section is parked in `feature-guide-v3-pending.md`. | P6 · a Cloud Build of patches 0053+0054 |
| Performance targets and attainment | P5 built a targets store and an attainment view, and the deployed backend's own OpenAPI document has no `/metrics/targets`. There is no handbook section, and inventing one would teach a page no supervisor can open. | P5 · backend rebuilt past `e6dc537`, then a handbook section |
| Alert preferences / inbound alerts | The admin surface is fork patch `0057`, unbuilt. The feature is also behind two independent switches (blocked-work register §3h, §3i), so even once the patch ships, what an operator sees depends on a second gate the UI does not mention. | P9 · a Cloud Build of patch 0057, plus both switches on |
| Case taxonomy administration | The five-field taxonomy is taught from the agent's side (chapter 5) because that is what exists on the tenant. The admin page that edits the taxonomy is fork patch `0060`, unbuilt (blocked-work register §3m), so today an administrator still edits those lists as Custom Attributes. | P10 · a Cloud Build of patch 0060 |
| The redesigned Roles & Permissions page | The handbook documents the page as it exists on the tenant (patches `0027`/`0028`). Patch `0059`'s redesign is unbuilt, so the admin curriculum teaches the current page and must be regenerated when `0059` ships. | P10 · a Cloud Build of patch 0059, then a handbook update |
| Data scopes (row-level data access) | **Deliberately not taught, and not merely missing.** `DATA_SCOPED_RBAC_ENABLED` restricts nothing: the scope logic has no caller and no query applies it (risk R16, blocked-work register §3j). Teaching an administrator to rely on it would teach a control that does not exist. | Enforcement wiring — not a documentation task |
| AI conversational quality (Translate, FAQ composer) | Held out of the handbook source: patches `0055`/`0056` unbuilt and the deployed backend has no `/assist/translate`. Parked in `feature-guide-v3-pending.md`. | P7 · Cloud Build of 0055+0056, backend rebuilt |
| AI cost and performance measurement | Held out of the handbook source: the eleven BigQuery views were never created, so the reports have nothing to read even once the code ships. Parked in `feature-guide-v3-pending.md`. | P8 · backend rebuilt plus `ensure_views()` run |
| Hands-on voice and phone practice | The voice and phone topics are taught from the handbook, but no real Twilio call has ever been placed (risk R10) and every `PHONE_*` capability switch is off on the tenant. The channel topics are therefore presentation-only: there is no exercise for them, and inventing one would be a lab that cannot run. | R10 · one real call, then a sandbox phone number |

See `../delivery-plan.md` for the session schedule, prerequisites, the sandbox reset between cohorts and the refresher cadence.
