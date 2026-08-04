# Package D — 100 seeded demo records + per-case detail view

**Date:** 2026-08-04
**Covers demo-feedback items:** #6 in the 2026-08-04 request — mock the per-ticket detail dashboard (#14) and the Customer 360 lookup (#15) with 100 dummy records
**Type:** a seeding tool plus a modest UI addition.
**Effort:** medium.

---

## 1. Goal

Make Customer 360, the per-case view, and the reporting pages demoable with
data that looks like Proton's real operation instead of three test contacts.
100 customers, their conversations, and their RSA incidents — all reversible.

**Decision taken: seed the `proton` tenant, every record tagged so a cleanup
script can remove them all.**

## 2. Why seeded data is the right call here

Both #14 and #15 are built but unconvincing to demo, because an empty search box
proves nothing. This package is what turns Package B's Contacts/360 merge and
Package E's reporting pages from "the page loads" into "the page shows a
plausible customer base". It is demo scaffolding with a delete button, not a
product feature — and the spec should keep it that way.

## 3. Data model — shaped by the client's own reports

The decks (`MONTHLY REPORTING FOR Proton e.MAS.pptx`, `Weekly Report Proton
e.MAS.pptx`) define the real vocabulary. Generated data uses it verbatim so
that Package E's reporting pages have something meaningful to aggregate:

- **Vehicle models:** `e.MAS 5`, `e.MAS 7`, `e.MAS 7 PHEV`, plus a share of `NA`
  (the decks carry an explicit NA column — customers who haven't bought yet).
- **Case type:** Inquiry / Complaint / Feedback, roughly in the decks' observed
  proportions (June: 1024 / 770 / 17 — feedback is genuinely rare, so don't
  generate a tidy third each).
- **Division:** Sales, After Sales, Apps, Charging, Product, Marketing, Others.
- **Concern (subcategory):** taken from the decks per division — e.g. Sales →
  Accessories, Booking, Delivery, Promotion, Trade In, Transfer Ownership;
  Charging → Home Charging; Apps → Information, Profile, Auto Logout;
  After Sales → Body, Spare Part, User Manual, Service Operation, ADAS.
- **Channel mix:** WhatsApp ~73%, Phone ~16%, Email ~9%, Social ~2% (the weekly
  deck's actual split).
- **Vehicle numbers:** Malaysian plate format (`WXY 1234`), unique per customer.
  Written as a `vehicle_no` custom attribute on both the contact and its
  conversations — the same field Package E's gap G3 introduces for reporting
  and Package B needs for a true plate lookup. If this package lands first, it
  defines the attribute; if E lands first, reuse it rather than inventing a
  second name.
- **Purchased-from dealer** (`purchased_from`), likewise, so the WIP tables in
  Package E have a populated column to render.
- **Phone numbers:** Malaysian mobile format, unique, and in a **reserved test
  range so a demo can never dial or message a real person.**
- **Names/emails:** obviously synthetic (`demo.<n>@example.invalid`), never
  real-looking PII.
- **Status/aging:** a spread across open/pending/resolved with created dates
  over the last ~8 weeks so the aging buckets and WoW deltas aren't empty.
- **Dealers:** the dealer names already used by the escalation routing config,
  with a minority of cases escalated so dealer TAT reporting has rows.

Roughly 100 contacts → ~250-300 conversations (customers with more than one
case are what makes a 360 view interesting) → ~30 RSA incidents.

## 4. Design

### 4.1 A script, not a feature

`deploy/scripts/seed-demo-data.py`, run manually against one tenant. It is not
wired into the app, not exposed over HTTP, and not shipped in an image. Two
subcommands:

- `seed --tenant proton --count 100` — create the data
- `purge --tenant proton` — delete everything it created

Deterministic by default (fixed seed), so a re-run produces the same names and
plates and a demo script stays valid.

### 4.2 Reversibility is the requirement that matters

Every created object carries `custom_attributes.demo_seed = "<batch-id>"`:
contacts, conversations, and RSA incidents alike. `purge` finds and deletes by
that marker only. Two safety rules, both non-negotiable:

- `purge` **never** deletes an object lacking the marker, whatever else matches;
- `seed` refuses to run against a tenant unless the tenant name is passed
  explicitly — no default, so a fat-fingered invocation can't hit production.

Print a summary of what will be created/deleted and require confirmation, since
this writes to a tenant a client will see.

### 4.3 Writing the data

- **Contacts and conversations:** the Chatwoot Application API with a tenant
  admin access token. `ChatwootAdapter` exists but is shaped for the live
  conversation flow (`_find_or_create_contact`, `_find_or_create_conversation`);
  the seeder needs plain creates with explicit attributes and its own pacing, so
  it calls the API directly rather than bending the adapter.
- **Messages:** two to six per conversation, alternating customer/agent, using
  concern-appropriate canned text (a delivery-delay complaint reads like one).
  This is what makes the 360 panel and any transcript view look real.
- **RSA incidents:** `POST /rsa/incidents` on the backend, with the same
  vehicle numbers used for the contacts so a vehicle search finds both a
  conversation and an incident — the exact path feedback #15 asked for.
- **Rate limiting:** a small delay between calls. A burst of ~1500 API calls
  against the tenant's Rails app is a self-inflicted outage.

### 4.4 Per-case detail view (#14)

With data in place, the per-case ask is answered in two places, both cheap:

1. The **360 panel** from Package B lists a customer's cases with the fields
   Proton named live (caller, phone, status) plus channel, division/concern,
   plate, and age.
2. A **Cases list** view — one table, all cases, filterable by division,
   case type, status, channel, and dealer, with the columns the decks' WIP
   tables use: Case ID, Division, Concern, Purchased From, Escalated To, Car
   Plate, Aging (days), Status. This is deliberately the same shape as the
   deck's outstanding-cases table, so it doubles as a reporting surface
   (Package E reuses it rather than building a second table).

Both read existing data; no new backend storage.

### 4.5 Where the data does *not* go

Seeded conversations must not trigger the AI bot, escalation emails, or
WhatsApp sends. Create them in a state the bot ignores (not `pending` on a
bot-enabled inbox), and run with `EMAIL_ESCALATION_ENABLED` off during
seeding. **Verify this on `default` before ever running against `proton`** —
100 accidental escalation emails to a real address is the obvious way this
goes wrong.

## 5. Testing

- Unit: generator produces the intended distributions, unique plates/phones,
  every record carries the batch marker.
- Unit: `purge` skips unmarked objects (assert explicitly with a mixed set).
- Integration: seed 5 records against `default`, verify in the UI, purge, verify
  clean.
- Then, and only then, run 100 against `proton`.

## 6. Risks

- **Demo data mistaken for real data.** Anyone reading proton's reports after
  seeding sees inflated numbers. Mitigate by naming contacts with a visible
  `[DEMO]` prefix and telling Proton the data is synthetic before the demo. If
  that's unacceptable for a client-facing tenant, fall back to a dedicated demo
  tenant.
- **Reporting contamination.** Package E's BigQuery sync will ingest these
  rows. **Decided (2026-08-04): flag-controlled exclusion, default off.**
  `METRICS_EXCLUDE_DEMO_SEED` (backend `Settings`, `deploy/tenants/*.env`)
  defaults to `false`: behaviour stays byte-identical to today, so
  demo-seeded conversations flow into the warehouse and Package E's
  reporting pages have data to show during the demo window. Excluding them
  unconditionally was rejected — on `proton`, real volume is sparse enough
  that the pages would sit near-empty with nothing to reconcile against the
  client's decks. Accepting the inflation permanently was also rejected —
  that relies on remembering to purge, and a forgotten purge is exactly how
  demo data ends up mistaken for real data (the risk above). The flag gets
  both: `run_sync` (`backend/apps/backend/src/chatbot/features/metrics/sync.py`)
  drops any conversation whose `custom_attributes.demo_seed` marker is set
  before it reaches BigQuery once the flag is true. **The operator must set
  `METRICS_EXCLUDE_DEMO_SEED=true` on `proton` before any real reporting
  run** — this is not automatic, and forgetting it silently keeps seeded
  rows flowing.
- **Purge misses something** and demo records linger. Hence the batch id and the
  dry-run summary.

## 7. Out of scope

- Seeding phone calls or recordings (Package C owns those).
- Backdating BigQuery metrics rows directly — the seeder writes CRM data and
  lets the existing sync derive metrics; faking the warehouse would make
  Package E untestable against reality.
- Any UI for seeding. It's a script.

## 8. Definition of done

`seed --tenant proton --count 100` produces a believable customer base; a
vehicle-number search returns both a conversation and an RSA incident; the
Cases list and 360 panel show the deck's fields; `purge` removes every trace;
and the run has been rehearsed on `default` first.
