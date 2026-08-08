# Contacts

## Contacts list & search

### What it is

The Contacts area lists everyone who has ever messaged in — across
WhatsApp, email, and phone/IVR — as a single directory of customers,
separate from the Conversations view's per-channel threads.

### Where to find it

**Contacts** in the main sidebar.

### How to use it

1. Open **Contacts** from the sidebar to see the full customer list.
2. Use the search box to find a customer by name, phone number, or email
   address.
3. Use the available filters to narrow the list (for example, by the
   channel a customer last used).
4. Click a customer's row to open their contact profile.

<!-- VERIFY-LIVE: confirm exact contacts list/search UI wording (search placeholder, filter names) on the live tenant -->

[[SCREENSHOT: ch03-contacts-list | The contacts list with search and filters]]

### Example scenario

An agent taking a phone call for a customer who has previously written in
on WhatsApp searches Contacts by the customer's phone number to confirm
they're speaking with an existing customer before opening a new
conversation.

### Integrations & automation

Every contact created through an inbound WhatsApp, email, or phone/IVR
message is added here automatically by Chatwoot — there is no separate
manual step to add a customer who has already messaged in.

## Contact profile & history

### What it is

A contact's profile is a single page showing everything the CRM knows
about that customer: their basic details and custom attributes (for
example, vehicle model), plus every conversation they've ever had across
every channel.

### Where to find it

Click any customer in the Contacts list, or open the contact side panel
from an active conversation and click through to the full profile.

### How to use it

1. Open a contact from the Contacts list or from an open conversation's
   contact panel.
2. Review their contact details and custom attributes at the top of the
   profile.
3. Scroll the conversation history to see every past conversation with
   this customer, regardless of channel or status.
4. Click any past conversation in the list to reopen and review it.

<!-- VERIFY-LIVE: confirm exact contact profile layout and tab names on the live tenant -->

[[SCREENSHOT: ch03-contact-profile | A contact's profile showing their conversation history]]

### Example scenario

Before replying to a new service complaint, an agent opens the customer's
profile and sees a previous conversation from three months ago about the
same vehicle, giving useful context before responding.

### Integrations & automation

Conversation history shown here is what the Customer 360 lookup
(below) also draws on when it lists a customer's conversations, so the
two views never disagree about which conversations belong to a customer.

## Notes & segments

### What it is

Notes let staff record customer-level context that isn't tied to a single
conversation (for example, a standing delivery preference). Segments are
saved contact filters — a named, reusable version of a Contacts search
you'd otherwise have to rebuild every time.

### Where to find it

Notes live on the contact's profile; segments are created from the
Contacts list's filter/search bar and then appear as a saved view in the
Contacts sidebar area.

<!-- VERIFY-LIVE: confirm exact notes tab wording and the segment creation/save flow on the live tenant -->

### How to use it

1. To add a note, open the contact's profile and use the notes area to
   record context about that customer.
2. To create a segment, open Contacts, build a filter (for example, by
   channel or a custom attribute), and save it with a name.
3. Reopen a saved segment at any time from the Contacts sidebar to reapply
   the same filter without rebuilding it.

[[SCREENSHOT: ch03-segments | Creating or applying a contact segment]]

### Example scenario

A dealer-relations coordinator saves a segment for customers tagged with
a particular dealer's custom attribute, then reopens it weekly instead of
re-entering the same filter each time.

### Integrations & automation

Notes and segments are contact-level only — they don't feed the AI
assistant's replies or any of the reports in the Reports chapter.

## Customer 360

### What it is

Customer 360 is a single search box that looks a customer up by phone
number or vehicle number and brings together, on one page, what the CRM
already knows about them: their matching contact record, every
conversation they've had across channels, any roadside-assistance (RSA)
incidents tied to their vehicle, and — where the DMS/TSP integration is
configured (see the Administration chapter's Integrations section) — a
vehicle and service-history block. It's read-only: nothing here can be
created, edited, or deleted; contact details are still edited from the
contact's own profile, and RSA incidents from the RSA Incident Log
chapter's page.

### Where to find it

**Customer 360** in the main sidebar. This page is only visible to
administrators who have also been granted the Customer 360 permission
(see the Administration chapter's Roles & Permissions section) — an
administrator without that permission won't see it in their sidebar.

### How to use it

1. Open **Customer 360** from the sidebar.
2. Enter a phone number or a vehicle number in the search box (at least
   two characters).

[[SCREENSHOT: ch03-customer360 | Searching Customer 360 by phone number or vehicle number]]

3. Click **Search**.
4. Review the **Contact** section for the matching customer's name,
   phone number, and email — searching by phone number looks for an
   exact match; searching by vehicle number instead matches any
   conversation whose noted vehicle model contains that value, so the
   conversations shown can differ slightly between the two search modes
   for the same customer.
5. Review the **Conversations** table — every conversation for the
   matched contact (or matching the vehicle), across any channel or
   status. Click a conversation's ID to open it.
6. Review the **RSA incidents** table — any roadside-assistance incidents
   whose vehicle number matches the search (see the RSA Incident Log
   chapter).
7. If a **DMS / TSP** section appears, review the customer's vehicles and
   service history from the connected dealer-management/telematics
   system; a "Not connected" notice means the integration couldn't be
   reached, and a "Mock data" notice means the results shown are demo
   data rather than a live system.

[[SCREENSHOT: ch03-customer360-dms | Customer 360 results showing vehicle and service history]]

### Example scenario

A dealer calls asking whether a customer who bought a Proton e.MAS 7 has
any open issues. The operator searches Customer 360 by the customer's
phone number and sees, in one place, an open WhatsApp conversation about
a charging fault, a closed RSA incident from the previous month for the
same vehicle, and — since the dealership's DMS/TSP connection is
configured — that vehicle's last two service visits, without switching
between separate systems.

### Integrations & automation

Customer 360 doesn't store anything of its own — it reads live from
Chatwoot's contacts and conversations, from the RSA Incident Log
(covered in the RSA Incident Log chapter), and, when enabled, from the
DMS/TSP connection configured under the Administration chapter's
Integrations section. If that connection isn't configured or isn't
reachable, Customer 360 still works — it simply shows the CRM's own data
without the vehicle/service-history block.
