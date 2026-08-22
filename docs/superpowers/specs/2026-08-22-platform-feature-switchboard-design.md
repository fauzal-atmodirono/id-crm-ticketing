# Platform feature switchboard — a new tenant opens empty

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

That is wrong in two directions. Commercially, a customer sees surfaces they
did not buy. Operationally, an empty CRM full of empty pages is a worse first
impression than a small CRM that grows as things are switched on.

Changing which features a tenant has today means editing an env file on the
VM and redeploying. There is no in-app control at any level.

## What we are building

A per-tenant **feature switchboard**, owned by the platform superadmin, that
defaults to everything off.

Two gates, deliberately orthogonal:

- **Feature** — *is this capability part of this tenant's product at all?*
  Owned by the platform superadmin (us, the vendor). Per tenant.
- **Permission** — *which of the enabled capabilities may this person use?*
  Owned by the tenant's own administrator, through the existing Roles &
  Permissions page. Per role.

A surface renders only when both agree: `hasFeature(f) && hasPermission(p)`.
With every feature off, nothing renders regardless of how permissive the
tenant's roles are — which is exactly the "opens empty" requirement, and it
holds even against a tenant admin who grants themselves everything.

This orthogonality is the core of the design. Today the two questions are
conflated: page visibility is driven by permission alone, which means the
only way to withhold a capability from a customer is to withhold it from
every one of their users individually, and nothing stops their admin from
granting it back.

## Who the superadmin is

`is_superadmin(user_id, profile_type) = (user_id == 1) or (profile_type == "SuperAdmin")`

Verified live on 2026-08-22 across all three tenants:

| tenant  | id 1                          | type         | other SuperAdmins |
|---------|-------------------------------|--------------|-------------------|
| aeon360 | `yuda.adi.pratama@devoteam.com` | `SuperAdmin` | id 4              |
| proton  | `yudaa0110@gmail.com`         | `SuperAdmin` | ids 6, 8          |
| default | `yuda.adi.pratama@devoteam.com` | `SuperAdmin` | —                 |

Both halves earn their place:

- **`user_id == 1` is an unrevocable floor.** Id 1 is the person who set the
  platform up, on every tenant. Hardcoding it means no sequence of
  administrative accidents can lock the platform owner out of the switchboard
  — the classic failure where the last privileged user removes their own
  privilege. It is never stored anywhere and can never be revoked.
- **`type == "SuperAdmin"` is Chatwoot's own concept**, already in
  multi-person use here (three superadmins on proton, two on aeon360).
  `SuperAdmin < User`, STI on `users.type`, and `/api/v1/profile` already
  exposes it (`json.type resource.type` in `_user.json.jbuilder`) — the same
  endpoint `TokenValidator` already calls.

**Granting superadmin to another person needs no new code.** Chatwoot's
`/super_admin` console already does it, and is already how the existing
superadmins were made. We deliberately do *not* build a parallel grant store:
a second list of superadmin ids would be free to disagree with `users.type`,
producing a user who is revoked in our UI and still a Chatwoot superadmin, or
the reverse. One source of truth.

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

## Architecture

### Feature registry (static)

`features/platform/feature_registry.py` — a static dict, one entry per custom
surface: key, human label, description, group, and the permission it pairs
with. Static because a feature that can be enabled by typing its name into a
store is a feature that can be enabled by *mistyping* something else. The
registry is the closed set of things that exist.

Initial keys, grouped as the admin page will show them:

| group | keys |
|---|---|
| AI | `ai_assist`, `copilot`, `faq_suggestion_popup` |
| Knowledge | `knowledge` |
| Reports | `reports_departments`, `reports_case_lifecycle`, `reports_anomaly`, `reports_weekly` |
| Cases | `cases`, `taxonomy`, `rsa_incidents` |
| Operations | `workforce`, `agent_softphone`, `sla_policies`, `escalation_routing`, `inbound_alerts` |
| Data | `customer360`, `integrations` |
| Admin | `audit_log`, `roles_permissions` |

