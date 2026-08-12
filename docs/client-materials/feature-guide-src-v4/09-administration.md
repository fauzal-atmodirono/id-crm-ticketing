# Administration (Settings)
<!-- TRAINING: audience=admin -->

This chapter covers the pages an administrator uses to configure the CRM
itself, rather than to handle a customer conversation. Most of these pages
require an administrator account; the Proton-specific pages that sit in the
main left-hand navigation — SLA Policies, Audit Log, Roles & Permissions,
Escalation Routing, Integrations, Case Taxonomy, Customer 360, Cases and the
RSA Incident Log — additionally require a specific permission
to be granted to your role. If you don't see one of these items in the
left-hand navigation, ask an administrator to grant you the matching
permission from **Roles & Permissions**. One entry in that list works the
other way round: **My status** is deliberately available to ordinary agents,
because setting your own availability is part of an agent's own working day.

## Agents
<!-- TRAINING: audience=admin, exercise -->

### What it is

The list of everyone who can log in to this CRM as staff, together with
their role (agent or administrator) and which teams/inboxes they belong to.

### Where to find it

Open **Settings** (the gear icon) and select **Agents**.

<!-- VERIFY-LIVE: confirm exact Agents settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Agents** to see everyone with access to this CRM.
2. To add someone, click **Add Agent**, enter their name and email, and pick
   a role (**Agent** or **Administrator**). An invitation is sent to that
   email address.
3. To change someone's role or the inboxes/teams they can see, click on
   their row and update the settings, then save.
4. To remove someone's access, open their row and choose the remove/deactivate
   option, then confirm.

<!-- VERIFY-LIVE: confirm exact Agents settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-agents | The Agents settings page]]

### Example scenario

Proton hires a new customer-service agent, Sari, to cover WhatsApp and
email support. An administrator adds her under **Settings → Agents** with the
**Agent** role, then assigns her to the **After-Sales** team and the relevant
inboxes so conversations can be routed to her.

### Integrations & automation

An agent's role here is what **Roles & Permissions** (below) grants extra
permissions on top of, and which teams they can be part of under **Teams**.

## Teams

### What it is

A named group of agents (for example, one team per department) used to
route and assign conversations as a group rather than to individuals one by
one.

### Where to find it

Open **Settings** (the gear icon) and select **Teams**.

<!-- VERIFY-LIVE: confirm exact Teams settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Teams** to see the existing teams.
2. To add a team, click **Add Team**, give it a name and description, and
   choose whether conversations assigned to the team round-robin between its
   members automatically.
3. Add or remove agents from the team on its detail page.
4. To retire a team, open it and use the remove/delete option, then confirm.

<!-- VERIFY-LIVE: confirm exact Teams settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-teams | The Teams settings page]]

### Example scenario

Proton organizes its support desk into **Sales**, **After-Sales**, and
**RSA** teams so that a complaint about a service appointment can be assigned
to the After-Sales team as a whole, and picked up by whichever of its agents
is free, instead of waiting for one named person.

### Integrations & automation

