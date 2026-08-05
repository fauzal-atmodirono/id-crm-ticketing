# Introduction

## What is Proton e.MAS CRM

### What it is

Proton e.MAS CRM is Proton's unified customer support platform for the
e.MAS electric-vehicle business. It brings every customer conversation —
WhatsApp, email, and phone/IVR calls — into a single inbox, adds an
AI assistant that can draft or send replies, and layers on automotive
support tools such as roadside-assistance (RSA) incident logging, a
vehicle/service lookup (Customer 360), case tracking, and reporting built
around dealer and PIC (person-in-charge) escalation.

Every agent and administrator uses the same platform through a web
browser; there is nothing to install.

### Where to find it

The platform is reached at your organization's CRM web address, provided
by your administrator. Bookmark it — you'll come back to it every shift.

### How to use it

1. Open a web browser and go to your organization's CRM address.
2. Sign in with the account your administrator created for you (see
   **Logging in** below).
3. After signing in you land on the **Conversations** view — the default
   screen showing every conversation across the inboxes you have access
   to.
4. Use the sidebar on the left to move between Conversations, Contacts,
   Knowledge, Cases, RSA, Reports, and (for administrators) Administration.

[[SCREENSHOT: ch01-overview | Landing view after logging in to Proton e.MAS CRM]]

### Example scenario

At the start of a shift, an agent at a Proton e.MAS dealership opens the
CRM in their browser and lands directly on the Conversations view, where
overnight WhatsApp inquiries about test drives and service bookings are
already waiting.

### Integrations & automation

The CRM is the single front door for WhatsApp, email, and phone/IVR
conversations, the Gemini-powered AI assistant, the vehicle/service data
lookup (DMS/TSP), and Proton's reporting — every chapter in this guide
covers one part of that single platform.

## Logging in

### What it is

The sign-in screen that authenticates you as an agent or administrator
before you can see any conversations or customer data.

### Where to find it

Your CRM web address opens directly to the login screen if you are not
already signed in.

### How to use it

1. Go to your organization's CRM address.
2. Enter the email address and password your administrator set up for
   you.
3. Click the sign-in button.
4. If you forget your password, use the "Forgot password?" link to
   request a reset email.
5. Once signed in, you stay logged in on that browser until you sign out
   or your session expires.

<!-- VERIFY-LIVE: confirm the exact login screen fields/branding on the live tenant -->

[[SCREENSHOT: ch01-login | The Proton e.MAS CRM login screen]]

### Example scenario

A newly hired agent receives a welcome email with their login details,
opens the CRM address on their laptop, signs in for the first time, and
is prompted to set a new password before reaching the dashboard.

### Integrations & automation

Signing in does not touch any other system by itself — it simply opens
the session you use for every other feature in this guide.

## Screen layout

### What it is

The main working screen you see after logging in: a navigation sidebar
on the left, the conversation list in the middle-left column, the open
conversation with its reply box in the center, and a contact side panel
on the right.

### Where to find it

This layout is what you see any time you are inside the Conversations
area of the CRM.

### How to use it

1. Use the **sidebar** on the far left to switch between Conversations,
   Contacts, Knowledge, Cases, RSA, Reports, Campaigns/Help Center, and
   (for administrators) Administration and other admin-only pages.
2. Use the **conversation list** to browse and filter the conversations
   in your inboxes — see the Conversations chapter for the filters
   available.
3. Click a conversation to open it in the **main conversation pane**,
   where the message thread and reply box live.
4. Check the **contact side panel** on the right for the customer's
   details and history alongside the conversation you're reading.
5. Look for AI-assist buttons (Ask Copilot, Suggest reply, Summarize)
   above the reply box when you need help drafting a response.

[[SCREENSHOT: ch01-dashboard-layout | Main dashboard layout: sidebar, conversation list, reply box, contact panel]]

### Example scenario

An agent handling a service-booking question keeps the contact panel
open to check the customer's vehicle model while typing a reply in the
main conversation pane, without leaving the conversation.

### Integrations & automation

The sidebar is where Proton's added modules — Knowledge, Cases, RSA,
the extra Reports pages, and (for administrators) Integrations,
Escalation Routing, SLA Policies, Audit Log, and Roles & Permissions —
sit alongside the standard Conversations and Contacts views, so
everything is reachable from one place.

## Roles: agent vs administrator

### What it is

Every account has a base role — **Agent** or **Administrator** — that
controls what you can see and do. Administrators can additionally be
granted specific permissions (for example, managing escalation routing,
SLA policies, integrations, or viewing the audit log), so two
administrators may not see exactly the same admin pages unless they've
been granted the same permissions.

### Where to find it

Your role is set by an administrator when your account is created, under
Administration → Agents. Fine-grained permissions are managed under
Administration → Roles & Permissions.

### How to use it

1. As an agent, you see Conversations, Contacts, Knowledge (read access),
   Cases, and RSA — the day-to-day support tools.
2. As an administrator, you additionally see Administration and any
   admin-only pages your permissions grant, such as Integrations,
   Escalation Routing, SLA Policies, Audit Log, and Roles & Permissions.
3. If you believe you're missing access you should have, ask an
   administrator to check your role and permissions under Administration
   → Roles & Permissions.

[[SCREENSHOT: ch01-roles | Sidebar differences between an agent and an administrator account]]

### Example scenario

A support supervisor is given the Administrator role plus the
escalation-routing permission, so they can update which dealer receives
escalation emails without also being handed full account-wide
administrator access to every setting.

### Integrations & automation

Roles and permissions gate every admin-only page covered in the
Administration chapter — what you see there depends directly on what's
set up here.

## Language (English / Indonesian)

### What it is

A setting that switches the CRM's interface text between English and
Indonesian. It changes labels and menus, not the language customers
write to you in.

### Where to find it

Your profile settings menu, usually reached from your avatar/name in the
top corner of the screen.

<!-- VERIFY-LIVE: confirm the exact profile-menu location and wording for the language switch on the live tenant -->

### How to use it

1. Open your profile settings from your avatar or name.
2. Find the interface language option.
3. Choose English or Indonesian (Bahasa Indonesia).
4. The interface labels update immediately (or after a page refresh).

[[SCREENSHOT: ch01-language-toggle | Switching the interface language between English and Indonesian]]

### Example scenario

An agent who is more comfortable working in Bahasa Indonesia switches
their interface language, while the AI assistant continues to reply to
each customer in whatever language that customer writes in.

### Integrations & automation

The interface language is a personal preference only — it doesn't change
which language the AI assistant uses when replying to a customer.
