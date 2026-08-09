# Administration (Settings)

This chapter covers the pages an administrator uses to configure the CRM
itself, rather than to handle a customer conversation. Most of these pages
require an administrator account; a handful of the Proton-specific pages
(SLA Policies, Audit Log, Roles & Permissions, Escalation Routing, and
Integrations) additionally require a specific permission to be granted to
your role — if you don't see one of these items in the left-hand navigation,
ask an administrator to grant you the matching permission from **Roles &
Permissions**.

## Agents

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

### What it is

An inbox is one connected communication channel — a WhatsApp number, an
email address, the website widget, and so on. Each inbox has its own
settings: which agents can see it, its working hours, and (Proton addition)
how long an idle conversation waits before the customer is warned and the
conversation is auto-closed.

### Where to find it

Open **Settings** (the gear icon) and select **Inboxes**, then choose the
inbox you want to configure.

<!-- VERIFY-LIVE: confirm the exact tab name that hosts business hours / inactivity settings on the live tenant -->

### How to use it

1. Open **Settings → Inboxes** and select the inbox to configure.
2. Use the inbox's general settings to control its name, the agents/teams
   assigned to it, and greeting/away messages.
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
   wording.
7. Click **Update** to save — this single action saves both the business
   hours and the inactivity-timing settings together.

[[SCREENSHOT: ch09-inboxes | Inbox inactivity-timing settings]]

### Example scenario

Proton's WhatsApp inbox is set to warn a customer after 10 minutes of no
reply ("Warn after idle (min)"), then auto-close 5 minutes later during
business hours, but wait longer overnight when no agent is on duty — set via
a larger out-of-hours grace value on the same inbox.

### Integrations & automation

The inactivity timers work together with the AI assistant's lifecycle
messages (see the AI Behaviour chapter) to keep conversations moving without
an agent having to manually chase or close every idle chat.

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

### Where to find it

Open **Settings** (the gear icon) and select **Custom Attributes**.

<!-- VERIFY-LIVE: confirm exact Custom Attributes settings UI wording on the live tenant -->

### How to use it

1. Open **Settings → Custom Attributes**.
2. Click **Add Custom Attribute**, choose whether it applies to
   conversations or contacts, give it a name and a type (text, number, list,
   checkbox, etc.), and save.
3. To edit an attribute's options, click on it and update the fields. For
   the five case-categorisation attributes specifically, treat this as a
   last resort rather than routine maintenance — Case Detail alone carries
   246 values matched one-for-one against the RFP source, and a hand
   edit here won't update that source file, so the two can drift apart.
4. To remove an attribute that is no longer needed, use its delete option and
   confirm.

<!-- VERIFY-LIVE: confirm exact Custom Attributes settings UI wording on the live tenant -->

[[SCREENSHOT: ch09-custom-attributes | The Custom Attributes settings page]]

### Example scenario

Proton's five case-categorisation fields (used on the Cases page) are
defined here as conversation-level custom attributes, so any conversation
can be tagged with the same structured Case Type/Category/Subcategory/
Detail/Vehicle Model values used in Case reporting.

### Integrations & automation

Custom attributes back the Cases feature's five categorisation fields (see
the Cases chapter) and can also be used as conditions in Automation rules
below.

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
   **DMS / TSP** card. Its status badge shows **Not connected**, **Enabled**,
   or **Status unavailable** — it never claims to be actively connected,
   since that can only be confirmed with the **Test connection** button
   described below.
2. Check **Enabled** to turn the connection on.
3. Fill in a **Provider label** (a friendly name for your own reference),
   the **Auth type** (Bearer token, Basic auth, or API key header), and the
   **Base URL** of the DMS/TSP system (must start with `https://`).
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
   configuration can actually reach the DMS/TSP system.

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
6. Leave any field empty on an inbox's policy to inherit the tenant default
   (or the deployed default, for Tier-2/warning) instead of setting an
   inbox-specific value.
7. Click **Save**. The policy applies to conversations on that scope going
   forward; it does not re-evaluate already-closed conversations.