Teams are used as assignment targets in **Automation** rules below, and as a
grouping in reports (see the Reports chapter's Departments & PIC report).

## Inboxes (incl. inactivity timing)
<!-- TRAINING: audience=admin, exercise -->

### What it is

An inbox is one connected communication channel — a WhatsApp number, an
email address, the website widget, and so on. Each inbox has its own
settings: which agents can see it, its working hours, whether it sends the
CRM's own greeting and satisfaction-survey messages, and (Proton additions)
how long an idle conversation waits before the customer is warned and the
conversation is auto-closed, plus which channel each agent handles first.

Three of these are worth calling out because they are the switches operators
most often assume are somewhere else:

- **The greeting message is a per-inbox setting here**, not part of the AI
  assistant's own wording. It is the acknowledgement a customer gets when
  they first write in.
- **The satisfaction-rating request sent when a conversation is resolved is
  also a per-inbox setting here** — again separate from the AI assistant's
  own rating prompt, which is a different message configured in a different
  place (see the Knowledge chapter).
- **Agent channel priorities are edited on this page too**, on the
  collaborators tab of an individual inbox, even though what they steer is
  automatic assignment across the account.

### Where to find it

Open **Settings** (the gear icon) and select **Inboxes**, then choose the
inbox you want to configure.

<!-- VERIFY-LIVE: confirm the exact tab names that host business hours / inactivity settings and the collaborators/agent-priorities table on the live tenant -->

### How to use it

1. Open **Settings → Inboxes** and select the inbox to configure.
2. Use the inbox's general settings to control its name and the
   agents/teams assigned to it, and to switch the **greeting message** and
   the resolution **satisfaction survey** on or off and set their wording.
3. Open the tab that hosts business hours to set working hours for the
   inbox.
4. On the same tab, scroll to **Inactivity & auto-close**. Toggle **Enable
   inactivity & auto-close for this inbox** on or off.
5. Set **Warn after idle (min)**, **Close grace — in business hours (min)**,
   **Close grace — out of hours (min)**, and **Resolution-confirm grace
   (min)**. Leave any of these blank to inherit the tenant-wide default
   instead of setting a per-inbox value.
6. Optionally customize the wording of the **Idle warning message**, **Chat
   closed message**, **Resolution prompt**, **Assign-to-agent message**, **AI
   rating survey**, **Agent rating survey**, and **Thank-you message** sent
   to the customer at each stage. Leave a field blank to use the default
   wording. Note that the last three of those seven — the two rating surveys
   and the thank-you — belong to the AI assistant's own survey step, which is
   **switched off account-wide today**, so wording entered there is stored
   but nothing sends it. The first four are live.
7. Click **Update** to save — this single action saves both the business
   hours and the inactivity-timing settings together.
8. To set which channel an agent picks up first, open the inbox's
   **collaborators** tab and use the **Agent Channel Priorities** table (see
   below).

**Agent Channel Priorities.** The table lists every agent with their current
availability, one **Primary** channel, and any number of **Also handles**
channels, chosen from WhatsApp, Call, Email, Social and Web. Primary is
always first in an agent's priority order, so the chip for an agent's current
primary channel is deliberately not selectable as an extra — a channel cannot
be both. A **Save** button appears on a row only once you have actually
changed it; a row you have saved shows a **Saved** tick and an untouched row
shows a dash, so at a glance you can see which rows still hold unsaved work.
The table is account-wide even though it is reached through one inbox: the
priorities you set here steer automatic assignment everywhere (see the
Conversations chapter).

**Where each per-inbox switch stands today**, so nobody plans around the
wrong one:

- The **greeting message** is on for the WhatsApp and Email inboxes and off
  for the API and website-demo inboxes.
- The **resolution satisfaction survey** is on for all four inboxes — this
  is the rating request customers actually receive when a conversation is
  resolved.
- The **idle warning, automatic close and "is your case resolved?" prompt**
  are live account-wide.

[[SCREENSHOT: ch09-agent-priorities | The Agent Channel Priorities table on an inbox's collaborators tab, with one edited row showing its Save button]]

### Example scenario

Proton's WhatsApp inbox is set to warn a customer after 10 minutes of no
reply ("Warn after idle (min)"), then auto-close 5 minutes later during
business hours, but wait longer overnight when no agent is on duty — set via
a larger out-of-hours grace value on the same inbox. On the same inbox's
collaborators tab, an administrator sets Sari's **Primary** channel to
WhatsApp and ticks Email under **Also handles**, so she is offered WhatsApp
work first and email only when WhatsApp is quiet; the row shows a Save
button until she clicks it, then a Saved tick.

### Integrations & automation

The inactivity timers work together with the AI assistant's lifecycle
messages (see the AI Behaviour chapter) to keep conversations moving without
an agent having to manually chase or close every idle chat — and where both
are set, the wording entered on this page wins over the assistant's own. The
greeting and satisfaction-survey switches on this page are the CRM's own,
and run whether or not the AI assistant's equivalent messages are enabled;
the two are easy to confuse and are covered side by side in the Knowledge
chapter. Agent channel priorities feed the automatic assignment described in
the Conversations chapter, and an agent's availability — the status shown
beside their name in that table — is what decides whether they are offered
new work at all (see **Agent Availability & My Status**, below).

## Labels

### What it is

Labels are short, colour-coded tags administrators define for use across
conversations — including the `escalate` label and the dealer labels that
drive escalation notifications (see the Conversations and AI Behaviour
chapters).

### Where to find it

Open **Settings** (the gear icon) and select **Labels**.

<!-- VERIFY-LIVE: confirm exact Labels settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Labels** to see every label defined for the account.
2. Click **Add Label**, give it a name, description, and colour, then save.
3. To change a label's name, description, or colour, click on it and update
   the fields.
4. To remove a label, use its delete option and confirm. This removes the
   label from any conversation it was applied to.

<!-- VERIFY-LIVE: confirm exact Labels settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-labels | The Labels settings page]]

### Example scenario

An administrator creates a `dealer_jakarta-selatan` label so that
conversations escalated to that dealer can be tagged consistently by agents
and picked up automatically by the escalation routing described later in
this chapter.

### Integrations & automation

Labels drive automation rules (below), the escalation email flow, and
dealer-turnaround reporting (see the Reports chapter).

## Custom Attributes
<!-- TRAINING: audience=admin, exercise -->

### What it is

Custom Attributes let administrators define extra fields on conversations
or contacts beyond what the CRM ships with by default — for example, the
five case-categorisation fields used by Cases (see the Cases chapter's
Case categorisation section): **Case Type**, **Case Category**, **Case
Subcategory**, **Case Detail**, and **Vehicle Model**, all rendered as
single-select ("List") conversation attributes. **Case Type and Vehicle
Model are new since the last edition of this guide** — before this
edition, neither had a definition here at all, so neither dropdown
rendered in the conversation sidebar. Case Category/Case Subcategory/Case
Detail values are matched to the client's RFP 2026_028 Appendix A
taxonomy exactly, including Case Detail's em-dash convention for folding
in the RFP's Level 3/4 rows (see the Cases chapter for the full
explanation) — these lists are large (246 Case Detail values alone) and
are provisioned from that source file, not hand-typed one at a time.

> **This page is no longer where three of those five are maintained, and
> the previous edition of this guide said otherwise.** Case Category, Case
> Subcategory and Case Detail are now owned by the separate **Case
> Taxonomy** admin page (see the Cases chapter, which explains its
> four-tier structure and the trap in its tier names). That page treats its
> own tree as the complete, authoritative list and rewrites all three option
> lists in full every time anyone saves or retires a node on it. So a value
> you add to any of those three from this page can be silently overwritten
> the next time an administrator touches Case Taxonomy — no error, no
> warning, the value is simply gone. Add and edit those three on the Case
> Taxonomy page instead.
>
> **Case Type and Vehicle Model are not affected.** Neither is part of the
> taxonomy tree, neither is touched by that page's sync, and neither has an
> admin page of its own — they stay here, as ordinary List attributes, and
> editing them here is correct.

### Where to find it

Open **Settings** (the gear icon) and select **Custom Attributes**.

<!-- VERIFY-LIVE: confirm exact Custom Attributes settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Custom Attributes**.
2. Click **Add Custom Attribute**, choose whether it applies to
   conversations or contacts, give it a name and a type (text, number, list,
   checkbox, etc.), and save.
3. To edit an attribute's options, click on it and update the fields.
   **Do not edit Case Category, Case Subcategory or Case Detail here** —
   use the Case Taxonomy page (see the box above, and the Cases chapter).
   Case Type and Vehicle Model are edited here as normal, though treat even
   those as occasional rather than routine maintenance: both are matched
   one-for-one against the RFP source, and a hand edit here won't update
   that source file, so the two can drift apart.
4. To remove an attribute that is no longer needed, use its delete option and
   confirm.

<!-- VERIFY-LIVE: confirm exact Custom Attributes settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-custom-attributes | The Custom Attributes settings page]]

### Example scenario

