# Native SAML SSO — Security settings section (design)

**Date:** 2026-08-02 · **Status:** Proposed · **Companion:**
`2026-07-27-own-sla-audit-rbac-design.md` (reuses the RBAC `/authz` permission
system for nav/page gating), patch `0029-remove-dead-enterprise-nav.patch` and
`0032-hide-security-settings-nav.patch` (this design replaces what those
removed, with a native equivalent).

## Problem

Chatwoot's own `Settings > Security` page only ever shows an "Upgrade to
Enterprise" paywall. The gate (`INSTALLATION_PRICING_PLAN`) is a
license-server-controlled `InstallationConfig` value with no admin UI to edit
it on a self-hosted install — it can never be unlocked. The per-account SAML
implementation behind that paywall (`AccountSamlSettings`,
`SamlSettingsController`, etc.) lives in Chatwoot's `enterprise/` tree, under
Chatwoot's commercial license — patching around the gate to use it without
paying would be circumventing that license, not a UI hack. We already hid the
dead nav entry (patch `0032`) rather than try to unlock it.

Separately, Chatwoot's **Google OAuth login** (`GOOGLE_OAUTH_*`,
`ENABLE_GOOGLE_OAUTH_LOGIN`) is a real, working, non-gated core CE feature —
already configured and functioning. It is not SAML and does not cover
enterprise IdP federation (Okta, Azure AD, etc.), which some customers may
specifically require.

## Goal

Build our **own** SAML SSO capability, as original code, inside the Chatwoot
fork — license-clean, productizable for any tenant (not Proton-specific), no
confirmed customer yet. This is a roadmap build: **spec now, implement later**
when a real need lands. Wrap it in an extensible native "Security" settings
section (SAML is the first module; room for e.g. 2FA or session policies
later) so the next security feature doesn't require a page restructure.

Guiding principle (same as the SLA/Audit/RBAC precedent): *replace an
enterprise-licensed surface with a surface we own, mirroring its shape where
that shape is genuinely good, never its code.*

## Non-goals

- Touching anything under Chatwoot's `enterprise/` directory (fork rule, never
  violated).
- Building 2FA, IP allowlisting, or other security modules now — only the
  extensible shell + SAML.
- IdP-initiated SSO — SP-initiated only (simpler, standard, avoids a class of
  spoofing/CSRF-shaped attacks IdP-initiated flows are prone to).
- Touching Google OAuth login — it already works and is unrelated.
- Rebuilding anything already covered by the RBAC design's `/authz` system —
  this design reuses it for nav/page gating, not reinvents it.

## Architecture

### Where this lives: Chatwoot Rails, not the Proton `backend/` service

The SLA/Audit/RBAC precedent stores data and logic in the `backend/`
per-tenant Postgres, with Rails as a thin UI forwarding an *already-issued*
Chatwoot access token for the backend to validate. SAML can't follow that
shape for its core flow: **the whole point of SAML is establishing that
Chatwoot session in the first place** — there is no access token yet to
forward when a SAML assertion comes back from the IdP. So the login/callback
flow, and the settings it depends on, live natively in Chatwoot's own Rails
app and Postgres — new code, new table, own migration, own controllers — the
same place Chatwoot's own (gated) implementation lives, just not reusing it.

The **settings page's nav/visibility**, however, does reuse the existing RBAC
`/authz` permission system (`useProtonPermissions`, same pattern as
`sla.manage` / `roles.manage` in patches `0025`-`0028`) via a new
`security.manage` permission — consistent UX with the other native admin
pages. The real security boundary for the settings CRUD endpoints is still a
standard Chatwoot Pundit policy (administrator-only), not the RBAC layer,
since this is new Rails code, not a backend-fronted endpoint.

### Data model

New table, own migration (not `enterprise/`), one row per account:

```
saml_settings
  id
  account_id          FK, unique (one IdP config per account)
  idp_entity_id
  idp_sso_target_url
  idp_cert                    -- PEM, public cert, no encryption needed
  name_identifier_format
  role_attribute_name         -- nullable; IdP assertion attribute to read for role mapping
  role_mapping                -- jsonb: { "<idp attribute value>": "administrator" | "agent" }
  default_role                -- enum agent|administrator, default 'agent'
  enabled                     -- boolean, default false
  enforce_sso                 -- boolean, default false
  created_at / updated_at
```

Default/empty state (`enabled = false`) is byte-identical to today's
behavior — no SAML option appears anywhere until an admin fills this in.

### Login flow (SP-initiated)

- Add `omniauth-saml` (wraps `ruby-saml`; both MIT-licensed) to the Gemfile —
  fork patch. Pin to a version past `ruby-saml`'s known signature-wrapping
  CVEs (verify current advisory list at implementation time).
- `GET /accounts/:account_id/saml/init` — builds `OmniAuth::Strategies::SAML`
  options **at request time** from that account's `saml_settings` row
  (idp cert/url/entity id), redirects to the IdP. 404 if no row / not
  enabled.
- `POST /accounts/:account_id/saml/callback` — the fixed ACS URL an admin
  registers with their IdP (stable per account, so IdP-side config doesn't
  change). Processes the assertion via `ruby-saml`: signature, `NotBefore`/
  `NotOnOrAfter`, audience restriction all validated by the gem.
