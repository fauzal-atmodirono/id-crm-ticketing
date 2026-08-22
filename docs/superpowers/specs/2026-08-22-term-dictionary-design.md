# Term dictionary — an industry-neutral CRM with an automotive preset

**Date:** 2026-08-22
**Status:** design, awaiting approval
**Branch:** dev-yuda

## The problem

The product's vocabulary is automotive, because its first customer is. A
tenant in banking, retail or logistics is shown "Dealer Escalation
Turnaround", "Vehicle Model", "RSA Incident Log" and "WIP No" — words that
are not merely off-brand for them but meaningless.

Measured across the fork: Dealer appears 233 times in added lines, RSA 315,
PIC 201, DMS 86, Vehicle 58, WIP 14.

## What we are building

A **term dictionary**: a closed set of nouns whose display text is resolved
per tenant, with two profiles at launch.

- **`generic`** — industry-neutral. The default for every new tenant.
- **`automotive`** — mirrors Proton's current wording exactly, so the next
  automotive customer is a profile selection rather than a fork.

A tenant picks a profile; it may then override individual nouns (a bank that
prefers "Branch" to "Partner"). Overrides are only accepted for keys already
in the noun list.

## The noun list

Ten nouns. This list is **closed** — see "Why it is capped" below.

| key | generic (default) | automotive (Proton) | appears in |
|---|---|---|---|
| `partner` | Partner | Dealer | escalation routing admin, escalation groups, turnaround report, case filters |
| `partner_principal` | Partner Manager | Dealer Principal | escalation ladder role |
| `partner_owner` | Partner Owner | Dealer Owner | escalation ladder role |
| `partner_rep` | Partner Rep | Dealer CRE | escalation ladder role |
| `asset` | Asset | Vehicle | Customer 360, case record panel, reports |
| `asset_model` | Asset Type | Vehicle Model | category-by-model report, case fields |
| `asset_id` | Asset ID | Vehicle No. | Customer 360 lookup, case fields |
| `asset_serial` | Serial No. | Chassis No. | case record panel |
| `field_incident` | Field Incident | RSA Incident | incident log page + nav |
| `job_no` | Job No. | WIP No. | cases list, case record panel |
| `partner_system` | Business System | DMS/TSP | integration card |

That is eleven rows; `partner_principal`/`partner_owner`/`partner_rep` are
counted as one noun's role variants rather than three independent nouns, and
they move together with `partner`.

### Deliberately NOT in the dictionary

- **PIC.** Ordinary business English across SEA, and it reads correctly to a
  bank as readily as to a dealership. Generalising it to "Owner" or "Person
  in Charge" would make every tenant's UI worse to serve a problem no tenant
  has. Kept verbatim, as you called it.
- **Department names** ("Aftersales", "Sales", "Service"). These are not UI
  strings we own — they come from the per-tenant taxonomy store, already 346
  nodes on proton, already tenant-configurable. Putting them in the
  dictionary would create a second place to change the same word.
- **Case, Conversation, Contact, Agent, Inbox.** Already industry-neutral,
  and several are Chatwoot's own vocabulary. Renaming them would fork the
  product away from its upstream for no gain.
- **CSAT, NPS, SLA.** Standard across industries.

### Why it is capped

A term dictionary has one well-known failure mode: it grows until every
string in the app is a lookup. The screens then become half-translated, and
nobody can grep for text they saw in the UI. The cap is the design.

The rule: a noun enters the dictionary only if it is **wrong**, not merely
suboptimal, for a tenant outside the originating industry. "Dealer" shown to
a bank is wrong. "Partner" shown to a dealership is suboptimal. Only the
first kind qualifies.

## Scope: display strings only

The dictionary resolves **rendered text**. It does not touch:

- **Data keys.** `dealer_escalated_at` is a Chatwoot custom attribute already
  written onto live conversations; `dealer_<slug>` are labels on production
  conversations.
- **API fields.** `vehicle_no`, `vehicle_model`, `vehicle_plate`,
  `vehicle_chassis` — request/response contracts, 290+ references.
- **Warehouse columns.** `category_by_vehicle_model` is a BigQuery view
  column that BI reads.
- **Python/JS identifiers.** `DealerStore`, `dealer_store`,
  `ProtonDealerEscalationSection.vue`.

Renaming any of those is a production data migration plus a BI break, bought
for a naming preference on text nobody sees. The boundary is non-negotiable:
**the dictionary changes what a human reads, never what a system stores.**

New code follows the generic vocabulary in its identifiers too — same rule
already adopted for the `Proton*` prefix: stop adding to the automotive
surface, do not go back and unpick it.

## Architecture

### Registry and resolution

`features/platform/term_dictionary.py`:

- `TERM_REGISTRY` — the closed noun list above, each entry carrying
  `singular`, `plural`, and `lower`.
- `PROFILES = {"generic": {...}, "automotive": {...}}` — the two columns of
  the table.
- `resolve_terms(profile, overrides)` → a flat `{key: {singular, plural,
  lower}}` map, overrides applied on top of the profile, unknown override
  keys ignored rather than raising.

`lower` is stored explicitly rather than derived by `.lower()`. Deriving it
produces "rsa incident" and "dms integration", which is precisely the kind of
half-broken output that makes people distrust a terminology layer. Acronym
terms carry their own lowercase form.

### Storage

Same store and same superadmin page as the custom-feature switchboard (see
`2026-08-22-platform-feature-switchboard-design.md`) — one page where the
superadmin decides what this tenant *has* and what it *calls things*. A
`profile` string plus an `overrides` map, per tenant.

### Read path

Terms ride on the existing custom-features response rather than adding a
second round trip: `GET /admin/custom-features` returns `{features: [...],
terms: {...}, is_superadmin: bool}`. `useCustomFeatures.js` exposes a `t()`
helper alongside `hasFeature()`.

**Fallback when the store is unreachable is `generic`**, not the last-known
value. A tenant briefly seeing neutral wording is a much smaller failure than
a bank's operators briefly seeing "Dealer".

### Rendering

Fork patch replacing the hardcoded nouns in the ~30 affected components with
`t('partner')` / `t('asset_model')` calls. Headings, table columns, form
labels, empty states, toast messages.

## Rollout — `default` first, proton untouched

1. **Backend + pin every existing tenant**: registry, profiles, resolution,
   response field, and a profile row for proton (`automotive`), aeon360 and
   default (both `generic`). No SPA change, so nothing renders differently
   anywhere — the rows are inert on the day they are written.
2. **Chatwoot image** with the `t()` call sites. `default` is not live, so it
   is the safe place to see every screen in neutral wording first.
3. **aeon360** pulls the image and renders generic.
4. **proton: no image pull, no restart, no env edit.** Its row from step 1
   means that whenever it does eventually pull a later image — on its own
   schedule, for its own reasons — it keeps saying Dealer, Vehicle, RSA
   and WIP.

### Every existing tenant is pinned in step 1, not later

An earlier draft made step 1 backend-only and left proton's profile to be
written "before its next Chatwoot image pull". That is a trap dressed as a
runbook step. The `t()` call sites live in the SPA, so proton renders no
resolved term while it runs its current image (`c9c4828` / `-rc9`) — but the
moment it pulls **any** later Chatwoot image, for a completely unrelated fix,
it gets the call sites. With no profile set it would resolve to `generic`,
and proton's operators would open the CRM to "Partner Escalation Turnaround"
where it has always said "Dealer". Nothing about that pull would look
vocabulary-related to whoever performs it.

So the pin is not a follow-up ops step. **Step 1 writes a profile row for
every tenant that exists**, in the same change that creates the store:

| tenant | profile |
|---|---|
| proton | `automotive` |
| aeon360 | `generic` |
| default | `generic` |

All three writes are invisible on the day they happen — no tenant is running
an image containing a `t()` call — which is exactly what makes step 1 the
safe place for them. `add-tenant.sh` writes `generic` for every tenant
provisioned afterwards, so no tenant ever relies on the fallback.

The fallback for a tenant with no row stays `generic`, because the product's
default vocabulary should be the neutral one rather than a vertical. That is
defensible only because the fallback is unreachable for every real tenant:
a vertical-as-default would otherwise be one skipped provisioning step away
from showing a bank the word "Dealer".

A test asserts proton resolves to `automotive`, so the pin cannot be
regressed by a later edit to the profile table.

## Testing

- Every `TERM_REGISTRY` key resolves in both profiles — no key can be added
  to one column and forgotten in the other.
- `automotive` resolves to the exact strings in the fork today, asserted
  against the literal current wording, so the preset is provably a mirror
  rather than an approximation.
- Unknown override keys are ignored, not raised.
- Store unreachable → `generic`, never a partial map.
- **proton resolves to `automotive`.** Asserted directly against the tenant
  pin table, so the pin cannot be silently dropped by a later edit.
- Acronym terms have an explicit `lower` that is not the naive `.lower()` of
  the singular.

## What this does not do

- No general i18n. This is a noun dictionary, not a translation layer; the
  surrounding sentences stay English.
- No renaming of data keys, API fields, warehouse columns or identifiers.
- No third profile. Banking/retail presets are a later exercise once a real
  tenant in that industry exists to argue with — inventing one now would be
  guessing at vocabulary nobody has asked for.