Proton's five case-categorisation fields (used on the Cases page) are
defined here as conversation-level custom attributes, so any conversation
can be tagged with the same structured Case Type/Category/Subcategory/
Detail/Vehicle Model values used in Case reporting. An administrator asked
to add a new vehicle model adds it here; an administrator asked to add a new
case category adds it on the Case Taxonomy page instead, and the value
reaches this page automatically.

### Integrations & automation

Custom attributes back the Cases feature's five categorisation fields (see
the Cases chapter) and can also be used as conditions in Automation rules
below. Three of the five — Case Category, Case Subcategory and Case Detail —
have the Case Taxonomy page as their source of truth and are written into
this page's definitions from there; the other two are maintained here
directly.

## Automation

### What it is

Automation lets administrators define "when X happens, do Y" rules that run
without an agent having to act — for example, applying a label automatically
when a conversation matches certain conditions.

### Where to find it

Open **Settings** (the gear icon) and select **Automation**.

<!-- VERIFY-LIVE: confirm exact Automation settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Automation** to see the existing rules.
2. Click **Add Automation Rule**, choose an event to trigger on (for
   example, a conversation being created), add one or more conditions, and
   choose the action(s) to run (add a label, assign a team, send a message,
   etc.).
3. Save the rule. It applies to matching conversations going forward.
4. To adjust a rule, click on it and edit its conditions or actions.
5. To stop a rule from running, disable or delete it.

<!-- VERIFY-LIVE: confirm exact Automation settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-automation | The Automation settings page]]

### Example scenario

An administrator sets up a rule that automatically applies a "Priority:
High" label whenever a new conversation arrives on the RSA (roadside
assistance) inbox, so those conversations stand out in the inbox view
without an agent tagging them by hand.

### Integrations & automation

Automation rules can use labels and custom attributes (both covered above)
as conditions, and can trigger the same kinds of actions an agent could take
manually from a conversation.

## Macros

### What it is

Macros bundle a sequence of actions — such as adding a label, assigning a
team, sending a reply, and resolving — into one saved, one-click action.
This page is where those macro steps are created and edited; running a
saved macro from an open conversation is covered in the Conversations
chapter.

### Where to find it

Open **Settings** (the gear icon) and select **Macros**.

<!-- VERIFY-LIVE: confirm exact Macros settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Macros** to see the existing macros.
2. Click **Add Macro**, give it a name, and add the steps it should run in
   order (for example: apply a label, then send a reply, then resolve).
3. Save the macro. It becomes available to agents from the macro menu inside
   any open conversation.
4. To change a macro's steps, click on it, edit the step list, and save.
5. To retire a macro, delete it — this does not affect conversations it was
   already run on.

<!-- VERIFY-LIVE: confirm exact Macros settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-macros | The Macros settings page]]

### Example scenario

An administrator builds a "Test drive confirmed" macro that applies a
`test-drive` label, sends a confirmation reply, and marks the conversation
resolved, so agents handling this routine request can finish it in one
click instead of three separate steps (see the Conversations chapter for
the agent-side view of running this macro).

### Integrations & automation

Macros created here are run by agents from the conversation view (see the
Conversations chapter's Macros section) — this page is the admin-only
counterpart to that agent-facing feature.

## Canned Responses

### What it is

Canned Responses are reusable message templates agents can insert into a
reply instead of typing the same answer out each time.

### Where to find it

Open **Settings** (the gear icon) and select **Canned Responses**.

<!-- VERIFY-LIVE: confirm exact Canned Responses settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Canned Responses** to see the existing templates.
2. Click **Add Canned Response**, give it a short code/shortcut and the full
   message text, then save.
3. To edit a template's text or shortcut, click on it and update the fields.
4. To remove a template that is no longer needed, use its delete option and
   confirm.

<!-- VERIFY-LIVE: confirm exact Canned Responses settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-canned-responses | The Canned Responses settings page]]

### Example scenario

An administrator creates a canned response for the standard warranty
explanation on the e.MAS 7, so every agent can insert the same accurate
wording instead of retyping it for each customer who asks.

### Integrations & automation

Canned responses are inserted by agents from the reply box during a
conversation (see the Conversations chapter); this page controls what is
available to insert.

## Integrations (incl. DMS / TSP connection)

### What it is

This covers two related areas: the native **Settings → Integrations** page
for connecting third-party apps and webhooks to the CRM, and a separate,
permission-gated **Integrations** page in the main left-hand navigation
where the Dealer Management System / Telematics Service Provider (DMS/TSP)
connection is configured. The DMS/TSP connection is what lets the Customer
360 lookup (see the Contacts chapter) show a customer's vehicle and service
history.

### Where to find it

For general integrations/webhooks, open **Settings** (the gear icon) and
select **Integrations**. For the DMS/TSP connection, look for **Integrations**
in the main left-hand navigation (visible only if your role has been granted
the "Manage DMS/TSP integration settings" permission — see **Roles &
Permissions** below), then open the **DMS / TSP** card.

<!-- VERIFY-LIVE: confirm exact native Settings -> Integrations UI wording on the live tenant -->

### How to use it

1. From the main left-hand navigation, open **Integrations**, then click the
   **DMS / TSP** card. Its status badge shows **Checking status…** while it
   loads, then **Not connected**, **Enabled**, or **Status unavailable** —
   it never claims to be actively connected, since that can only be
   confirmed with the **Test connection** button described below. Even an
   **Enabled** badge carries a caption saying reachability has not been
   verified.
2. Check **Enabled** to turn the connection on.
3. Fill in a **Provider label** (a friendly name for your own reference),
   the **Auth type** (Bearer token, Basic auth, or API key header — the
   third option names the header it sends), and the **Base URL** of the
   DMS/TSP system (must start with `https://`).
4. Click **Replace** next to **Credential** to enter the API key/secret for
   this connection. Once saved, the credential always shows as masked
   (`••••••••`) and is never displayed again — click **Replace** again later
   to change it.
5. Optionally set an **Extra header name** and **Extra header value** if the
   DMS/TSP system requires an additional custom header. Unlike Credential,
   the extra header value is stored and shown in plain text, so avoid putting
   a second secret there.
