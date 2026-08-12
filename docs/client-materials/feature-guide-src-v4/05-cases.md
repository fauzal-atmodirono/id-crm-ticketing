# Cases
<!-- TRAINING: audience=supervisor -->

## Case list
<!-- TRAINING: audience=supervisor, exercise -->

### What it is

The Cases list is a single filterable table that shows every conversation
as a "case" — Division, Concern, Purchased From, Escalated To, Agent, Car
Plate, Aging (in days), and Status — so a supervisor can scan the whole
book of work without opening conversations one at a time. It doesn't add
a separate case record: it reads the same conversations shown in the
Conversations chapter and displays their category, contact, assignment,
and status information as table columns. Conversations that predate this
vocabulary, or were never categorized, simply show a dash in the columns
that don't apply — an unassigned case shows a dash in the Agent column
the same way.

### Where to find it

**Cases** in the main sidebar. This page requires the same permission as
Customer 360 (see the Contacts chapter) — an administrator who hasn't
been granted that permission won't see it.

### How to use it

1. Open **Cases** from the sidebar.
2. Use the Division, Case type, Status, Channel, and Dealer filters at
   the top to narrow the list, or use **Reset filters** to clear them.
3. Read the table: Case ID, Division, Concern, Purchased From, Escalated
   To, Agent, Car Plate, Aging (days), and Status. **Agent** shows the
   name of whoever the underlying conversation is assigned to, or a dash
   if it's unassigned.
4. Click a Case ID to open the underlying conversation.
5. If a banner appears saying the list is showing only the first N of a
   larger total, the list has reached its display limit — the filters
   and totals shown no longer reflect every case in the account.

[[SCREENSHOT: ch05-case-list | The Cases list page, including the Agent column]]

### Example scenario

A supervisor filters the Cases list to the Aftersales division and the
Complaint case type to see every open aftersales complaint across all
dealers at a glance, then sorts by Aging to prioritize the oldest ones
first — glancing at the Agent column to see, without opening a single
conversation, which of the oldest cases don't have anyone assigned yet.

### Integrations & automation

The Division, Concern, Case type, Channel, and Dealer values shown here
come from the same conversation categorization covered in Case
categories (below) — the AI assistant sets these automatically while
handling a conversation (see the AI Assistant Behaviour chapter), and an
agent can correct them the same way. Car Plate and Purchased From come
from the customer's own contact record, and Agent simply mirrors the
conversation's own assignee (see the Conversations chapter's Assignment
& teams section). **The Agent column is a Cases-list-only addition** — it
does not (yet) appear on the conversation card in the regular
Conversations inbox view, so if you need to see who owns a case at a
glance outside of Cases, open the conversation itself.

## Case categorisation (five fields)
<!-- TRAINING: audience=agent, exercise -->

### What it is

**New since the last edition of this guide.** Every conversation can now
carry five separate case-categorisation fields, each a single-select
dropdown in the conversation's custom attributes panel, matching the
client's RFP taxonomy exactly (RFP 2026_028, Appendix A):

| Field | What it is | RFP tier | Values | Example |
|---|---|---|---|---|
| **Case Type** | Inquiry, Complaint, or Compliment & Feedback | — | 3 | `Compliment & Feedback` |
| **Case Category** | Division | Division | 8 | `Sales` |
| **Case Subcategory** | Level 1 | Level 1 | 89 | `Sales: Delivery` |
| **Case Detail** | Level 2 (with Level 3/4 folded in) | Level 2–4 | 246 | `Sales: Delivery: No Estimated Time Delivery` |
| **Vehicle Model** | Which vehicle the case concerns | — | 4 | `e.MAS 5` |

**Only three of these five cascade together**: Case Category → Case
Subcategory → Case Detail. Picking a Case Category narrows the Case
Subcategory list to only that division's Level 1 values; picking a Case
Subcategory narrows Case Detail the same way one level further. Changing
a value anywhere in that chain clears anything below it that no longer
matches, the same "clears a stale child" behaviour the previous edition
of this guide described for category/subcategory alone. **Case Type and
Vehicle Model are independent, ordinary dropdowns** — they don't filter
each other or the category chain, and picking one has no effect on any
of the other four fields.

Case Subcategory and Case Detail values are **prefixed with their full
parent path**, not just their own name, because the same Level 1 (or
Level 2) name recurs under different divisions with different meanings —
for example `Refund` exists under both **Sales** and **Charging**, and
`Booking` under both **Sales** and **Charging** too. Without the prefix
those would be indistinguishable in the dropdown. So Case Subcategory
reads `<Division>: <Level 1>` (e.g. `Sales: Delivery`), and Case Detail
reads `<Division>: <Level 1>: <Level 2>` (e.g. `Sales: Delivery: No
Estimated Time Delivery`). Where the RFP source row also has a Level 3
and/or Level 4, those are folded into that *same* Case Detail value —
never a sixth or seventh dropdown — appended with an em dash (`—`), in
order: `Sales: Refund: Booking — Status — Dealer Refund`. Not every Level
1 has a Level 2 in the source RFP; where it doesn't, there's simply no
Case Detail option for it and Case Subcategory alone is as specific as
that case gets.

