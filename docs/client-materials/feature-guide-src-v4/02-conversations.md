# Conversations
<!-- TRAINING: audience=agent -->

## Conversation inbox & views
<!-- TRAINING: audience=agent, exercise -->

### What it is

The conversation list is where every incoming conversation lands —
WhatsApp, the website chatbot, email, and phone/IVR alike — filterable by
who it's assigned to and by its status. By default the CRM shows **every** conversation across
**all** statuses, so nothing is hidden behind a narrower filter unless you
choose one.

### Where to find it

The left-hand column of the Conversations view, always visible once
you're signed in.

### How to use it

1. Use the assignee tabs to switch between **Mine**, **Unassigned**, and
   **All** conversations.
2. Use the status filter to narrow the list to **Open**, **Pending**,
   **Snoozed**, or **Resolved** conversations, or leave it on **All** to
   see everything regardless of status.
3. Click an inbox name in the sidebar to scope the list to a single
   channel (for example, just WhatsApp).
4. Click any conversation in the list to open its full thread.
5. Use the sort option to reorder the list, for example by most recent
   activity. <!-- VERIFY-LIVE: confirm the exact sort options available on the live tenant -->

[[SCREENSHOT: ch02-inbox-views | Conversation list showing the default "All" tab and "All" status filter]]

### Example scenario

At the start of a shift, an agent opens the Conversations view and — since
it defaults to "All" agents and "All" statuses — immediately sees a
pending WhatsApp inquiry about a test drive that came in overnight,
without having to change any filter first.

### Integrations & automation

New conversations can arrive automatically from the AI assistant handling
WhatsApp, web chatbot, or phone/IVR contact before a human ever sees them
(see the AI assistant behaviour chapter); this list is also where
assignment, labels, and priorities (below) become visible at a glance.

## Assignment & teams

### What it is

Assignment determines which agent — or which team — owns a conversation.
An unassigned conversation is visible to the whole team until someone
picks it up or it's assigned.

### Where to find it

The assignee and team controls in the conversation's action panel, next
to an open conversation.

<!-- VERIFY-LIVE: confirm exact assignment UI wording on the live tenant -->

### How to use it

1. Open the conversation you want to assign.
2. Click the assignee control and choose an agent, or click the team
   control and choose a team.
3. To reassign, repeat the same steps and pick a different agent or team.
4. To unassign, clear the assignee field.

[[SCREENSHOT: ch02-assignment | Assigning a conversation to an agent or team]]

### Example scenario

A WhatsApp inquiry about booking a test drive for the Proton e.MAS 7 comes
in; the on-duty agent assigns it to the "Sales — Jakarta" team so any
available team member can respond, rather than keeping it for themselves.

### Integrations & automation

When the AI assistant hands a conversation off to a human, it can trigger
an automatic agent assignment as part of that handoff (see the AI
assistant behaviour chapter). Administrators can also set which channels
each agent handles first, to steer that automatic assignment — see the
Administration chapter's Inboxes section.

## Labels
<!-- TRAINING: audience=agent, exercise -->

### What it is

Labels are tags you attach to a conversation to categorize it. Alongside
general-purpose labels, Proton uses two special families: an **escalate**
label that flags a conversation for the escalation email workflow, and
`dept_<slug>`/dealer-specific labels used to route that escalation and,
for dealer labels, to feed turnaround-time reporting. Applying a
department label routes the escalation to whichever contact is configured
for that department under Escalation Routing (see the Administration
chapter) — and a department with **no** contact configured there sends
the escalation email to nobody, with no error anywhere to warn you.
**Check the Escalation Routing page rather than assume every department
is covered.** (Checked on 2026-08-12: all six departments and both dealer
groups had a contact configured that day — but the directory is editable at
any time, so treat that as one data point, not a guarantee, and check the
page yourself.) A dealer label works the same way but can route to a whole
**dealer group** instead of a single contact — every member of that group
is forwarded the case, again only for a dealer that's actually been
configured there.