6. Set the **Timeout (seconds)** the Customer 360 lookup should wait for a
   response before giving up and showing CRM data only (between 0.1 and 30
   seconds).
7. Click **Save**, then click **Test connection** to confirm the saved
   configuration can actually reach the DMS/TSP system. **Test connection
   runs against the saved configuration, not what is on screen**, so save
   first if you have just edited the form. A successful test reports
   **Reachable**; a failure names the reason — authentication failed, timed
   out, an unexpected response, or not configured yet.

[[SCREENSHOT: ch09-integrations | Configuring the DMS / TSP connection under Integrations]]

### Example scenario

Proton's IT team receives API credentials from its DMS provider ahead of a
pilot. An administrator with the integration-management permission opens
**Integrations → DMS / TSP**, enables the connection, enters the base URL and
credential, saves, and clicks **Test connection** to confirm it returns
**Reachable** before telling the After-Sales team that vehicle history will
now appear in Customer 360.

### Integrations & automation

Once enabled and reachable, this connection feeds the DMS/TSP vehicle and
service-history section of the Customer 360 lookup (see the Contacts
chapter). It has no effect anywhere else in the CRM until it is enabled.

## SLA Policies
<!-- TRAINING: audience=supervisor, exercise -->

### What it is

SLA Policies define the response and resolution time targets the CRM
tracks per conversation — either a single tenant-wide default, or an
override for a specific inbox — plus two related timing settings: how
long an unresolved breach waits before re-alerting a second time
(**Tier-2**), and how much advance warning a case gets before it actually
breaches. These targets are also what the always-on SLA breach engine
checks a conversation against: when a case on the Email inbox goes past
its target, the CRM posts a private note on it and emails the department's
PIC group — see the Conversations chapter's SLA breach alerts section for
what that looks like from an agent's side.

### Where to find it

In the main left-hand navigation, select **SLA Policies** (visible only if
your role has been granted the "Manage SLA policies" permission — see
**Roles & Permissions** below).

### How to use it

1. Open **SLA Policies**. Use the **Scope** dropdown to choose **Tenant
   default** or a specific inbox.
2. Set the **Response window (hours)** — how quickly a first reply is
   expected — and the **Resolution window (hours)** — how quickly the
   conversation is expected to be resolved.
3. Set **Tier-2 re-alert after (hours)** if you want a second, level-2
   alert to fire when a case is still unresolved that many hours after its
   first breach — leave it blank to inherit the deployed default.
4. Set **Warn before breach (minutes)** to have the CRM raise an early
   warning that many minutes before a case is about to breach its
   resolution target, rather than waiting for the breach itself — leave it
   blank to inherit the deployed default.
5. Optionally set **Per-channel ACK minutes (JSON)** for a channel-specific
   acknowledgement target, for example `{"whatsapp": 15}`, and a **PIC
   WhatsApp number** to notify.
6. Use the **Engine enabled override** checkbox to switch SLA tracking off
   for this scope entirely — useful for an inbox that should not be
   measured against a target at all.
7. Leave any field empty on an inbox's policy to inherit the tenant default
   (or the deployed default, for Tier-2/warning) instead of setting an
   inbox-specific value. *Setting a field back to empty after you have
   typed in it can be rejected on save*; if that happens, ask us to clear
   the value rather than fighting the form.
8. Click **Save**. The new targets apply from the next check onward. A
   breach that has already been recorded is never recomputed or removed, and
   a conversation already resolved is not re-examined — so lowering a
   threshold cannot retrospectively create or erase a breach.