`nav_menu` is retired as a feature key: it gates the nav container rather
than a capability, and with per-surface keys an empty nav follows from every
surface being off.

### Feature store (per tenant)

`features/platform/feature_store.py` — Firestore-backed, the same shape as
the existing `PicStore`/`DealerStore` (which is the precedent for
operator-editable per-tenant config in this codebase). Maps feature key →
bool. **An absent key is off**, so a store that has never been written to
yields a completely blank CRM. No seeding, no migration, no first-boot marker.

### The ceiling

Effective set = `{k in registry : store[k] is true and k in PROTON_FEATURES}`.

`PROTON_FEATURES` keeps its name and its place but changes meaning: it is now
the **licensed ceiling** — what this tenant is *allowed* to switch on —
rather than what is currently on. The superadmin moves things within that
ceiling; a key outside it renders in the admin page as a disabled toggle
labelled "not licensed", so the boundary is visible rather than mysterious.

This gives a clean commercial story: the ceiling is set per contract when the
tenant is provisioned and only changes when the contract does; day-to-day
enablement is a UI action needing no deploy.

The compose default becomes the full registry (everything licensed) so a new
tenant's superadmin is not blocked from switching anything on, and the vendor
narrows it deliberately per contract. The tenant still opens empty, because
the *store* is empty — the ceiling permits, it does not enable.

A key in `PROTON_FEATURES` that is not in the registry is ignored rather than
rejected. This matters immediately: `nav_menu` is retired as a key but sits
in both live tenants' env files, and an unknown-key error there would fail
the tenants this change is meant to leave alone.

**The ceiling must be widened before it is enforced.** Today
`PROTON_FEATURES` gates only four names while every other surface is
permission-gated, so proton's and aeon360's Reports, Cases, Taxonomy,
Workforce and the rest are reachable *despite* not being in that list.
Reinterpreting the same four-item string as a ceiling would newly block all
of them. Both tenants' `PROTON_FEATURES` are therefore widened to the full
registry as part of rollout — their effective ceiling today is already
everything, so this records reality rather than granting anything new.

### Read path

`GET /admin/features/effective` — returns the effective key list plus
`is_superadmin` for the calling session. Authenticated as any signed-in
Chatwoot session; there is nothing sensitive in "what is switched on for the
CRM I am already looking at".

`useProtonFeatures.js` — a new composable mirroring `useProtonPermissions.js`
exactly: module-level cache, single in-flight promise, fail-closed to `[]` on
error.

Patch 0058 deliberately avoided making `hasFeature` async, on the grounds
that auditing every synchronous call site for a load-order race was a large
change to verify. That reasoning was sound then and is weaker now:
`useProtonPermissions.js` has since shipped and does this exact thing for
nav gating, so the pattern is proven in this fork rather than novel. The
synchronous `useProtonConfig().hasFeature` stays in place, still reading the
ERB-stamped list, so nothing that reads it today breaks; new gating uses the
composable.

**The cost, stated plainly:** fail-closed means a backend blip briefly renders
the CRM empty rather than briefly rendering surfaces the tenant should not
see. That is the right trade for a licensing gate, and it is identical to
what permissions already do — but it does mean a backend outage looks like a
missing product rather than an error, so the composable surfaces a distinct
`loadFailed` state the nav can use to say so.

### Admin page

New fork patch: `ProtonPlatformFeaturesPage.vue` at `proton/platform/features`,
nav entry visible only when `is_superadmin`. Registry grouped into sections,
one toggle per feature, ceiling-blocked entries disabled with an explanatory
label. Writes go to `POST /admin/features` behind
`require_platform_superadmin`.

## Data flow

