# Platform feature switchboard — every tenant ships whole, starts empty

**Date:** 2026-08-22
**Status:** design, awaiting approval
**Branch:** dev-yuda

## The problem

A freshly provisioned tenant does not open empty. It opens with whatever
`PROTON_FEATURES` happens to contain — in practice
`ai_assist,nav_menu,copilot,knowledge`, because that is the compose default
and `${PROTON_FEATURES:-...}` falls back to it on an empty value as readily
as on an absent one. Every other custom surface (Cases, Taxonomy, Workforce,
Customer 360, SLA policies, RSA incidents, the report pages, Integrations,
Audit log, Roles & Permissions) is gated on an RBAC *permission* instead, and
`seed_defaults` grants the `administrator` role every permission in the
registry. So the first person to log into a new tenant sees nearly the whole
product.

Meanwhile the real control surface is 47 environment variables in a file on
the VM. proton has 47 flags on; `default` has 3. Changing what a customer
has means a `sed` and a redeploy, by hand, on production.

## What we are building

Every tenant ships with the **whole** feature set — same image, nothing
withheld at build or deploy time — and **every feature starts off**. A new
tenant's first deployment looks exactly like `default` does today: a blank
CRM. The platform superadmin then switches on what that customer bought,
from a page inside the CRM, with no deploy.

Two gates, deliberately orthogonal:

- **Feature** — *is this capability part of this tenant's product at all?*
  Owned by the platform superadmin (the vendor). Per tenant.
- **Permission** — *which of the enabled capabilities may this person use?*
  Owned by the tenant's own administrator, through the existing Roles &
  Permissions page. Per role.

A surface renders only when both agree: `hasFeature(f) && hasPermission(p)`.
With every feature off, nothing renders regardless of how permissive the
tenant's roles are — which is the "starts empty" requirement, and it holds
even against a tenant admin who grants themselves everything.

This orthogonality is the core of the design. Today the two questions are
conflated: page visibility is driven by permission alone, so the only way to
withhold a capability from a customer is to withhold it from each of their
users individually, and nothing stops their admin granting it back.

### There is no licensing ceiling

An earlier draft kept `PROTON_FEATURES` as a per-tenant ceiling bounding what
the superadmin could switch on. That is dropped, deliberately.

A ceiling only earns its keep if someone *other than the vendor* can flip
switches — it bounds a power you have delegated. Here the switchboard is
superadmin-only and invisible to the tenant's own admin, so the ceiling was a
second lock on a door only one person can open. It added a concept, a failure
mode ("enabled but not licensed"), and a production env edit to every
rollout, in exchange for no authority that the switchboard did not already
provide.

`PROTON_FEATURES` and the ERB block that stamps it therefore become **inert**
once the SPA reads the store (see Rollout). They are left in place rather than
unpicked — patches 0001 and 0058 own that code and are load-bearing for other
things — but an env var that looks live and decides nothing is its own trap,
so it is documented as vestigial in `example.env` and removed in a follow-up
once no gate reads it.

Note there is no `CUSTOM_FEATURES` env var replacing it. With the ceiling
gone the store is the only input, and reintroducing an env var — under any
name — would put back the second source of truth this section just removed.
The generic naming applies to the concept and the code (below), not to a
resurrected variable.

## Naming: "custom features", not "Proton features"

Everything new in this design is named generically. The platform is meant to
be sold to any tenant, and proton is one customer of several — a switchboard
literally called `PROTON_FEATURES` is awkward to show an AEON360 operator.

| thing | name |
|---|---|
| backend module | `features/platform/custom_features.py` |
| store | `CustomFeatureStore` |
| registry | `CUSTOM_FEATURE_REGISTRY` |
| endpoints | `GET/POST /admin/custom-features` |
| composable | `useCustomFeatures.js` |
| SPA page | `CustomFeaturesPage.vue` |
| route | `custom_features` at `admin/custom-features` |