[[SCREENSHOT: ch09-sla-policies | Editing an SLA policy's response, resolution, Tier-2, and warning thresholds]]

### Example scenario

Proton sets a tenant-wide default of a 1-hour response window and an
8-hour resolution window, then overrides the Email inbox specifically with
a 30-minute response window and a **Warn before breach** of 30 minutes,
so the team gets a heads-up while there's still time to respond before an
Email case actually breaches and triggers the PIC-group alert.

### Integrations & automation

A policy breach is what the SLA breach alert engine (Conversations chapter)
actively notifies the PIC group and posts a private note about, on the Email
inbox today — that part is live and reads this account's real conversations.

The targets set here are *also* what the SLA reports in the Reports chapter
measure performance against, but read that chapter's opening note before
using those pages to judge whether a target is being met: the cross-channel
reporting connection those sections read from has not yet been switched on
for this account, so the "Proton" sections on the reports pages currently
show illustrative example numbers rather than this account's real
performance. Editing a target here changes what the live alert engine
enforces immediately; it does not make an illustrative report page real.

## Audit Log
<!-- TRAINING: audience=admin, exercise -->

### What it is

A read-only, filterable trail of what has happened to a case — who moved it
from one state to another, and when — used to review a case after the fact.

**It records more than an agent changing a status by hand**, which is worth
knowing before you read a row and can't place it. The same trail also
carries the SLA engine's own breach and Tier-2 markers, the moment an agent
first replied, whether an escalation email actually went out, an
escalation being acknowledged, and a supervisor reassigning a case from one
agent to another. Rows whose actor is not a person are the system recording
something it did.

### Where to find it

In the main left-hand navigation, select **Audit Log** (visible only if your
role has been granted the "View the audit log" permission — see **Roles &
Permissions** below).

### How to use it

1. Open **Audit Log** to see the recorded entries, newest first. The five
   columns are **At** (when), **Ticket** (which case), **Actor** (who or
   what), **Transition** (the move from one state to another), and
   **Remark**.
2. To narrow the list, enter an **Actor**, and/or a **From**/**To** date
   range. **Actor is an exact match, not a partial one** — a half-typed
   name returns nothing rather than a shortlist. The **To** date is
   inclusive of the whole day you pick.
3. Click **Filter** to apply the filters.
4. The page shows the most recent entries matching your filter rather than
   the entire history, so narrow the date range if you are looking further
   back.

[[SCREENSHOT: ch09-audit-log | Filtering the audit log]]

### Example scenario

A customer disputes when their case was marked resolved. An administrator
opens **Audit Log**, filters by the case's date range, and finds the exact
transition, who made it, and the remark left at the time — and, a few rows
above it, the SLA engine's own breach marker showing the case had already
run past its resolution target before anyone touched it.

### Integrations & automation

The audit log is the historical trail behind the case status shown in the
Cases chapter, the SLA breach alerts in the Conversations chapter, and the
escalation email in this chapter's Escalation Routing section — not a
separate data source, and not a copy that can disagree with them. Unlike
the report pages in the Reports chapter, it reads live records rather than
the reporting warehouse.

## Roles & Permissions
<!-- TRAINING: audience=admin, exercise -->

### What it is

This page controls which capabilities each role grants: both the CRM's own
native access controls (which conversations a role can see, and whether it
can manage contacts, reports, or the help centre) and the Proton-specific
permissions (SLA Policies, Audit Log, Roles & Permissions itself, Escalation
Routing, Case Taxonomy, Customer 360, the DMS/TSP integration and
more). It replaces the need to edit any configuration file to change what a
role can do.

**The page was redesigned since the last edition of this guide, and the
steps below have changed with it.** The most important change is that
nothing you tick is written when you tick it: every change is staged against
a local draft and only applied when you press **Save changes**. Creating a
role is the one exception — that happens immediately.

### Where to find it

In the main left-hand navigation, select **Roles & Permissions** (visible
only if your role has been granted the "Manage roles and permission
assignments" permission).

### How to use it

1. Open **Roles & Permissions**. Roles are listed down the left as cards,
   each showing how many permissions and how many members it has — click
   one to open it. To add a role, click **+ New role**, enter a **Name**,
   check the **Id** the page derives from it, and click **Create**. *The id
   is permanent and cannot be changed later*, so read it before you
   confirm.
2. Under **Chatwoot access**, choose one **Conversation visibility** option:
   **Default access** (no CRM-side restriction at all — visibility stays
   whatever the account already does for that person), **All
   conversations**, **Unassigned + own**, or **Own conversations only**.
   Then, under **Sections**, toggle whether the role can manage
   **Contacts**, **Reports**, and/or the **Knowledge base**. **Read the
   warning below before you change either of these on a role that any
   administrator belongs to** — the other three visibility options and the
   Sections toggles do not narrow an administrator's access, they replace
   it. For a role that exists only to grant Proton permissions, leave
   visibility on **Default access** and every Sections toggle off, and go
   straight to step 3.
3. Under **Permissions**, grant or revoke the Proton permissions. They are
   grouped — Knowledge & AI, Cases & taxonomy, Routing & escalation,
   Workforce, Alerts, Customer data and Administration — with a count of how
   many in each group are granted, a **Grant all** / **Revoke all** button
   per group, and a search box for finding one by name. That is the full set
   of groups; **you may well see fewer.** A group only appears if the
   platform is currently offering at least one permission belonging to it, so
   the page reflects what this account can actually grant today rather than
   a fixed list. A group you expected and cannot find is a question for us,
   not something to work around.
4. Under **Members**, click **+ Add member** and search for the person by
   name or email, or click **Remove** on a row to take the role away. A
   staged addition or removal is marked as such and can be undone before you
   save.
5. Click **Save changes** in the bar at the bottom of the page — or
   **Discard** to throw the draft away. Navigating away, or clicking a
   different role, with unsaved changes asks you first.

> **Never switch on a Chatwoot-access restriction for a role that
> administrators belong to.** This is the one mistake on this page that
> cannot be undone by the people it affects. Choosing any **Conversation
> visibility** option other than **Default access**, or ticking any of the
> **Sections** toggles, does not *narrow* what an administrator can do — it
> **replaces** their administrator access with exactly what that role
> grants. Everyone assigned to the role loses the Settings area and every
> administrative page in this chapter, including this one, so they cannot
> put it back themselves; it takes someone else with administrator rights,
> or us. This has happened on a live account, so treat it as a rule rather
> than a caution. If you need a role that only grants Proton permissions —
> which is the usual case — leave **Conversation visibility** on **Default
> access** and leave every **Sections** toggle off. The Chatwoot-access
> controls are for shaping *agent* roles.

> **Two things the page tries to catch for you.** If a save would leave you
> without the "Manage roles and permission assignments" permission through
> any role, the page warns you that this removes the page from your own
> navigation and cannot be undone from inside the product. That check has to
> work out which account is yours by matching your email address against the
> staff directory, and where it cannot, it stays quiet — **so treat it as a
> useful catch for the obvious case, not as a guarantee you will always be
> stopped.** Read back what you are about to save. Separately, if a save
> partly fails, the page tells you how many of your changes went through and
> leaves the rest staged, so you can fix the problem and save again rather
> than guessing at a half-applied state — that one does not depend on
> identifying you.

[[SCREENSHOT: ch09-roles-permissions | Creating a role, granting a group of permissions, and the save bar showing staged changes]]

### Example scenario

Proton onboards a new agent, Budi, who should only handle his own assigned
conversations and never see administrative pages. An administrator either
assigns him the existing **Agent** role, or clicks **+ New role**, names it
**Front-desk**, sets **Conversation visibility** to **Own conversations
only**, leaves every Proton permission group at zero granted, adds Budi
under **Members**, and clicks **Save changes**. That visibility setting is
safe here precisely because the role's members are agents; the same setting
on a role an administrator belongs to would take that administrator's own
access away.

### Integrations & automation

Every Proton page in this chapter — SLA Policies, Audit Log, Roles &
Permissions, Escalation Routing, Case Taxonomy, and the DMS/TSP integration
page — only appears in the navigation
for a user whose role has been granted the matching permission here. The
same is true of Customer 360, the Cases list and the RSA Incident Log
covered in earlier chapters. **My status** is the deliberate exception: its
permission is granted to the default Agent role, so ordinary agents can set
their own availability.

## Escalation Routing
<!-- TRAINING: audience=admin, exercise -->

### What it is

Escalation Routing maintains two directories used to route notifications:
which person-in-charge (PIC) to notify for each department, and which
**dealer group** — a named group with one or more member email addresses —
represents each dealer, for SLA breaches and escalated conversations.
Every member of a dealer group receives the escalation forward, not just
one address.

**This page is the reason an escalation reaches anyone at all, so read
what happens when a row is missing.** Applying a department label and then
`escalate` stamps the conversation and looks, from the conversation view,
exactly like a successful escalation — whether or not that department has a
contact configured here. A department with no PIC, or a dealer group with
no members, sends the escalation email to nobody, silently, with no error
visible anywhere in the CRM. There are six department labels — `dept_sales`,
`dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs` and
`dept_technical` — plus one row per dealer group. **Check this page rather
than assume every department is covered**; the directory is editable at any
time, so what was complete last month may not be today. Nothing about how
you apply the labels has changed (see the Conversations chapter's Labels
section).

**Checked on 2026-08-12:** every one of the six department rows —
Pre-Sales, Engineer, Sales, After-Sales, CS and Technical — had a PIC email
on file that day, and both dealer groups had at least one member. That is a
snapshot, not a guarantee: the directory is editable at any time, so treat
the instruction above — check this page yourself before you rely on it — as
the durable rule, and this note as one data point in its favour.

### Where to find it

In the main left-hand navigation, select **Escalation Routing** (visible
only if your role has been granted the "Manage PIC/dealer escalation
routing" permission).

### How to use it

1. Open **Escalation Routing**. It has two sections: **Department PICs** and
   **Dealer groups**.
2. To add a department's PIC, fill in **Department**, **PIC name**, **PIC
   email**, optionally **PIC WhatsApp**, and optionally **Members (CC)**
   (comma-separated email addresses to CC on that department's escalation
   email), then click **Add PIC**.
3. To change an existing PIC, click **Edit** on its row, update the fields
   — including adding or removing addresses from **Members (CC)** — and
   click **Save**; or click **Delete** and confirm to remove it.
4. To add a dealer group, fill in **Group name** and **Members** (one or
   more email addresses, comma-separated — everyone listed is forwarded the
   case) under the Dealer groups section, then click **Add dealer**.
5. To change a dealer group's membership — adding a new staff member or
   removing someone who's left — click **Edit** on its row, update the
   comma-separated **Members** list, and click **Save**; or click
   **Delete** and confirm to remove the whole group.
6. Changes take effect immediately for new escalations — there is no
   redeploy or waiting period. Any department or dealer not yet edited here
   falls back to the defaults configured when the tenant was set up.
7. To confirm a department actually routes before relying on it, scroll
   **Department PICs** and check that its row shows a PIC email — and, for
   any dealer you intend to escalate to, that its **Dealer groups** row
   lists at least one member. A row that is absent, or present with no PIC
   email or no members, is exactly the silent-dead-end case described
   above. Do this for all six department labels before a launch or a
   campaign rather than discovering a gap from a customer complaint.

[[SCREENSHOT: ch09-escalation-routing | Editing the PIC contact for a department and a dealer group's members]]

### Example scenario

The After-Sales PIC at Proton changes when a new department head, Ibu
Ratna, takes over. An administrator opens **Escalation Routing**, clicks
**Edit** on the **After-Sales** row, updates the **PIC name** and **PIC
email** to hers, and clicks **Save** — the next escalation email for
After-Sales goes to her immediately, without needing IT to change anything
outside the CRM. Separately, when a dealer outlet adds a second service
advisor who should also see escalated cases, an administrator opens the
matching dealer group, adds the new advisor's address to **Members**
alongside the existing one, and saves — both now receive every future
forward to that dealer.

**Escalating to a department or a dealer group**, from an agent's side, is
the same eight-second action every time — apply a department label, then a
dealer label if there is one, then `escalate` (see the Conversations
chapter's Labels section). Which slug goes with which kind of case:

1. A pricing/financing question that needs Pre-Sales: apply `dept_pre_sales`,
   then `escalate`. The Pre-Sales PIC's email is who receives it.
2. A vehicle fault needing engineering judgement: apply `dept_engineer`,
   then `escalate`. Routes to the Engineer PIC.
3. A completed-sale delivery/booking complaint: apply `dept_sales`, then
   `escalate`. Routes to the Sales PIC.
4. A warranty/service complaint: apply `dept_aftersales`, then `escalate`.
   Routes to the After-Sales PIC.
5. A general enquiry with no clear department: apply `dept_cs`, then
   `escalate`. Routes to the CS PIC.
6. A software/app/infotainment fault: apply `dept_technical`, then
   `escalate`. Routes to the Technical PIC.
7. A case that also needs a specific dealer outlet looped in: apply that
   dealer's label *alongside* whichever department label fits, before
   `escalate`. Every member of that dealer group is forwarded the case, and
   the dealer's turnaround clock starts at the same moment.

Each of those routes to whatever this page currently holds for that
department or dealer — and to nobody, silently, where a row is missing or
empty. That is why step 7 above is worth doing before you rely on any of
them.

### Integrations & automation

This directory is what the escalation email (see the AI Behaviour chapter's
escalation-labels section) and dealer-labeled conversations use to decide
who gets notified, and what the dealer-turnaround report (see the Reports
chapter) uses to attribute a handoff to a specific dealer. It is also the
allowlist that the escalation reply loop (see the Conversations chapter's
Escalation replies section) checks against — a reply that lands from an
address not listed here as a PIC or a dealer-group member is not linked
back onto the case.

## Agent Availability & My Status
<!-- TRAINING: audience=agent, exercise -->

### What it is

An extended set of **availability statuses** an agent can choose beyond the
three the CRM offers natively (Online, Busy, Offline): **Available, Busy,
Lunch, Break, Coaching, Training, Toilet** and **Prayer** — new since the
last edition of this guide and live on this account.

One thing about how this is presented is deliberate, and knowing it prevents
drawing the wrong conclusion from the status shown elsewhere in the CRM:

> **Named statuses mirror into the CRM's own Online/Busy/Offline.**
> Selecting "Lunch" shows as **Busy** to colleagues in the conversation
> list, while the **My status** page shows the named status itself. This is
> deliberate: the CRM's own availability field only has three values, and
> mirroring means an agent is still correctly excluded from being offered
> new work even if the named-status service is unavailable.

**Two capabilities in this area are built but not switched on for this
account, and neither should be described to agents as though it were
working:**

- **The absence alerts do not fire.** The design behind them is real —
  Lunch, Break, Toilet and Prayer are marked as "counts as unavailable", and
  the **My status** page even shows an "(alerts)" marker beside them, which
  is why this needs saying plainly. But the alerting itself is switched off
  on this account, so nobody is notified when an agent stays in one of those
  statuses. Ask us before you plan a shift-supervision process around it.
- **After-Call Work is not switched on.** Nothing places an agent into a
  wrap-up state automatically when a phone call ends, and the status is not
  offered as a choice.

**Average handling time is not part of this** either. It depends on call
queue statistics the platform does not yet receive from the telephony side —
the same gap that leaves several call-related rows of the monthly report
unmeasured.

### Where to find it

Agents set their status in two equivalent places: the **availability
control in the profile menu**, which now offers the named statuses rather
than only the CRM's three, and a dedicated **My status** page in the main
left-hand navigation.

**My status** appears only if your role has the matching permission — "Set
your own availability status" — which the default Agent role carries. If an
agent cannot see **My status**, check on the **Roles & Permissions** page
that they have been assigned a role at all.

### How to use it

1. **Open the availability control in your profile menu, or the My status
   page**, and pick the status that matches what you are doing — **Lunch**
   when you step out, for example. You do not need to also set yourself
   Busy: choosing Lunch does that for you, which is why colleagues still
   see you as Busy in the conversation list. **Offline** stays exactly as it
   always was and is still how you say you are off shift.
2. **Check the My status page when you want to know what you are currently
   set to.** The profile-menu control shows the underlying Online/Busy/
   Offline dot rather than the named status, so after picking "Lunch" it
   still reads Busy — that is correct, not a fault. The **My status** page
   is the one that says *Currently: Lunch — for 55 min*.
3. Set yourself back to **Available** when you return. Anything other than
   Available means new conversations are not routed to you.
4. **To add a status of your own** — a shift pattern or an activity specific
   to your team — an administrator opens the **Status catalogue** section at
   the bottom of the **My status** page and clicks **Add status**. Give it a
   key, a label, a colour, which of the CRM's three native statuses it
   should mirror into, whether an agent in it can still receive new
   conversations, and whether it should count as unavailable. Existing
   statuses are edited the same way. **Statuses are never deleted**, because
   past history refers to them — retire one by marking it as not receiving
   new conversations.
5. In that catalogue, a row marked **Default** is one of the built-in
   statuses that has not been saved on this account yet; saving it writes
   it. **Counts as unavailable** is the setting that would arm the absence
   alerts if they were switched on — leave it off for scheduled activities
   like Coaching and Training, on for stepping away.

[[SCREENSHOT: ch09-my-status | The My status page: the status picker, the current status with its elapsed time, and the administrator-only Status catalogue]]

### Example scenario

An agent sets herself to **Lunch** at 10:45 before stepping away from her
desk. Colleagues opening the conversation list see her as **Busy**, so
routing correctly stops offering her new conversations, while her own **My
status** page reads *Currently: Lunch — for 55 min* so she can see exactly
how long she has been away. She sets herself back to **Available** just
before 11:40 when she returns, and new conversations resume.

### Integrations & automation

Availability is what conversation routing already reads to decide who can be
offered new work (see the Conversations chapter), so a named status takes
effect on routing through the native status it mirrors into — which is why
routing keeps behaving correctly even if the named-status service is
unavailable. The agent availability shown beside each name in the **Agent
Channel Priorities** table earlier in this chapter is the same value.

Two separate permissions, both granted from **Roles & Permissions**, govern
what's described here: *setting your own status* belongs to the Agent role
by default, because it is part of an agent's own working day; *editing the
status catalogue or setting another agent's status* is an administrator
permission. An agent cannot mark a colleague as being on Lunch — that would
take the colleague out of routing under their own name.

## AI Conversational Quality

### What it is

A set of adjustments to how the AI reads and answers customers. Two of them
are live on this account and appear in the reply composer; the rest are
configuration your administrator asks us to switch on, rather than
checkboxes inside the CRM, because each one changes what customers receive.

**Live today:**

1. **Translate, for the agent.** A **Translate** action in the reply
   composer renders the customer's latest message in English as a **private
   note** on the conversation. The translation is never sent to the
   customer — the only thing the platform can do with it is add a note. See
   the Conversations chapter for the agent-side view.
2. **The FAQ suggestion strip** above the reply box, offering a single
   best-matching FAQ answer with an Apply button. Also covered in the
   Conversations chapter.

**Built, deployed, and switched off on this account:**

3. **Sentiment on every conversation.** The AI would record how the customer
   sounds — positive, neutral, negative or urgent — on the conversation
   itself, using the same request it already makes to answer the message.
   No extra AI call and no extra delay.
4. **Tone matched to that sentiment**: a measured, apologetic register for
   an angry customer, a brisk one for an urgent safety issue. It does
   nothing without sentiment recorded first, and a sentiment older than
   about a quarter of an hour is ignored, so an hour-old complaint does not
   make a cheerful "thanks, all sorted" come back apologetic.
5. **Photo and video diagnosis.** When a customer sends a photo or video,
   the AI would be asked to describe what is actually visible, name the
   likely fault, state how confident it is and ask at most one follow-up
   question — instead of a generic "please describe the issue". Voice notes
   are deliberately excluded: there is nothing to look at.
6. **Resolved-case summaries.** When a case is resolved, the AI can post a
   summary of it as a private note, and can separately add that summary to a
   searchable store of previously resolved cases. The two are independent.

A seventh adjustment is **already in effect at a setting that changes
nothing**: a keyword-overlap score can be blended into FAQ ranking, to help
product codes and model names (e.MAS 7, for instance) that match poorly on
meaning alone. It ships at zero weight, which reproduces today's suggestions
exactly, entry for entry.

Two of these carry limits that must be read before they are switched on.

> **Tamil.** Inbound Tamil translation — so an agent can *read* a Tamil
> message — is what the live Translate action gives you. **Outbound Tamil
> replies to customers remain deliberately disabled**, pending an evaluation
> of thirty real Tamil enquiries scored by a Tamil speaker. Switching it on
> before that evaluation sends unverified machine translation to customers.
>
> **Resolved-case suggestions are not approved guidance.** They are
> generated from summaries of previously resolved cases — what a colleague
> did last month, not what the manual says — and are labelled as such
> wherever they are shown. The store holds summaries rather than
> transcripts, and the summariser is instructed to omit customer
> identifiers, but **that is a mitigation, not guaranteed removal of
> personal data.** Two things about it are worth knowing precisely: nothing
> checks or edits a summary before it is stored, and an operator's own
> persona **guardrails** are placed ahead of that instruction in the same
> request — so a guardrail saying the opposite ("always include the
> customer's full name") is text the AI may prefer. Anyone who can edit the
> persona can weaken the mitigation without any software change.

### Where to find it

The **Translate** action and the **FAQ suggestion strip** are in the reply
composer, inside a conversation. Everything else on this list is a **setting
in the account's configuration**, not a page in the CRM: there is no admin
screen that toggles them, which is deliberate — each one changes what
customers receive, so switching it on is a deployment decision with a
record, not a checkbox. Ask your administrator.

Where sentiment is switched on, it appears on the conversation's custom
attributes. Where resolved-case summaries are switched on, they appear as
private notes in the conversation.

> **Not yet demonstrated.** The photo-and-video diagnosis wording has never
> been tried against a real photo sent through a real WhatsApp number. It is
> switched off today, and we would want that test before switching it on for
> a live account — please ask for it rather than assuming it has been done.

<!-- VERIFY-LIVE: confirm the photo/video diagnosis prompt against a real image sent through a real WhatsApp number before this setting is enabled on any tenant -->

### How to use it

1. **Use Translate to read, not to reply.** It posts a private note. To
   answer a Malay, Chinese or Tamil customer, write the reply as you
   normally would — the AI already answers in the language the customer
   wrote in.
2. **Ask for sentiment before tone.** Tone selection does nothing on its
   own: with no sentiment recorded there is nothing to choose a tone from,
   and the assistant keeps its standard wording.
3. **Sentiment on its own is not a no-op, so expect one side effect.** A
   case whose customer sounded negative or urgent is treated as **open**
   rather than resolved, and that reading is not time-bounded — so one angry
   turn keeps later turns in the same conversation reading as open,
   including a closing "thanks, all sorted". The replies themselves are
   unchanged. Tell us if that is unwanted, rather than discovering it in a
   report.
4. **Leave the FAQ keyword weight at zero until you have a measurement.**
   Zero reproduces today's ordering and today's scores exactly. A large
   weight would damage the common case — ordinary questions, which already
   match well — in order to fix the rare one.
5. **Do not switch on outbound Tamil.** See the note above.
6. **The two resolve settings are independent.** The private-note summary
   needs no database and can be used on its own. The searchable store of
   resolved cases needs the knowledge database, which this account does
   have; if it were not configured, the summary note would still post and
   the store would simply be skipped. Either way, **resolving the case
   always succeeds** — resolving is the agent's action, and the summary is
   an addition to it that can never fail it.
7. **A case reopened and resolved again gets a second summary**, appended
   rather than overwriting the first, because the first summary is a true
   record of the first resolution.
8. **Emptying the resolved-case store is not self-service yet.** It is
   deliberately kept separate from your authored FAQs, so clearing it can
   never remove curated content — but there is no button or command for it
   today, so ask us before you need it in a hurry.

### Example scenario

A customer messages in Tamil about a warning light. The agent, who does not
read Tamil, clicks **Translate**: a private note appears in the conversation
with the English text, and the customer sees nothing. She answers in her own
words and the assistant's FAQ suggestion strip offers the standard warranty
wording, which she applies with one click. Later, Proton asks whether the
assistant could also read the dashboard photo the customer sent and name the
warning symbol — that is the photo-diagnosis setting above, and the answer
is that it is built and switched off, and we would want to try it against a
real photo first.

### Integrations & automation

Sentiment, where enabled, is written to the conversation's own custom
attributes, so it is available to automation rules and to reporting exactly
like any other case field — no new integration surface. The resolved-case
summary is produced by the same summariser the **Summarize** button in the
composer already uses, so there is one summariser and one set of wording to
review rather than two that can drift apart, and it is posted through the
same private-note mechanism as escalation notes.

**Translate has its own permission** ("use translation"), granted to the
default Agent role rather than kept administrator-only: reading a customer's
own message is part of an ordinary agent's job.

Two limits on what has been delivered, recorded here because both read as
delivered if nobody says so:

- **Nothing yet shows resolved-case suggestions to an agent.** Summaries
  would be stored and labelled, and the labelling exists so that whichever
  panel eventually adds them cannot present them with a curated FAQ's
  authority — but no panel queries that store today. Switching it on builds
  the corpus; it does not yet surface it.
- **No before-and-after accuracy figures exist.** The evaluation sets and
  the runner for them are built and are part of the delivery, but they have
  never been run against the real AI service, so the accuracy tables read
  "unmeasured". Any percentage produced by the test harness in its current
  form is a property of the harness, not of the model, and must not be
  quoted as a result.

## Account settings

### What it is

Account-wide settings that are not specific to any one inbox, team, or
agent — for example the account name, default timezone, and other
platform-level preferences.

### Where to find it

Open **Settings** (the gear icon) and select **Account Settings**.

<!-- VERIFY-LIVE: confirm exact Account settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Account Settings**.
2. Update the account name, timezone, or other available preferences as
   needed.
3. Save your changes.

<!-- VERIFY-LIVE: confirm exact Account settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-account-settings | The account settings page]]

### Example scenario

When Proton's support desk switches its reporting to WIB (Indonesia
Western time), an administrator updates the account timezone under
**Account Settings** so timestamps shown across the CRM match local
business hours.

### Integrations & automation

Account-level settings such as timezone affect how timestamps are displayed
across reports and the audit log elsewhere in this chapter.