**Case Detail is not set automatically.** The AI assistant can classify
Case Category, Case Subcategory, Case Type, and Vehicle Model on its own
(see Integrations & automation, below) — but nobody has wired an
automatic classifier for Case Detail, the most granular field. Expect to
always pick it by hand.

### Where to find it

The conversation's custom attributes panel, alongside the conversation
(the same panel used for any other custom attribute). Case Type and
Vehicle Model are new rows in that same panel — before this edition,
neither had a custom-attribute definition at all, so those two dropdowns
didn't render there yet.

Administrators also see a separate **Case Taxonomy** page in the
sidebar, new since the last edition, for managing where Case Category,
Case Subcategory, and Case Detail's option lists themselves come from —
see Integrations & automation, below.

<!-- VERIFY-LIVE: confirm the exact custom attributes panel label and location on the live tenant, and the exact Case Taxonomy page wording -->

### How to use it

1. Open the conversation you want to categorize, and open its custom
   attributes panel.
2. Choose a value for **Case Type** — `Inquiry`, `Complaint`, or
   `Compliment & Feedback`. This doesn't affect any other field.
3. Choose a value for **Case Category** (the division — for example
   `Sales`, `Aftersales`, `Charging`, `Apps`, `Product`, `Network`,
   `Marketing`, or `Others`).
4. Choose a value for **Case Subcategory** — only Level 1 values
   belonging to the division you just picked are offered, each shown
   with its `<Division>: ` prefix.
5. Choose a value for **Case Detail** — only Level 2 (and folded
   Level 3/4) values belonging to the subcategory you just picked are
   offered, each shown with its full `<Division>: <Level 1>: ` prefix.
   If the subcategory has no Level 2 in the source taxonomy, this list
   is empty and there's nothing further to pick — that's expected, not
   an error.
6. Choose a value for **Vehicle Model** if the case concerns a specific
   vehicle (`e.MAS 5`, `e.MAS 7`, `e.MAS 7 PHEV`) or `Not Applicable`
   otherwise. This also doesn't affect any other field.
7. Changing **Case Category** after Case Subcategory/Case Detail are
   already set clears both, since they no longer match the new division
   — reselect them from the narrowed lists. The same happens one level
   down if you only change Case Subcategory.
8. Save, or move on — most CRM attribute panels save automatically as
   soon as a value is picked.

[[SCREENSHOT: ch05-case-categories | The Case Category / Case Subcategory / Case Detail cascade, narrowed by division and then by Level 1]]

[[SCREENSHOT: ch05-case-type-vehicle-model | The independent Case Type and Vehicle Model dropdowns, new to this edition]]

### Example scenario