```
Chatwoot session (access-token/client/uid)
  └─> TokenValidator.resolve_identity()  ── GET /api/v1/profile ──> (user_id, type)
        └─> is_superadmin = user_id == 1 or type == "SuperAdmin"

GET /admin/features/effective
  └─> registry ∩ store(enabled) ∩ PROTON_FEATURES  ──> ["knowledge", ...]
        └─> useProtonFeatures (cached, fail-closed)
              └─> nav + route gates:  hasFeature(f) && hasPermission(p)

POST /admin/features {key, enabled}
  └─> require_platform_superadmin
        └─> reject key ∉ registry (400) or key ∉ ceiling (403)
              └─> store.set(key, enabled)
```

## Error handling

- Token resolution failure → 401. Never a silent allow. Unchanged from the
  existing `require_permission` posture.
- Not a superadmin → 403 on writes; reads still succeed and simply report
  `is_superadmin: false`.
- Key outside the registry → 400. Key outside the ceiling → 403. These are
  different failures and get different codes: one is a bad request, the other
  is a licensing boundary.
- Store unreachable → reads fail closed (empty list, `loadFailed: true`);
  writes 503 rather than reporting a success that was not persisted.
- `TokenValidator`'s existing short-TTL cache covers both `user_id` and
  `type`, so superadmin status is not a per-request round trip. A promotion in
  the `/super_admin` console takes up to the TTL to be visible.

## Testing

Backend:

- Registry/store unit tests, including the invariant that an unwritten store
  yields an empty effective list.
- Ceiling intersection: enabled-but-unlicensed stays off; licensed-but-not-
  enabled stays off.
- `is_superadmin`: id 1 with `type = nil` → true (the floor); id 7 with
  `type = "SuperAdmin"` → true; id 7 with `type = nil` → false. The first and
  third cases together are the regression guard against a truthiness check.
- `require_platform_superadmin`: 401 without a session, 403 for a non-
  superadmin, pass for both superadmin routes, and no shared-secret bypass.
- SuperAdmin holds all RBAC permissions, on a tenant where they have no role.
- Write path rejects unregistered keys and ceiling violations with distinct
  codes.

Fork patch: no JS test harness exists for patches in this repo, so
verification is a Cloud Build plus manual check of the three states — feature
off, feature on, feature ceiling-blocked.

## Rollout

Ordered so nothing goes dark. proton and aeon360 both set
`PROTON_FEATURES=ai_assist,nav_menu,copilot,knowledge` today.

1. **Backend image first.** Store, registry, endpoints, identity change. The
   SPA still reads the ERB-stamped list, so this is invisible to users.
2. **Widen the ceiling** — set `PROTON_FEATURES` to the full registry in
   `proton.env` and `aeon360.env` (see "The ceiling must be widened before it
   is enforced" above). Hand the `sed` to Yuda; the classifier blocks prod
   env edits from this session.
3. **Enable in the store** every surface each live tenant reaches today —
   which for both is effectively all of them, since their admins hold every
   permission. Their stores then match what their users already see.
4. **Chatwoot image** (`amd64`, Cloud Build — never on the prod VM) with the
   admin page and the composable-based gates.

`default` is deliberately left with an empty store, so it opens blank — it is
the first tenant to demonstrate the new default.

Between 3 and 4 the store and the ERB list agree, so there is no window in
which a live tenant's CRM empties out. Steps 2 and 3 are the ones that must
not be skipped, and the sequencing exists entirely to make skipping them
survivable: if step 4 shipped first, both live tenants would open blank.

## What this does not do

- No cross-tenant identity. Each tenant has its own Chatwoot and its own user
  table, so this is a per-tenant superadmin. "Platform superadmin" means the
  same *person* holds it on every tenant, not that there is one account
  spanning them.
- No superadmin grant UI — Chatwoot's `/super_admin` console already does it.
- No change to what any RBAC permission means, or to the Roles & Permissions
  page, beyond superadmins holding everything.
- No onboarding wizard. This is the switchboard a wizard would eventually
  drive, not the wizard.
