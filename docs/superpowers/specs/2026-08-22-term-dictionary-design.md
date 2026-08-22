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
- **`automotive`** — mirrors Proton's current vocabulary, so the next
  automotive customer is a profile selection rather than a fork. Not a
  byte-for-byte mirror: roughly 15 strings differ cosmetically where
  converting a call site was also an opportunity to fix an inconsistency
  already present in the fork (`"Vehicle no."` → `"Vehicle No."`,
  `"DMS / TSP"` → `"DMS/TSP"`, and two acronym expansions that are dropped
  rather than repeated at every call site). The noun itself is unchanged in
  every case.

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

**When the store is unreachable, the router answers 503, not 200 with an
empty map.** `CustomFeatureStore.get_document()` raises
`CustomFeatureStoreUnavailable`, and `/admin/custom-features` turns that into
an HTTP 503 rather than a 200 carrying an empty `terms`/`features` payload —
inherited from the sibling feature-switchboard plan, where a 200-with-empty
was found to blank two live tenants' CRMs on a transient Firestore blip.
`useCustomFeatures.js`'s existing `.catch()` handles that 503 the same way it
already handles a features fetch failure: it schedules a retry and leaves
`terms`/`termProfile`/`termProfiles` exactly as they were, so a page that had
already loaded once keeps showing its last known vocabulary through an
outage. A **cold load with no prior fetch** has no last-known value to fall
back to, so `t()` renders its own fallback instead: the raw key with
underscores turned to spaces (e.g. `field_incident` → "field incident")
rather than an empty label or the automotive wording. That fallback is a
last resort, not a design goal — it is what a first-ever page view shows
during an outage, and it is deliberately still greppable.

### Rendering

Fork patch replacing the hardcoded nouns in the ~30 affected components with
`t('partner')` / `t('asset_model')` calls. Headings, table columns, form
labels, empty states, toast messages.

## Rollout — `default` first, proton untouched

1. **Backend**: registry, profiles, resolution, `TERM_PROFILE` setting,
   response field. Nothing renders differently anywhere — there are no `t()`
   call sites yet.
2. **`default`**: set `TERM_PROFILE=generic` in `default.env`, then ship the
   Chatwoot image with the `t()` call sites. `default` is not live, so it is
   the safe place to see every screen in neutral wording first.
3. **aeon360**: set `TERM_PROFILE=generic`, then pull the image.
4. **proton: nothing at all.** No row, no env var, no image pull, no restart.
   It falls through to the `automotive` default and keeps its vocabulary
   whenever it eventually pulls a later image, on its own schedule.

### The pin is a fallback, not a remembered write

An earlier draft had step 1 write a profile row for every existing tenant,
keyed by tenant name. That is wrong twice over.

First, it needs the backend to know which tenant it is, and this codebase has
already decided against deriving behaviour from the compose `TENANT` name —
see `app_environment` in `config.py`: *"a guard whose answer is guessed from a
name someone chose for unrelated reasons is one rename away from being wrong
in the dangerous direction."* A `{"proton": "automotive"}` lookup is exactly
that guard.

Second, a written row is still a thing somebody has to have done. If it is
missed, proton flips to generic on its next image pull.

So the profile resolves through a fallback chain instead, with no boot-time
write and no tenant-name lookup anywhere:

```
profile = stored_profile (superadmin's choice, if ever set)
       or settings.term_profile (TERM_PROFILE env, default "automotive")
```

**`TERM_PROFILE` defaults to `automotive`**, which is this codebase's usual
default-preserving posture — unset means *today's behaviour, byte-identical*.
proton has no row and no env var, so it resolves to automotive and keeps
saying Dealer, Vehicle, RSA and WIP forever, with nothing to remember and
nothing to write.

The cost is real and worth stating: the *product's* default vocabulary is
then a vertical, so a tenant provisioned without `TERM_PROFILE=generic` would
show a bank the word "Dealer". That is the safer of the two failures. Getting
it wrong in this direction is loud and immediate — someone opens the new
tenant during onboarding and sees the wrong words. Getting it wrong in the
other direction is silent and lands on a live customer months later, during a
deploy nobody connected to terminology. `add-tenant.sh` writes
`TERM_PROFILE=generic` so the loud failure does not happen either.

Once a superadmin picks a profile in the UI, the stored value wins and the
env var stops mattering for that tenant.

## Testing

- Every `TERM_REGISTRY` key resolves in both profiles — no key can be added
  to one column and forgotten in the other.
- `automotive` resolves to the exact strings in the fork today, asserted
  against the literal current wording, so the preset is provably a mirror
  rather than an approximation.
- Unknown override keys are ignored, not raised.
- Store unreachable → the router answers 503 (never a 200 with a partial or
  empty map); the SPA's `.catch()` keeps whatever vocabulary was already
  loaded, and `t()`'s own fallback (humanised key, e.g. "field incident")
  covers the cold-load case where there is no prior vocabulary to keep.
- **A tenant with no stored profile and no `TERM_PROFILE` resolves to
  `automotive`** — proton's exact situation, asserted so the default cannot
  be flipped to `generic` by a later edit without a test going red.
- A stored profile beats the env var; the env var beats the built-in default.
- Acronym terms have an explicit `lower` that is not the naive `.lower()` of
  the singular.

## What this does not do

- No general i18n. This is a noun dictionary, not a translation layer; the
  surrounding sentences stay English.
- No renaming of data keys, API fields, warehouse columns or identifiers.
- No third profile. Banking/retail presets are a later exercise once a real
  tenant in that industry exists to argue with — inventing one now would be
  guessing at vocabulary nobody has asked for.