A customer emails complaining that their new e.MAS 5's delivery has no
estimated date. The agent sets **Case Type** to `Complaint`, **Case
Category** to `Sales`, which narrows **Case Subcategory** to Sales'
Level-1 list, where they pick `Sales: Delivery`; that narrows **Case
Detail** to Delivery's Level-2 list, where they pick `Sales: Delivery: No
Estimated Time Delivery`; and **Vehicle Model** to `e.MAS 5`. The case
now shows up correctly filtered in the Cases list, and counts toward the
Sales division in reporting at exactly the granularity the client's RFP
Appendix A specifies.

### Integrations & automation

**New since the last edition of this guide:** Case Category, Case
Subcategory, and Case Detail's option lists are now administered from
the dedicated **Case Taxonomy** page (see Where to find it, above),
reachable only to administrators granted the case-taxonomy permission —
not every administrator has it. There, an administrator builds the
division/subcategory/detail tree entry by entry, giving each one a
label and, optionally, the escalation department it belongs to. Saving
a change updates the dropdown values agents see immediately, with no
restart or redeploy. A value is never permanently deleted, only
**retired** — a case created before a value was retired keeps showing
it correctly, and retiring a value doesn't retire anything beneath it
in the tree, so the page walks the administrator through re-homing or
retiring those separately rather than leaving them invisible by
accident.

Because the Case Taxonomy page treats its own list as the complete,
authoritative one and rewrites it in full every time an administrator
saves or retires a node there, **don't add or edit Case Category, Case
Subcategory, or Case Detail values from the general Custom Attributes
settings page anymore** (see the Administration chapter) — a value
added that way can be silently overwritten the next time anyone uses
the Case Taxonomy page. Case Type and Vehicle Model aren't part of this
taxonomy tree and have no dedicated admin page of their own; keep
provisioning those two as flat option lists under Administration →
Custom Attributes, same as before, sourced from RFP 2026_028 Appendix A.

Case Category, Case Subcategory, Case Type, and Vehicle Model are also
set automatically by the AI assistant when it classifies an incoming or
just-resolved conversation (see the AI Assistant Behaviour chapter) —
an agent reviewing the case can always correct a misclassification
here, the same as before. Case Detail is never set by the AI; it's
always an agent's own pick. These five values feed the Cases list
(above) and the Departments & PIC and Case Lifecycle reports (see the
Reports chapter).

## Case lifecycle & status
<!-- TRAINING: audience=agent -->

### What it is

A case doesn't have a lifecycle of its own — its Status column simply
shows the underlying conversation's status (for example Open, Pending,
Snoozed, or Resolved). Resolving, reopening, or snoozing the conversation
does the same thing to the case; there's no separate case state to keep
in sync.

### Where to find it

The Status column in the Cases list; the same status is also shown and
changed on the conversation itself (see the Conversations chapter).

### How to use it

1. In the Cases list, use the Status filter to see cases in a particular
   state (for example, only Open cases).
2. Open a case's conversation (click its Case ID) to change its status —
   resolve it, snooze it, or reopen it, exactly as covered in the
   Conversations chapter.
3. Return to the Cases list (or refresh it) to see the Status column
   reflect the change.
4. Watch the Aging (days) column alongside Status — it counts days since
   the conversation was created regardless of status, so a case can be
   both "old" and already resolved.

[[SCREENSHOT: ch05-case-lifecycle | A case moving through its lifecycle statuses]]

### Example scenario

A warranty complaint case sits with Status "Open" for several days while
the dealer investigates. Once the dealer confirms the repair, the agent
resolves the conversation from the conversation view; the next time
anyone opens the Cases list, that same case now shows Status "Resolved".

### Integrations & automation

Because a case's status is just its conversation's status, anything that
changes conversation status elsewhere — an agent action, an automation
rule, or the AI assistant resolving a conversation it handled — is
reflected here automatically, with nothing to configure specifically for
Cases.

## How cases relate to conversations
<!-- TRAINING: audience=agent -->

### What it is

There is no separate "case" record behind the scenes — every conversation
is a case. The Cases list is simply a different view of the same
conversations shown in the Conversations chapter, built from each
conversation's category/subcategory, labels, status, and contact
information.

### Where to find it

Anywhere a conversation lives: the Conversations view, the conversation's
custom attributes panel, and the contact's own record all feed what the
Cases list displays.

### How to use it

1. Treat categorizing a conversation (see Case categories, above) as the
   same action as categorizing its case — there's nothing extra to do.
2. Make sure the contact's vehicle number and dealer/purchase details are
   filled in on their profile (see the Contacts chapter), since the Cases
   list's Car Plate and Purchased From columns are read from there.
3. Once a conversation has these details, it appears correctly in the
   Cases list the next time the list loads — no separate publishing step.

[[SCREENSHOT: ch05-case-conversation-link | A case category set on a conversation]]

### Example scenario

Several different customers each report the same software update problem
in separate conversations, on separate days, through different channels.
Because every agent categorizes their conversation under the same case
category and subcategory, a supervisor filtering the Cases list by that
category sees every affected customer's case together in one place, even
though each one started as an unrelated conversation with a different
contact.

### Integrations & automation

Because a case is a conversation, everything that already applies to
conversations — labels, escalation emails, AI auto-drafted replies (see
the Conversations and AI Assistant Behaviour chapters) — applies to its
case as well, with no separate synchronization step between the two.

## Escalation status on a case
<!-- TRAINING: audience=agent -->

### What it is

A case that's been escalated by email carries the same labels its
underlying conversation does, so a supervisor scanning the Cases list can
tell an escalation's state without opening it: whether it's simply been
escalated (`escalate`), whether the dealer or PIC has since replied
(`escalation_replied`), and which dealer it went to (the `Escalated To`
column, and any `dealer_<slug>` label).

### Where to find it

The **Escalated To** column in the Cases list, and the conversation's own
labels once you open it — see the Conversations chapter's Labels and
Escalation replies sections for the full mechanics.

### How to use it

1. In the Cases list, look at **Escalated To** for cases that have
   already been forwarded somewhere.
2. Open a case whose escalation you want to check on. If the dealer or
   PIC has replied, the conversation carries the `escalation_replied`
   label and two private notes — the dealer's reply and an AI-drafted
   customer reply waiting for review (see the Conversations chapter's
   Escalation replies section for what those look like and how to act on
   them).
3. If a case shows `escalate` but no `escalation_replied` after a
   reasonable wait, that's your signal to follow up with the dealer/PIC
   directly rather than assume the CRM will surface a reply that hasn't
   arrived yet.
4. Use the Agent column (above) alongside this to see who's responsible
   for following up on a stalled escalation.

[[SCREENSHOT: ch05-case-list | Escalation labels visible on rows in the Cases list]]

### Example scenario

A supervisor reviewing the Cases list at the end of the week filters to
cases carrying `escalate` and sorts by Aging, to spot any escalated case
that's been open more than three days without an `escalation_replied`
label — a sign worth chasing up with the dealer directly rather than
waiting on the CRM.

### Integrations & automation

This is the Cases-list view of the same escalation mechanics covered in
the Conversations chapter (Labels, Escalation replies) and the AI
Assistant Behaviour chapter (Escalation labels & the escalation email);
nothing here needs separate configuration.