This does **not** start a rename of the existing fork. The `Proton*`
component prefix, the `proton_*` route names, `useProtonConfig`,
`PROTON_BACKEND_URL` and the rest stay exactly as they are: renaming them
touches all 70 patches, every route name the SPA resolves, and live env files
on production, for no behavioural gain. The rule adopted here is narrower and
cheap to hold — **new code stops adding to the Proton-branded surface.**
De-branding what already exists is a separate piece of work with its own
risk profile, worth doing when something else already forces those files open.

## Who the superadmin is

`is_superadmin(user_id, profile_type) = (user_id == 1) or (profile_type == "SuperAdmin")`

Verified live on 2026-08-22 across all three tenants:

| tenant  | id 1                            | type         | other SuperAdmins |
|---------|---------------------------------|--------------|-------------------|
| aeon360 | `yuda.adi.pratama@devoteam.com` | `SuperAdmin` | id 4              |
| proton  | `yudaa0110@gmail.com`           | `SuperAdmin` | ids 6, 8          |
| default | `yuda.adi.pratama@devoteam.com` | `SuperAdmin` | —                 |

Both halves earn their place:

- **`user_id == 1` is an unrevocable floor.** Id 1 is the person who set the
  platform up, on every tenant. Hardcoding it means no sequence of
  administrative accidents can lock the platform owner out of the switchboard
  — the classic failure where the last privileged user removes their own
  privilege. It is never stored and can never be revoked. This convention is
  already load-bearing here: proton sets `RBAC_BOOTSTRAP_ADMIN_USER_ID=1`,
  which auto-assigns user 1 the `administrator` role at boot
  (`main.py:1093`).
- **`type == "SuperAdmin"` is Chatwoot's own concept**, already in
  multi-person use here (three superadmins on proton, two on aeon360).
  `SuperAdmin < User`, STI on `users.type`, and `/api/v1/profile` already
  exposes it (`json.type resource.type` in `_user.json.jbuilder`) — the same
  endpoint `TokenValidator` already calls.

**Granting superadmin to another person needs no new code.** Chatwoot's
`/super_admin` console already does it, and is already how the existing
superadmins were made. We deliberately do *not* build a parallel grant store:
a second list of superadmin ids would be free to disagree with `users.type`,
producing a user revoked in our UI but still a Chatwoot superadmin, or the
reverse. One source of truth.

Implementation note that will bite if missed: a regular user's `type` is
`nil`, **not** `"User"`. The check must be an equality test against
`"SuperAdmin"`, never a truthiness test.

### Feature management is deliberately NOT an RBAC permission

The obvious-looking move — add `features.manage` to `PERMISSION_REGISTRY` —
is wrong, and worth stating so nobody re-adds it later. `seed_defaults`
grants the `administrator` role *every* key in that registry. A
`features.manage` key would therefore be auto-granted to each tenant's own
admin, handing the customer the power to switch on surfaces they did not buy.
That is precisely the authority boundary this design exists to draw.

The switchboard's write path is gated by a new `require_platform_superadmin`
dependency instead, sitting outside RBAC entirely.

The converse also needs saying: a Chatwoot SuperAdmin is treated as holding
**all** RBAC permissions. Without this, the platform owner is locked out of a
tenant's Roles & Permissions page on any tenant where they were never
assigned a role — which is most of them.

### The switchboard is invisible to tenants

A non-superadmin gets no nav entry, no route, and a 403 on the write endpoint.
They cannot enumerate which features exist but are off. This is a deliberate
choice against a read-only "here's what else you could buy" view: an upsell
surface inside the customer's own admin console invites questions we would
rather answer in a sales conversation, and it leaks the product roadmap of
every other tenant.

## Architecture

### Feature registry (static)

`features/platform/custom_features.py` — a static dict, one entry per
feature: key, label, description, group, `kind`, and the RBAC permission it
pairs with. Static because a feature that can be enabled by typing its name
into a store is one that can be enabled by *mistyping* something else. The
registry is the closed set of things that exist.

`kind` distinguishes two genuinely different things, and the distinction
drives the phasing below:

- **`surface`** — a page, panel or nav entry. Gating it means the SPA not
  rendering it. This is the whole of Phase 1.
- **`behavior`** — a backend runtime path with no UI of its own (the
  lifecycle scanner, escalation email, recording transcription, KB-grounded
  replies, auto-routing). Gating it means the backend consulting the store at
  runtime rather than `Settings` at boot.

**Phase 1 implements `surface` keys only.** `behavior` keys are registered
and listed in the admin page as read-only "env-controlled", showing their
current value, so the page tells the whole truth about the tenant from day
one rather than implying the 47 env flags do not exist. Phase 2 moves them to
the store. This is called out because a switchboard that silently omits half
the tenant's configuration is worse than one that shows it and says who owns
it.

Surface keys (Phase 1), grouped as the admin page will show them:

| group | keys |
|---|---|
| AI | `ai_assist`, `copilot`, `faq_suggestion_popup`, `translate` |
| Knowledge | `knowledge` |
| Reports | `reports_departments`, `reports_case_lifecycle`, `reports_anomaly`, `reports_weekly` |
| Cases | `cases`, `taxonomy`, `rsa_incidents` |
| Operations | `workforce`, `agent_softphone`, `sla_policies`, `escalation_routing`, `inbound_alerts`, `alert_preferences`, `agent_status`, `agent_priorities` |
| Data | `customer360`, `integrations` |
| Admin | `audit_log`, `roles_permissions` |

`knowledge` stays a single key covering all eight console sections
(Assistants, FAQs, Documents, Playground, Scenarios, Inboxes, Tools,
Settings) — they are one console and splitting them would produce a nav
section that can be half-present for no operator benefit.

`nav_menu` is retired as a key: it gates the nav container rather than a
capability, and with per-surface keys an empty nav follows from every surface
being off.

### Feature store (per tenant)

`CustomFeatureStore` (same module) — Firestore-backed, the same shape as
the existing `PicStore`/`DealerStore`, which is this codebase's precedent for
operator-editable per-tenant config. Maps feature key → bool.

**An absent key is off.** A store that has never been written yields a
completely blank CRM. There is no seeding, no migration, no first-boot
marker, and no default-on list anywhere — "starts empty" is a property of the
data model rather than a value someone has to remember to set.

### Read path

`GET /admin/custom-features` — returns the enabled key list plus
`is_superadmin` for the calling session. Authenticated as any signed-in
Chatwoot session; there is nothing sensitive in "what is switched on in the
CRM I am already looking at".

`useCustomFeatures.js` — a new composable mirroring `useProtonPermissions.js`
exactly: module-level cache, single in-flight promise, fail-closed to `[]`.

Patch 0058 deliberately avoided making `hasFeature` async, on the grounds that
auditing every synchronous call site for a load-order race was a large change
to verify. That reasoning was sound then and is weaker now:
`useProtonPermissions.js` has since shipped and does this exact thing for nav
gating, so the pattern is proven in this fork rather than novel. The
synchronous `useProtonConfig().hasFeature` stays in place so nothing that
reads it today breaks; new gating uses the composable.

**The cost, stated plainly:** fail-closed means a backend blip briefly renders
the CRM empty rather than briefly rendering surfaces the tenant should not
see. That is the right trade for a licensing gate and is identical to what
permissions already do — but it does mean a backend outage looks like a
missing product rather than an error, so the composable exposes a distinct
`loadFailed` state the nav uses to say so.

### Admin page

New fork patch: `CustomFeaturesPage.vue` at `admin/custom-features`,
nav entry rendered only when `is_superadmin`. Registry grouped into sections,
one toggle per surface feature, `behavior` keys listed read-only with their
env-derived value and an "env-controlled" label. Writes go to
`POST /admin/custom-features` behind `require_platform_superadmin`.

## Data flow