[[SCREENSHOT: ch09-sla-policies | Editing an SLA policy's response, resolution, Tier-2, and warning thresholds]]

### Example scenario

Proton sets a tenant-wide default of a 1-hour response window and an
8-hour resolution window, then overrides the Email inbox specifically with
a 30-minute response window and a **Warn before breach** of 30 minutes,
so the team gets a heads-up while there's still time to respond before an
Email case actually breaches and triggers the PIC-group alert.

### Integrations & automation

SLA policy targets are what the SLA reports in the Reports chapter measure
performance against; a policy breach is what those reports flag as missed,
and what the SLA breach alert engine (Conversations chapter) actively
notifies the PIC group and posts a private note about, on the Email
inbox today.

## Audit Log

### What it is

A read-only, filterable log of case/ticket status changes — who changed a
case from one status to another, and when — used to review what happened on
a case after the fact.

### Where to find it

In the main left-hand navigation, select **Audit Log** (visible only if your
role has been granted the "View the audit log" permission — see **Roles &
Permissions** below).

### How to use it

1. Open **Audit Log** to see every recorded status change, newest first.
   Each row shows the timestamp, the ticket/case, the actor who made the
   change, the transition (from status to status), and any remark left with
   it.
2. To narrow the list, enter an **Actor** to filter by who made the change,
   and/or a **From**/**To** date range.
3. Click **Filter** to apply the filters.

[[SCREENSHOT: ch09-audit-log | Filtering the audit log]]

### Example scenario

A customer disputes when their RSA case was marked resolved. An
administrator opens **Audit Log**, filters by the case's date range, and
finds the exact transition from "In progress" to "Resolved," who made it,
and the remark left at the time.

### Integrations & automation

The audit log records the same case status transitions surfaced in the
Cases and RSA chapters — it is the historical trail behind those features,
not a separate data source.

## Roles & Permissions

### What it is

This page controls which capabilities each role grants: both Chatwoot's own
native access controls (which conversations a role can see, and whether it
can manage contacts, reports, or the knowledge base) and the Proton-specific
administrative permissions (SLA Policies, Audit Log, Roles & Permissions
itself, Escalation Routing, Customer 360, and DMS/TSP integration
management). It replaces the need to edit any configuration file to change
what a role can do.

### Where to find it

In the main left-hand navigation, select **Roles & Permissions** (visible
only if your role has been granted the "Manage roles and permission
assignments" permission).

### How to use it

1. Open **Roles & Permissions**. Pick an existing role from the **Role**
   dropdown, or create a new one by entering a role id and name under **New
   role id** / **Name** and clicking **Create role**.
2. Under **Chatwoot access**, choose one conversation-visibility option for
   the role — **Manage all conversations**, **Unassigned conversations
   only**, or **My conversations only** — and toggle whether the role can
   manage **Contacts**, **Reports**, and/or **Knowledge base**.
3. Under **Permissions**, check or uncheck any of the Proton administrative
   permissions to grant or remove them for this role (for example, "Manage
   SLA policies" or "View the Customer 360 lookup").
4. Under **Assigned users**, enter a Chatwoot user id and click **Assign**
   to give a specific person this role, or click **Remove** next to a listed
   user id to take it away.

[[SCREENSHOT: ch09-roles-permissions | Creating a role and assigning permissions]]

### Example scenario

Proton onboards a new agent, Budi, who should only handle his own assigned
conversations and never see administrative pages. An administrator either
assigns him the existing **Agent** role, or creates a limited **Front-desk**
role with conversation visibility set to **My conversations only** and none
of the Proton administrative permissions checked, then assigns Budi's user
id to it under **Assigned users**.

### Integrations & automation

Every Proton administrative page in this chapter — SLA Policies, Audit Log,
Roles & Permissions, Escalation Routing, and the DMS/TSP integration page —
only appears in the navigation for a user whose role has been granted the
matching permission here.

## Escalation Routing

### What it is

Escalation Routing maintains two directories used to route notifications:
which person-in-charge (PIC) to notify for each department, and which
**dealer group** — a named group with one or more member email addresses —
represents each dealer, for SLA breaches and escalated conversations.
Every member of a dealer group receives the escalation forward, not just
one address.

**Escalation routing is now complete.** All six department labels —
`dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`,
`dept_cs`, and `dept_technical` — have a PIC configured and route
correctly today, as do both live dealer groups, `dealer_komang_motor` and
`dealer_caroline_motor`. Previously, most department labels had no PIC
behind them: applying `dept_aftersales`/`dept_cs`/`dept_technical` (for
example) and then `escalate` still stamped the conversation and looked
like a successful escalation, but the mail simply had nobody to send to —
silently, with no error visible anywhere in the CRM. That gap is what
this section fixes; nothing about how you apply the labels changed (see
the Conversations chapter's Labels section).

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
7. To confirm every department actually routes before relying on it, scroll
   **Department PICs** and check that all six rows — `dept_sales`,
   `dept_engineer`, `dept_pre_sales`, `dept_aftersales`, `dept_cs`, and
   `dept_technical` — show a PIC email, and that **Dealer groups** lists
   both `dealer_komang_motor` and `dealer_caroline_motor` with at least
   one member each. A row with no PIC/members is exactly the silent-dead-end
   case described above.

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

**Escalating to each of the six departments and a dealer group**, from an
agent's side, is the same eight-second action every time — apply a
department label, then a dealer label if there is one, then `escalate`
(see the Conversations chapter's Labels section) — repeated here once per
destination so there's no ambiguity about which slug goes with which
case:

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
7. A case that also needs a specific dealer outlet looped in — Komang
   Motor, say — apply `dealer_komang_motor` (or `dealer_caroline_motor`
   for the other live dealer group) *alongside* whichever department
   label fits, before `escalate`. Every member of that dealer group is
   forwarded the case, and the dealer's turnaround clock starts at the
   same moment.

All seven of these are now live, verified destinations — before this
edition, only some had a PIC behind them.

### Integrations & automation

This directory is what the escalation email (see the AI Behaviour chapter's
escalation-labels section) and dealer-labeled conversations use to decide
who gets notified, and what the dealer-turnaround report (see the Reports
chapter) uses to attribute a handoff to a specific dealer. It is also the
allowlist that the escalation reply loop (see the Conversations chapter's
Escalation replies section) checks against — a reply that lands from an
address not listed here as a PIC or a dealer-group member is not linked
back onto the case.

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

A seventh setting, `FAQ_SUGGESTION_POPUP_ENABLED`, gates a dismissible FAQ
suggestion strip above the composer — a single best-matching FAQ answer for
the customer's last message, with an Apply button that writes it straight
into the reply. It only appears when the suggestion's confidence clears a
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
   bot keeps its standard wording. Enabling sentiment alone is a safe first
   step — it records the reading without changing a single reply.
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
7. **The resolved-case store can be emptied without touching your authored
   FAQs.** They live in separate tables with nothing shared between them, so
   clearing machine-generated summaries cannot remove curated content. That
   containment is what makes enabling the store reversible.

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
the same summariser the **Summarise** button in the composer already uses, so
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
