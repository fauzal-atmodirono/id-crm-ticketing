# Cases

## Case list

### What it is

The Cases list is a single filterable table that shows every conversation
as a "case" — Division, Concern, Purchased From, Escalated To, Car Plate,
Aging (in days), and Status — so a supervisor can scan the whole book of
work without opening conversations one at a time. It doesn't add a
separate case record: it reads the same conversations shown in the
Conversations chapter and displays their category, contact, and status
information as table columns. Conversations that predate this vocabulary,
or were never categorized, simply show a dash in the columns that don't
apply.

### Where to find it

**Cases** in the main sidebar. This page requires the same permission as
Customer 360 (see the Contacts chapter) — an administrator who hasn't
been granted that permission won't see it.

### How to use it

1. Open **Cases** from the sidebar.
2. Use the Division, Case type, Status, Channel, and Dealer filters at
   the top to narrow the list, or use **Reset filters** to clear them.
3. Read the table: Case ID, Division, Concern, Purchased From, Escalated
   To, Car Plate, Aging (days), and Status.
4. Click a Case ID to open the underlying conversation.
5. If a banner appears saying the list is showing only the first N of a
   larger total, the list has reached its display limit — the filters
   and totals shown no longer reflect every case in the account.

[[SCREENSHOT: ch05-case-list | The Cases list page]]

### Example scenario

A supervisor filters the Cases list to the Aftersales division and the
Complaint case type to see every open aftersales complaint across all
dealers at a glance, then sorts by Aging to prioritize the oldest ones
first.

### Integrations & automation

The Division, Concern, Case type, Channel, and Dealer values shown here
come from the same conversation categorization covered in Case
categories (below) — the AI assistant sets these automatically while
handling a conversation (see the AI Assistant Behaviour chapter), and an
agent can correct them the same way. Car Plate and Purchased From come
from the customer's own contact record.

## Case categories

### What it is

Every conversation can carry a case category and a case subcategory —
for example, category **Aftersales** with subcategory **Aftersales:
Service Operation**. The subcategory list narrows to only the
subcategories that belong to the category you've picked, so you can't
accidentally pair a category with a subcategory from a different one;
changing the category clears a subcategory that no longer matches.

### Where to find it

The conversation's custom attributes panel, alongside the conversation
(the same panel used for any other custom attribute).

<!-- VERIFY-LIVE: confirm the exact custom attributes panel label and location on the live tenant -->

### How to use it

1. Open the conversation you want to categorize.
2. Open the custom attributes panel.
3. Choose a value for **Case category** (for example, Sales, Aftersales,
   Charging, Apps, Product, Marketing, or Others).
4. Choose a value for **Case subcategory** — only the subcategories that
   belong to the category you just chose are offered.
5. Save, or move on — most CRM attribute panels save automatically as
   soon as a value is picked.

[[SCREENSHOT: ch05-case-categories | Choosing a case category and subcategory]]

### Example scenario

A customer messages about a faulty home charger. The agent sets the
conversation's case category to **Charging** and its subcategory to
**Charging: Home Charging**, so the case shows up correctly filtered in
the Cases list and counts toward the Charging division in reporting.

### Integrations & automation

The list of available categories and subcategories is defined by an
administrator under the Administration chapter's Custom Attributes
section. Category/subcategory values are also set automatically by the
AI assistant when it classifies an incoming conversation (see the AI
Assistant Behaviour chapter) — an agent reviewing the case can always
correct a misclassified category or subcategory here. These values feed
the Cases list (above) and the Departments & PIC and Case Lifecycle
reports (see the Reports chapter).

## Case lifecycle & status

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