### Where to find it

The label control on an open conversation, and a Labels view in the
sidebar listing conversations by label.

<!-- VERIFY-LIVE: confirm exact label picker wording on the live tenant -->

### How to use it

1. Open the conversation you want to label.
2. Click the label control and pick one or more existing labels.
3. **Apply the department label first** — one of `dept_sales`,
   `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, or
   `dept_technical` — then the dealer label if one is involved, and apply
   **escalate** last. The escalation handler reads whichever labels are
   already on the conversation at the moment `escalate` is applied —
   applying it first means the department/dealer leg doesn't fire for
   that trigger. On an Email-channel conversation with no department
   label yet, you may see a private note suggesting one before you get
   here — see AI-suggested escalation department, below.
4. Apply the relevant dealer label so the conversation counts toward that
   dealer's turnaround reporting, even outside an email escalation.
5. Remove a label the same way, by deselecting it.
6. **A limitation worth knowing before you rely on this:** the automated
   escalation email only fires on an **Email**-channel conversation.
   Applying `escalate` on a WhatsApp, web chatbot, or phone conversation
   changes the label and nothing else — no one is notified. The dealer
   label still records the case for turnaround reporting on any channel;
   that part isn't affected. See the Integration Overview chapter's
   WhatsApp, Web chatbot, and Phone sections for what to actually do on
   those channels instead.

[[SCREENSHOT: ch02-labels | Applying a label to a conversation]]

### Example scenario

A customer's warranty complaint escalates over email; the agent adds the
department label, then the dealer's label, then **escalate** — in that
order — which together kick off the automated escalation email to
everyone on that dealer group and start the clock for turnaround
reporting. See Escalation replies, below, for what happens once the
dealer answers.

### Integrations & automation

The **escalate** label triggers the two-thread escalation email described
in the AI assistant behaviour chapter; dealer labels feed the dealer
turnaround figures shown in the Reports chapter. The email this sends now
carries a hidden reference back to this conversation, which is what makes
Escalation replies (below) possible.

## AI-suggested escalation department

### What it is

**New since the last edition of this guide.** On an Email-channel
conversation with no `dept_<slug>` label yet, an incoming customer
message can trigger a private note naming the department the AI thinks
is the best fit — for example:

> AI-suggested escalation department: **pre_sales**. This is a
> suggestion only — no label has been applied. If you agree, add the
> `dept_pre_sales` label yourself, BEFORE the `escalate` label…

**It never applies the label itself.** This is a nudge for a human, not
an automatic escalation — the note only ever proposes; you decide. It
only ever proposes a department that actually has a PIC configured, so
it can't suggest a dead end. It posts once per conversation (a second
incoming message won't post a second suggestion), and only on Email
inboxes.

### Where to find it

As a private note on the conversation, the same place any other private
note appears (see Private notes, below). Not something an agent turns on
— it's an administrator/tenant-level setting, off by default, and live
today on this tenant.

### How to use it

1. Watch for a private note starting `AI-suggested escalation
   department:` on an Email conversation you haven't yet labelled with
   a department.
2. **To act on it:** add the exact `dept_<slug>` label the note names —
   for example `dept_pre_sales` — yourself, from the label control, then
   continue the normal escalation order (dealer label if needed, then
   `escalate` last; see Labels, above). The note doesn't do this for you.
3. **To ignore it:** simply don't add the suggested label. Nothing
   happens automatically either way — you can pick a different
   department, or none at all, and the conversation carries on exactly
   as if the note weren't there. There's no "reject" button to click;
   not acting on it is the reject.
4. If you disagree with the suggestion, trust your own read of the case
   over the note — it's a best-effort classification from a single
   Gemini call over the recent transcript, not a guarantee.

[[SCREENSHOT: ch02-dept-suggestion-note | An AI-suggested escalation department private note on an Email conversation]]

### Example scenario

A customer emails asking about financing options and a trade-in value
before committing to an order — no department label yet. The AI posts a
private note suggesting `pre_sales`. The agent agrees, adds
`dept_pre_sales`, then `escalate`, and the case routes to the Pre-Sales
PIC as normal. On a different case, a customer's email is a straightforward
delivery-status question the agent already knows is Sales' — they see the
suggestion, judge it doesn't fit, and label it `dept_sales` themselves
without ever adding the suggested label.

### Integrations & automation

Candidates always come from the same Escalation Routing directory used
to actually send the mail (see the Administration chapter's Escalation
Routing section) — a department with no PIC configured can never be
suggested. This is a separate, best-effort layer on top of the escalation
mechanics in Labels (above) and the AI Assistant Behaviour chapter's
Escalation labels & the escalation email section; it changes nothing
about how the escalation email itself fires.

## Escalation replies

### What it is

When a dealer or PIC replies to an escalation email, their reply doesn't
start a fresh conversation you have to go hunting for — it's linked back
onto the original conversation automatically, as a private note starting
`Reply from <name> <email>:`. If a customer instead replies to their own
acknowledgement email, that reply rejoins their case too and the
conversation reopens — but **correction from the previous edition of
this guide: it does not arrive as an ordinary incoming message.**
Chatwoot only accepts a synthetic incoming message on an Api-channel
inbox; every Email-channel conversation (which is the only channel this
loop ever runs on) refuses it, since an Email inbox doesn't support
messages posted in that way. The CRM tries the inline post first, and it
always fails on this tenant's Email inbox today, so what you actually see
is a **private note**, prefixed `Customer's own reply (from
<email>, could not be posted inline -- see conversation <id>):`,
followed by the customer's own words. The reopen still happens either
way — that part behaves exactly as before.

### Where to find it

The reply lands as a new message on the **original** escalated
conversation — not on any new conversation that may briefly appear
elsewhere in the inbox. A throwaway conversation is created behind the
scenes to receive the raw email, but it's automatically labelled and
resolved, so it shouldn't show up in your queue as something to work.

### How to use it

1. Watch a conversation you've already escalated for a new private note
   starting `Reply from `. That's the dealer or PIC's reply, with any
   quoted trail and signature stripped out.
2. Directly beneath it, look for a second private note titled exactly
   `Suggested customer reply (draft — review before sending):`. This is
   an AI-drafted reply based on what the dealer said — read it, edit it
   to sound right, and send it as your own reply. It never goes to the
   customer on its own.
3. The conversation also picks up an `escalation_replied` label, so you
   can spot which escalated cases already have an answer waiting for
   review just by scanning labels in the conversation list.
4. **If the reply instead comes from the customer** replying to their
   own acknowledgement email, watch instead for a private note prefixed
   `Customer's own reply (from <email>, could not be posted inline --
   see conversation <id>):`, followed by their own words — not a normal
   incoming message, on this tenant's Email inbox. The conversation
   still reopens by itself either way. **What to actually do:** read the
   note as if it were the customer's message (it is, word for word,
   just delivered as a note instead of an inline bubble), then reply
   publicly the same way you would to any other reopened case — don't
   wait for a "real" incoming message that this loop is never going to
   post here.
5. A second reply from the same dealer/PIC address after the first one's
   already linked in doesn't post another note — check the dealer's own
   mailbox directly if they say they've replied twice. A customer can
   reply more than once without limit; each reply lands as its own
   private note the same way.

### Example scenario

An agent escalates a charging-fault complaint to a dealer on Monday. On
Wednesday the dealer emails back confirming the part has been ordered.
Within a couple of minutes the agent sees a `Reply from` private note on
the original conversation with the dealer's update, and a second note
with a drafted customer-facing reply already summarizing it — the agent
tidies the wording and sends it straight to the customer without
re-typing anything.

### Integrations & automation

This is the other half of the **escalate** label workflow described in
Labels, above, and in the AI Assistant Behaviour chapter's Escalation
labels & the escalation email section. Only senders in the Escalation
Routing directory (Administration chapter) — or the conversation's own
customer — are trusted to link a reply back onto a case this way.

## Inbound alerts

### What it is

A toast notification that pops up right inside the CRM the moment a new
customer message arrives on one of your inboxes — so you don't have to keep
staring at the conversation list to notice new work.

### Where to find it

It appears on its own, anywhere in the CRM, whenever a new customer message
lands in your inbox. No particular page needs to be open for it to fire.

### How to use it

1. Keep working anywhere in the CRM — the alert appears on its own when a
   new customer message comes in.
2. Click the toast to jump straight to that conversation.
3. Today this fires on the platform's built-in default for a new message: a
   toast notification, nothing more. A fuller, self-service preferences page
   — where each agent could add a sound or a desktop notification, or tune
   alerts for other events — exists in the product but is **not switched on
   for this tenant**, so there's nothing to configure yet. What you see is
   the same for every agent, and it can't currently be changed.

### Example scenario

An agent is deep in a different conversation when a new WhatsApp inquiry
lands on their inbox; a toast notification in the corner of the screen
catches their attention immediately, instead of the message sitting
unnoticed until they next glance at the conversation list.

### Integrations & automation

This is separate from SLA breach alerts (below), which are email-driven and
scoped to a missed response/resolution target rather than to every new
message.

## SLA breach alerts

### What it is

If a conversation on the Email inbox goes past its response or
resolution target without action, the CRM posts a private note on that
conversation flagging the breach, and separately emails the department's
PIC group (resolved from the conversation's own department label) so the
breach doesn't rely on someone happening to notice the note.

### Where to find it

The private note appears directly in the conversation's timeline, marked
private the same way any other internal note is. The matching email goes
to whichever PIC group is configured for that conversation's department
under Escalation Routing (see the Administration chapter).

### How to use it

1. Watch for a private note starting `⚠️ SLA breach` on a conversation —
   it names which target was missed (first response or resolution) and
   the case number.
2. Treat it as a prompt to act on the conversation now, not just a
   record — the department's PIC group has already been emailed the same
   breach, so a customer-facing follow-up may already be expected.
3. Respond to or resolve the conversation as normal; a second scan
   doesn't re-alert for the same breach once it's been recorded, so you
   won't see the note repeat on its own.
4. If a breach note appears but you believe the response/resolution
   target is wrong for this inbox, check — or ask an administrator to
   check — the thresholds under SLA Policies (see the Administration
   chapter).

[[SCREENSHOT: ch02-sla-breach-note | An SLA breach private note on a conversation]]

### Example scenario

An email case sits unassigned past its response window overnight. The
next morning, the agent opens the conversation and finds a private note
flagging the breach, already an hour old — the department's PIC group
also got an email at the same moment, so the agent replies immediately
rather than treating it as a routine pickup.

### Integrations & automation

SLA breach alerts read the same response/resolution targets set on the
SLA Policies admin page (see the Administration chapter), including the
Tier-2 re-alert and warning thresholds described there. Today this only
scans the Email inbox.

## Priorities

### What it is

A priority flag (for example Urgent, High, Medium, Low, or none) you can
set on a conversation to indicate how urgently it needs attention. It
shows as a badge in the conversation list so high-priority conversations
stand out.

<!-- VERIFY-LIVE: confirm the exact priority levels and badge styling on the live tenant -->

### Where to find it

The priority control in the conversation's action panel.

### How to use it

1. Open the conversation you want to flag.
2. Click the priority control.
3. Choose the priority level that matches how urgently it needs handling.
4. Change it at any point as the situation develops.

[[SCREENSHOT: ch02-priorities | Setting a conversation's priority]]

### Example scenario

A call reporting a vehicle breakdown on the highway is marked Urgent so
it surfaces at the top of the queue and gets picked up immediately for
roadside-assistance follow-up.

### Integrations & automation

A conversation's priority flag is separate from, but complements, the
per-agent channel handling order administrators can configure in
Administration → Inboxes, which decides which agent gets auto-assigned
first for a given channel.

## Private notes
<!-- TRAINING: audience=agent, exercise -->

### What it is

Internal messages attached to a conversation that only agents and
administrators can see — never the customer. Used for handoff context,
questions to a teammate, or reviewing an AI-drafted reply before sending
it.

### Where to find it

The reply box's note mode, toggled from the main "reply" mode.

<!-- VERIFY-LIVE: confirm exact private-note UI wording on the live tenant -->

### How to use it

1. Open the conversation and click into the reply box.
2. Switch the reply box to its private "Note" mode.
3. Type your note — mention a teammate with @ if you need their input
   (see Mentions below).
4. Send the note; it posts as private and is visually distinct from a
   customer-facing reply.

[[SCREENSHOT: ch02-private-note | Adding a private note visible only to agents]]

### Example scenario

An agent handing off their shift leaves a private note summarizing that
the customer has already been told their service booking is delayed by a
day, so the next agent doesn't repeat the same message.

### Integrations & automation

Private notes are exactly how an AI-drafted reply arrives when the AI
assistant is running in suggest mode (see AI auto-draft below), and where
Ask Copilot answers land when inserted into the conversation.

## Canned responses
<!-- TRAINING: audience=agent, exercise -->

### What it is

Pre-written reply snippets for common questions, so agents don't retype
the same answer over and over.

### Where to find it

A shortcut inside the reply box, typically triggered by typing a
character like "/" or clicking a canned-response icon.

<!-- VERIFY-LIVE: confirm exact canned-response UI wording on the live tenant -->

### How to use it

1. Click into the reply box.
2. Trigger the canned-response search (for example, typing "/").
3. Search by the short code or keyword for the response you need.
4. Select it — it's inserted into the reply box.
5. Edit if needed, then send as normal.

[[SCREENSHOT: ch02-canned-responses | Inserting a canned response into a reply]]

### Example scenario

For a frequently asked question about e.MAS charging cable compatibility,
an agent inserts the matching canned response instead of retyping the
same answer for the tenth time that day.

### Integrations & automation

Canned responses are created and maintained by administrators (see the
Administration chapter), which keeps the wording consistent across the
whole team.

## Macros

### What it is

A macro bundles a sequence of actions — for example, adding a label,
assigning a team, sending a reply, and marking the conversation resolved
— so an agent can run all of them in a single click instead of doing each
step by hand.

### Where to find it

The macro option in the conversation's action menu, inside an open
conversation.

<!-- VERIFY-LIVE: confirm the exact macro menu location/label and the macros available on the live tenant -->

### How to use it

1. Open the conversation you want to act on.
2. Open the macro menu from the conversation's action area.
3. Pick the macro you want to run from the list.
4. Confirm — its steps run automatically in order (for example: apply a
   label, send a reply, then resolve).
5. Check the conversation afterward to confirm every step applied as
   expected.

[[SCREENSHOT: ch02-macros | Running a macro from an open conversation]]

### Example scenario

An agent closes out a routine "test drive confirmed" conversation by
running a single macro that adds a confirmed label, sends the standard
confirmation message, and marks the conversation resolved.

### Integrations & automation

Agents can only run macros that already exist — building or editing what
a macro does is administrator work, covered in the Administration
chapter.

## Mentions

### What it is

Typing @ followed by a teammate's name inside a private note to notify
them directly — useful for handoffs or asking for a second opinion.

### Where to find it

Inside the private-note editor in the reply box.

<!-- VERIFY-LIVE: confirm exact mentions UI wording on the live tenant -->

### How to use it

1. Switch the reply box to note mode.
2. Type @ followed by the teammate's name.
3. Select them from the suggestion list that appears.
4. Finish writing your note and send it — the mentioned teammate is
   notified.

[[SCREENSHOT: ch02-mentions | Mentioning a teammate in a private note]]

### Example scenario

Unsure how to answer a question about an extended warranty case, an agent
mentions their team lead in a private note to ask for guidance before
replying to the customer.

### Integrations & automation

Mentions only work inside private notes, so a mentioned teammate is
notified without the customer ever seeing it.

## Ask Copilot panel
<!-- TRAINING: audience=agent, exercise -->

### What it is

A chat panel next to the conversation where an agent can ask an AI
assistant questions about the customer or the topic at hand, and get
answers grounded in the knowledge base. Each answer is followed by a
"Looked at" line naming which knowledge tools it consulted, and — when
sources are available — a "Sources" line with each source title, shown
as a clickable link where one exists or as plain text otherwise.

### Where to find it

Opened from a Copilot button above the reply box, on the right side of
the conversation.

<!-- VERIFY-LIVE: confirm the exact Ask Copilot button label and panel wording on the live tenant -->

### How to use it

1. Open the conversation you need help with.
2. Click the Copilot button above the reply box.
3. Type your question in the panel's chat box and send it.
4. Read the answer, along with the "Looked at" line noting which
   knowledge tools (for example, a knowledge-base search) it used to
   answer, and the "Sources" line underneath when it has one — click a
   source to open it, or read its title if it isn't linked.
5. Click "Insert into reply" on any answer you'd like to drop straight
   into your draft.
6. Reset the panel to start a fresh question thread, or close it when
   you're done.

[[SCREENSHOT: ch02-copilot-panel | Asking the Copilot panel a question and seeing its "Looked at" line]]

### Example scenario

While handling a report of a charging error code on an e.MAS 5, an agent
asks Copilot what the error code means and how it's usually resolved,
then inserts the grounded answer straight into their reply.

### Integrations & automation

Copilot draws on the same knowledge base and per-inbox assistant
configuration covered in the Knowledge chapter and the AI assistant
behaviour chapter; it's a feature an administrator can turn on or off per
tenant.

## Translate

### What it is

A one-click action that translates the customer's most recent message into
English and posts the translation as a private note — useful the moment a
customer writes in Malay, Tamil, Chinese, or any language you're not
confident reading, without leaving the conversation to look it up.

### Where to find it

A "Translate" button next to Copilot, above the reply box.

### How to use it

1. Open the conversation with the message you want translated.
2. Click "Translate" above the reply box.
3. Wait a moment — the translation appears as a new private note on the
   conversation. It isn't inserted into your reply box, because it isn't
   meant to be sent to the customer as-is.
4. Read the note, which also names the language it detected the message
   was written in, then reply to the customer yourself in whichever
   language fits.

### Example scenario

A customer writes an entire complaint in Bahasa Melayu about a delayed
delivery; the agent clicks Translate, reads the English private note a
moment later, and replies having understood the complaint precisely rather
than guessing at it.

### Integrations & automation

Translate only ever posts a private note — the same customer-safety rule
every other AI-assist action in this chapter follows, so nothing it
produces reaches the customer without an agent choosing to send it. It only
translates *into* English; there's no option here to translate an outgoing
reply into another language.

## FAQ suggestion strip

### What it is

A small, dismissible strip that can appear above the reply box showing the
single best-matching FAQ answer for the customer's latest message, with an
"Apply" button that drops the full answer straight into your reply.

### Where to find it

Above the reply box, alongside the other composer actions. It only appears
when the CRM has found a confident FAQ match for the latest customer
message — it will not appear on every message.

### How to use it

1. Watch for the strip after a new customer message arrives — it only
   shows up when a good match exists.
2. Read the suggested FAQ answer in the strip.
3. Click "Apply" to paste the full answer into your reply box, then edit
   and send it as normal.
4. Dismiss it if it doesn't fit the case. A dismissed suggestion for that
   particular message won't reopen, but a later customer message can still
   surface a fresh one.

### Example scenario

A customer asks a common question about charging cable compatibility; the
strip surfaces the matching FAQ answer immediately, the agent clicks Apply,
adjusts the greeting, and sends — without leaving the conversation to
search the Knowledge base by hand.

### Integrations & automation

The strip draws on the same knowledge base as Ask Copilot and Suggest-a-
reply (see the Knowledge chapter), but it only ever shows a suggestion when
the match is confident — a weak or uncertain hit stays silent instead of
handing you something that might be wrong.

## Suggest-a-reply
<!-- TRAINING: audience=agent, exercise -->

### What it is

A one-click AI-drafted reply for the conversation you're currently in,
based on the conversation so far and the knowledge base, placed directly
in the reply box for you to review before sending — with a "Sources"
line showing what it was grounded in. It also looks at any photo, video,
or voice note the customer has sent in the conversation, not just their
typed words.

### Where to find it

A "Suggest a reply" action above the reply box.

<!-- VERIFY-LIVE: confirm the exact Suggest-a-reply button label on the live tenant -->

### How to use it

1. Open the conversation.
2. Click "Suggest a reply" above the reply box.
3. Wait a moment for the draft to appear in the reply box. If the customer
   has sent a photo, video, or voice note, the draft takes that into
   account too — it can describe what's in a warning-light photo or a
   video without you having to ask the customer to explain it again.
4. Check the Sources line underneath the draft for the knowledge-base
   articles it drew on.
5. Edit the draft if needed, then send it like any other reply.

[[SCREENSHOT: ch02-suggest-reply | A suggested reply drafted in the reply box with its source citations]]

### Example scenario

A customer asks about the cancellation policy for a service booking; the
agent clicks Suggest a reply, receives a draft citing the relevant FAQ
article, adjusts the tone slightly, and sends it. In a different case, a
customer sends only a photo of a dashboard warning light with no
explanation; Suggest a reply still produces a draft, because the icon in
the photo — not just the customer's (absent) words — is what led the
knowledge-base search this time.

### Integrations & automation

Suggest-a-reply draws on the same knowledge base as Ask Copilot and the
AI auto-draft behaviour described below; its source citations link back
to content covered in the Knowledge chapter. When a conversation has media
attached, what's in that media — not only the customer's typed words —
leads the knowledge-base search, which is why it can still find the right
source article on a photo-only or voice-note-only turn.

## Summarize conversation

### What it is

Generates a short summary of the conversation so far — useful for a
quick catch-up or for handing a conversation off to another agent.

### Where to find it

A "Summarize" action above the reply box.

<!-- VERIFY-LIVE: confirm the exact Summarize button label on the live tenant -->

### How to use it

1. Open the conversation you want to summarize.
2. Click "Summarize" above the reply box.
3. Wait a moment for the summary to be generated.
4. The summary is inserted into the reply box as a draft, switched to
   private "Note" mode — it is not posted automatically, so review it and
   click send yourself to add it to the conversation.
5. Read it before handing the conversation to a teammate, or to refresh
   your own memory of a long-running case.

[[SCREENSHOT: ch02-summarize | Generating a conversation summary for a quick catch-up or handover]]

### Example scenario

Before going off shift, an agent summarizes a long-running roadside
assistance conversation so the next agent can immediately see the
vehicle's status and what's already been arranged.

### Integrations & automation

The summary is written in English regardless of the conversation's
original language, matching Proton's internal reporting standard.

## AI auto-draft and suggest-vs-auto mode

### What it is

The AI assistant can act on incoming customer messages on its own,
without an agent asking it to. It always works in one of two modes, set
per inbox by an administrator: **auto mode** — the tenant-wide default
today, unless an inbox has its own override — where it sends its reply
straight to the customer; or **suggest mode**, where it drafts a reply as
a private note and reopens the conversation for a human to review and
send.

### Where to find it

Not something an agent turns on or off — it's configured per inbox by an
administrator. Agents simply see its effects appear inside conversations.

### How to use it

1. On most inboxes today, the AI's reply is sent to the customer directly
   — that's auto mode, the tenant-wide default — and the conversation
   stays in **Pending** while the AI keeps handling it. It only moves to
   **Open** once the AI hands off or an agent steps in.
2. On an inbox an administrator has switched to suggest mode instead,
   watch for a private note marked as a suggested reply on a conversation
   the AI has just handled.
3. Read that suggested draft, edit it if needed, then send it yourself as
   a normal reply. The AI never sends a suggest-mode draft for you.
4. If the AI can't confidently help, it hands the conversation off to a
   human on its own — reopening it and, if configured, posting a brief
   acknowledgement to the customer first.

[[SCREENSHOT: ch02-ai-draft-note | An AI-drafted reply posted as a private note awaiting agent review]]

### Example scenario

On the tenant's default auto mode, a customer asks about the price of an
e.MAS X service package on WhatsApp; the AI answers directly, and the
conversation stays Pending until the AI hands off or an agent steps in.
On an inbox an administrator has switched to suggest mode instead, the
same kind of question produces a private-note draft that the on-duty agent
reviews, adjusts, and sends as their own reply.

### Integrations & automation

Suggest-vs-auto mode is a per-inbox setting administrators manage; when
the AI can't answer, it hands off to a human automatically. See the AI
assistant behaviour chapter for the full explanation of when it replies,
escalates, or hands off.

## Contact side panel

### What it is

A panel next to the conversation showing the customer's details, past
conversations, and notes, so you don't have to leave the conversation to
see who you're talking to.

### Where to find it

The right side of an open conversation. It now opens by default, rather
than needing to be expanded manually each time.

### How to use it

1. Open any conversation — the contact panel opens automatically.
2. Scroll through it to see contact details, conversation history, and
   any existing notes.
3. Collapse or expand it manually if you want more room for the
   conversation itself; your choice is remembered for next time.

[[SCREENSHOT: ch02-contact-panel | The contact side panel open next to a conversation]]

### Example scenario

While chatting with a returning EV owner about a second service booking,
an agent glances at the contact panel to confirm the vehicle model
already on file instead of asking the customer to repeat it.

### Integrations & automation

The contact panel is the entry point to the fuller contact profile and
Customer 360 lookup tools covered in the Contacts chapter.

## Resolving, snoozing & transcripts
<!-- TRAINING: audience=agent, exercise -->

### What it is

The actions that close out a conversation (**Resolved**), pause it for
later (**Snoozed**), or let you review and export its full message
history (**transcript**).

### Where to find it

Action buttons in the conversation header, and a menu option for
exporting the transcript.

<!-- VERIFY-LIVE: confirm exact resolve/snooze/transcript UI wording on the live tenant -->

### How to use it

1. Once the customer's issue is fully handled, click **Resolve**.
2. If you need to come back to a conversation later — for example,
   waiting on the customer or a dealer — click **Snooze** and choose
   when it should reopen.
3. A snoozed conversation reopens automatically at the chosen time, or
   immediately if the customer replies first.
4. To get a transcript of the conversation, open the conversation's menu
   and choose the transcript/export option.

[[SCREENSHOT: ch02-resolve-snooze | Resolving or snoozing a conversation]]

### Example scenario

A test-drive scheduling conversation is snoozed for two days while the
customer checks their calendar; it reopens automatically when they
reply, and once the drive is booked the agent marks it Resolved.

### Integrations & automation

Conversations can also close automatically due to inactivity timers an
administrator sets per inbox — an idle warning is posted first, then an
automatic close after a grace period (see the Administration chapter).
Resolving a conversation on any of the four live inboxes sends the
customer a CSAT satisfaction-rating request — that's the CRM's own,
native survey, and it's what the CSAT figures in the Reports chapter are
built from. This is a separate mechanism from the AI assistant's own
rating prompt (a "rate our AI assistant / support agent from 1 to 5"
message the AI itself can send as part of its own resolve flow, covered
in the AI assistant behaviour chapter) — that one is not currently
switched on for this tenant, so an agent shouldn't expect to see it fire.
The satisfaction request each customer actually receives on resolve is
the native CSAT one, not that one.