```
Chatwoot session (access-token/client/uid)
  └─> TokenValidator.resolve_identity() ── GET /api/v1/profile ──> (user_id, type)
        └─> is_superadmin = user_id == 1 or type == "SuperAdmin"

GET /admin/custom-features
  └─> {k in registry : store[k] is true}  ──> ["knowledge", ...]
        └─> useCustomFeatures (cached, fail-closed)
              └─> nav + route gates:  hasFeature(f) && hasPermission(p)

POST /admin/custom-features {key, enabled}
  └─> require_platform_superadmin
        └─> reject key ∉ registry (400), reject kind == "behavior" (409)
              └─> store.set(key, enabled)
```

## Error handling

- Token resolution failure → 401. Never a silent allow. Unchanged from the
  existing `require_permission` posture.
- Not a superadmin → 403 on writes; reads succeed and report
  `is_superadmin: false`.
- Key outside the registry → 400. Writing a `behavior` key in Phase 1 → 409,
  because it is a real key that is simply not yet writable — distinct from a
  key that does not exist.
- Store unreachable → reads fail closed (empty list, `loadFailed: true`);
  writes 503 rather than reporting a success that was not persisted.
- `TokenValidator`'s existing short-TTL cache covers both `user_id` and
  `type`, so superadmin status is not a per-request round trip. A promotion in
  the `/super_admin` console takes up to the TTL to become visible.

## Testing

Backend:

- An unwritten store yields an empty effective list — the "starts empty"
  invariant, asserted directly.
- `is_superadmin`: id 1 with `type = nil` → true (the floor); id 7 with
  `type = "SuperAdmin"` → true; id 7 with `type = nil` → false. The first and
  third together are the regression guard against a truthiness check.
- `require_platform_superadmin`: 401 without a session, 403 for a
  non-superadmin, pass for both superadmin routes, no shared-secret bypass.
- A Chatwoot SuperAdmin holds all RBAC permissions on a tenant where they
  have no role assigned.
- Write path rejects unregistered keys (400) and `behavior` keys (409) with
  distinct codes.
- Every `surface` key in the registry names a permission that exists in
  `PERMISSION_REGISTRY` — catches a typo pairing at test time rather than as
  a permanently invisible page.

Fork patch: no JS test harness exists for patches in this repo, so
verification is a Cloud Build plus a manual check of feature-off, feature-on,
and non-superadmin-sees-nothing.

## Rollout

Ordered so nothing goes dark. proton and aeon360 are live; `default` is not.

1. **Backend image first.** Store, registry, endpoints, identity change. The
   SPA still reads the ERB-stamped list, so this is invisible to users.
2. **Populate the stores** for proton and aeon360 through the new API — every
   surface they reach today, which for both is effectively all of them, since
   their admins hold every permission. Their stores then match what their
   users already see.
3. **Chatwoot image** (`amd64`, Cloud Build — never on the prod VM) with the
   admin page and the composable-based gates.

No production env edit is needed at any step; dropping the ceiling removed it.

`default` is deliberately left with an empty store, so it opens blank and
becomes the living example of the new baseline.

Between 2 and 3 the store and the ERB list agree, so there is no window in
which a live tenant's CRM empties out. Step 2 is the one that must not be
skipped, and the sequencing exists to make skipping it survivable: if step 3
shipped first, both live tenants would open blank.

## What this does not do

- **Phase 1 does not move the 47 backend env flags into the store.** They stay
  env-driven and are shown read-only in the switchboard. Phase 2 covers them,
  and it is a real piece of work: those flags are read from `Settings` at boot,
  so making them store-driven means runtime-mutable settings with their own
  caching and invalidation story.
- No cross-tenant identity. Each tenant has its own Chatwoot and its own user
  table, so this is a per-tenant superadmin. "Platform superadmin" means the
  same *person* holds it on every tenant, not one account spanning them.
- No superadmin grant UI — Chatwoot's `/super_admin` console already does it.
- No change to what any RBAC permission means, or to the Roles & Permissions
  page, beyond superadmins holding everything.
- No onboarding wizard. This is the switchboard a wizard would eventually
  drive, not the wizard.
