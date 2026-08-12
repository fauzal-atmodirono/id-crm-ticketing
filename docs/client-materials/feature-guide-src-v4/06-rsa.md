# RSA Incident Log
<!-- TRAINING: audience=supervisor -->

## Logging an RSA incident
<!-- TRAINING: audience=supervisor, exercise -->

### What it is

The RSA Incident Log is a standalone page for recording
roadside-assistance (RSA) incidents — breakdowns, accidents, and similar
callouts — as they're reported. Each incident is its own record with the
vehicle, the reported cause, and where and when it happened. It's manual
data entry: logging an incident here doesn't dispatch a tow truck or
notify anyone by itself.

### Where to find it

**RSA Incident Log** in the main sidebar. In the current release this
page shares its visibility with the SLA Policies page — an administrator
needs the same permission that controls SLA Policies (see the
Administration chapter) to see it.

### How to use it

1. Open **RSA Incident Log** from the sidebar.
2. Review the **Cases by cause** and **Cases by dealer** summary at the
   top of the page for a running total of incidents logged so far.
3. Under **Log an incident**, fill in **Incident date**, **Vehicle no.**,
   and **Cause** (all required).
4. Fill in whichever of the optional fields you already know: Vehicle
   model, Purchased from, Breakdown location, Arrived location, Customer
   called-in time, Towing assigned time, Time arrived breakdown area,
   Time arrived outlet, Total km, Late reason, and Remarks.
5. Click **Log incident** to save it. It appears in the incidents table
   below, and the cause/dealer summary at the top updates immediately.

[[SCREENSHOT: ch06-rsa-new-incident | Creating a new RSA incident record]]

### Example scenario

A customer calls the roadside-assistance line about a flat battery on the
Jakarta–Cikampek toll road. Staff open the RSA Incident Log, log a new
incident with today's date, the vehicle's plate number, cause "Flat
battery / SOC 0%", and the breakdown location, leaving the
still-unknown timing fields blank for now.

### Integrations & automation

There's no dispatch-system integration behind this form — logging an
incident is a record for tracking and reporting, not an automatic
trigger to send a tow truck. What you log here is what later appears in
Customer 360 and in reporting (see the sections below).

## Incident statuses & updates

### What it is

Rather than a single status label, an incident's progress is tracked
through the timestamp fields captured when it was logged — when the
customer called in, when towing was assigned, when the tow arrived at
the breakdown location, and when the vehicle arrived at the outlet —
plus free-text Late reason and Remarks fields for anything that needs
explaining. As new information comes in, staff update the same incident
record rather than creating a new one.

### Where to find it

The incidents table at the bottom of the RSA Incident Log page; each row
has **Edit** and **Delete** actions.

### How to use it

1. Find the incident in the table and click **Edit**.
2. Fill in each stage's field as it happens — for example, Towing
   assigned time once a tow truck is dispatched, then Time arrived
   breakdown area and Time arrived outlet as the vehicle progresses.
3. Add Total km and a Late reason if relevant.
4. Click **Save** to update the record, or **Cancel** to discard the
   changes.
5. Use **Delete** to remove a record entered in error — this cannot be
   undone.

[[SCREENSHOT: ch06-rsa-status | Updating the status of an RSA incident]]

### Example scenario

After logging the initial callout, staff assign a tow truck and edit the
incident to add the Towing assigned time. Once the vehicle reaches the
dealer's service outlet, staff edit the same incident again to fill in
Time arrived outlet and Total km, completing the record without creating
a second one.

### Integrations & automation

Every edit is reflected immediately in the Cases by cause and Cases by
dealer summary at the top of this page, since that summary is calculated
from the current incident records, not a separate log.

## RSA in Customer 360 & reports

### What it is

Incidents logged here surface in exactly two other places: an operator
searching Customer 360 by a vehicle number sees any matching incidents
alongside that customer's contact and conversations, and this page's
own Cases by cause/Cases by dealer summary (above) totals the same
incident records. RSA incidents are their own separate record, kept
apart from conversation data — they are not part of what feeds the
Reports chapter's Departments & PIC or Case Lifecycle reports, so an
incident logged here will not show up on either of those report pages.

### Where to find it

The RSA incidents table inside a Customer 360 lookup (see the Contacts
chapter), and the Cases by cause/Cases by dealer summary on this page —
see What it is, above, for why the Reports chapter's own report pages
aren't a third place to look.

### How to use it

1. Go to **Customer 360** and search by the vehicle's number.
2. Review the **RSA incidents** table in the results — it lists the same
   fields as this page's incident table.
3. Alternatively, stay on the RSA Incident Log page and use the Cases by
   cause/Cases by dealer summary for a running total without doing a
   per-vehicle lookup.

### Example scenario

A breakdown call comes in for a customer's vehicle; staff log the
incident, arrange towing, and update the record as the vehicle reaches
the dealer outlet. Weeks later, that same dealer calls asking about the
customer's vehicle history; staff open Customer 360, search the vehicle's
plate number, and the completed incident — with its full timeline —
appears alongside the customer's conversations.

### Integrations & automation

Customer 360's vehicle-number search matches against each incident's
Vehicle no. field, so entering that field accurately when logging an
incident (see Logging an RSA incident, above) determines whether it will
be found later from Customer 360.