- On success: resolve user by NameID/email. If no matching `User` +
  `AccountUser`, JIT-provision one — role from `role_mapping` (looked up by
  `role_attribute_name`'s asserted value) falling back to `default_role`
  (`agent`) if unmapped or absent. Sign in via Devise/Warden and issue
  Chatwoot's normal auth tokens, mirroring the shape of the existing Google
  OAuth callback controller.
- On failure: redirect to login with a generic error — never leak assertion
  validation detail to the client.
- The account context comes from the fixed URL path segment, never a
  user-suppliable param — prevents a crafted RelayState from redirecting a
  successful login into a different tenant's account context.

### SP metadata endpoint

`GET /accounts/:account_id/saml/metadata` — generates SP metadata XML (SP
entity ID, ACS URL, NameID format) for an admin to hand to their IdP. No auth
required to fetch (it's not a secret, same as Chatwoot's own approach) —
account id in the path is not guessable-sensitive.

### Enforce-SSO toggle

When `enforce_sso = true` for an account: the base (non-`enterprise/`)
session/auth controller rejects password-based sign-in for that account's
`AccountUser`s, redirecting to `/accounts/:account_id/saml/init` instead.
Instance `super_admin`s are unaffected (separate auth path, `super_admin`
console). **Break-glass:** no in-app bypass UI is built (that would itself be
a security hole) — if an account admin locks themselves out with a broken IdP
config, ops flips `enforce_sso` back to `false` directly via Rails console.
Document this explicitly in the runbook at implementation time.

### Settings UI — native "Security" section

- Replaces the nav item hidden by patch `0032` with a real page at
  `Settings > Security`.
- Shell: a settings page with room for multiple modules (tabs or a left
  sub-nav); SAML is the only module today.
- SAML panel: form for IdP fields (entity ID, SSO URL, cert), role-mapping
  table, `enabled`/`enforce_sso` toggles, "Copy SP metadata URL" /
  "Download SP metadata" action.
- Nav entry + page gated on `security.manage` permission via the existing
  `useProtonPermissions` composable (same pattern `Sidebar.vue` already uses
  for `sla.manage`/`roles.manage`).

### API endpoints (Rails, own controller — not `enterprise/`)

- `GET/PUT /api/v1/accounts/:account_id/security/saml_settings` —
  administrator-only via a new Pundit policy (own class, not reusing
  `enterprise/`'s `AccountSamlSettingsPolicy`).
- No client secret exists in SAML (asymmetric signing) — no need for Rails 7
  `encrypts` on `idp_cert`; don't over-engineer this.

## Data flow

```
Login:
  Browser → GET /accounts/:id/saml/init → redirect to IdP
  IdP → POST /accounts/:id/saml/callback (SAML Response)
    → ruby-saml validates signature/timing/audience
    → resolve/JIT-provision User + AccountUser (role_mapping → default_role)
    → Devise sign_in → normal Chatwoot auth tokens issued

Settings:
  Fork SPA (Settings > Security, gated by security.manage via /authz)
    → GET/PUT /api/v1/accounts/:id/security/saml_settings
         → Pundit: current AccountUser.administrator? → allow/deny
```

## Security considerations

- SP-initiated only (see Non-goals).
- Signature, timing window, and audience validation delegated to `ruby-saml`
  — don't hand-roll assertion parsing.
- Pin `ruby-saml` to a version past known CVEs (signature-wrapping attacks
  have hit this gem before); note the exact version check as an
  implementation-time task, not baked into this doc.
- Account context fixed by URL path, never trusted from client-suppliable
  RelayState.
- JIT-provisioned users default to least privilege (`agent`) unless
  `role_mapping` explicitly says otherwise.
- `enforce_sso` break-glass is deliberately ops-only (Rails console), not a
  UI feature — avoids building a lockout-bypass that's itself an attack
  surface.

## Testing

- **Rails:** request specs for `init`/`callback`/`metadata` routes against a
  stubbed IdP response fixture; unit tests for role-mapping resolution
  (mapped value, unmapped value, missing attribute); Pundit policy specs
  (administrator-only); `enforce_sso` password-login rejection test.
- **Fork/frontend:** `git apply --check` the full patch chain including the
  new patches (mirrors existing convention); vite build gate; browser smoke —
  Security page loads, gated correctly by `security.manage`.
- **Manual, pre-ship:** test the full login flow against a real IdP (a free
  Okta or Auth0 developer tenant) before offering this to any actual
  customer — assertion fixtures don't substitute for a real IdP round trip.

## Implementation phases

1. **Core auth capability** — migration, `saml_settings` model, Gemfile
   addition, init/callback/metadata routes, JIT provisioning, Devise
   sign-in. Testable via `rails console`/curl before any UI exists.
2. **Settings UI** — native Security page shell + SAML panel (fork patch,
   `security.manage`-gated), CRUD API + Pundit policy. Un-hides the nav
   (supersedes patch `0032`).
3. **Enforce-SSO toggle** — password-login rejection + ops break-glass
   runbook entry.

Each phase gets its own implementation plan; phase 1 is the one to write
first since 2 and 3 depend on it.

## Multi-tenant / rollout

Per-account (`account_id` FK), ships in the shared custom Chatwoot image —
every tenant gets the capability once the image is rebuilt and deployed, but
a tenant only "has" SAML once an admin fills in the settings form
(`enabled = false` by default = today's behavior, minus the dead nav already
removed by `0032`).

## Open decisions

- **D1 — gem/version pin.** `omniauth-saml`/`ruby-saml`, MIT-licensed;
  confirm the latest CVE-patched version at implementation time (don't pin a
  version in this doc that will be stale by build time).
- **D2 — role-mapping shape.** Single attribute-value → role table for now
  (`role_attribute_name` + `role_mapping` jsonb). Multi-group/list-based
  mapping deferred unless a confirmed customer need requires it.
- **D3 — Security page module framework.** This doc only specs SAML as a
  module; the shell's extensibility mechanism (tabs vs. sub-nav, how a future
  module registers itself) is left to the phase-2 implementation plan rather
  than over-specified here with no second module yet to validate against.
