# Enterprise Cleanup — strip Chatwoot cloud/enterprise cruft (design)

**Date:** 2026-07-19 · **Status:** design for review · **Companion:**
`2026-07-18-crm-enhancement-program-spec.md`, `2026-07-18-chatwoot-fork-foundation-design.md`.

## Goal

Remove every **non-reusable** enterprise/cloud surface from the Community Chatwoot fork so the
product shows no billing, no "Upgrade to Enterprise" prompts, no `/enterprise/*` 404s, and no
Captain remnants (we replaced Captain with our own Copilot + Knowledge). **Keep and repurpose**
every enterprise-flagged *capability* we can use as our own (SLA, audit logs, custom roles,
reports, help center, agent bots, channels, automations, …).

Guiding rule (from the user): *remove all enterprise-flagged things, but if we can reuse it to
make it our own, keep it.*

## Remove vs Keep

**REMOVE (pure cloud/enterprise cruft — dead on self-hosted):**
- `/enterprise/*` billing calls: `limits`, `subscription`, `checkout`, `topup_checkout`,
  `toggle_deletion`, `account`.
- Upgrade UI: `dashboard/routes/dashboard/upgrade/UpgradePage.vue`,
  `helpcenter/components/UpgradePage.vue`, and any "Upgrade to Enterprise" banners.
- Billing settings: `dashboard/routes/dashboard/settings/billing/*` (nav + route).
- **Captain**: flags `captain_integration`, `captain_tasks`, `custom_tools`,
  `captain_document_auto_sync` — hide all Captain nav/settings (replaced by our Copilot/Knowledge).
- "Contact Chatwoot support" (`contact_chatwoot_support_team`) and the "Powered by Chatwoot"
  branding (enable `disable_branding` for white-label).

**KEEP + make our own (reusable — do NOT remove):** `sla`, `audit_logs`, `custom_roles`,
`agent_bots`, `reports`, `help_center`, `automations`, `macros`, `campaigns`, all `channel_*`,
`custom_attributes`, `labels`, `team_management`, `advanced_assignment`, `integrations`, etc.

## Architecture — two levers (not dozens of edits)

Chatwoot gates ~most of this UI behind `FEATURE_FLAGS` + `isFeatureEnabledonAccount`, so the
account's **enabled_features** set is the primary control; a small patch covers the rest.

### Lever 1 — feature-flag config (a `chatwoot-config` provisioning script)
A new idempotent script `chatwoot-config/provision_features.py` (mirrors `provision_labels.py`)
that sets the account's premium/enterprise feature toggles for one tenant:
- **Disable:** `captain_integration`, `captain_tasks`, `custom_tools`,
  `captain_document_auto_sync`, `contact_chatwoot_support_team`.
- **Enable (white-label + our reused capabilities):** `disable_branding`, `sla`, `audit_logs`,
  `custom_roles`, `agent_bots`, `reports`, `help_center`.
- Mechanism: Chatwoot's account features are toggled via the internal/super-admin path; the
  script uses the platform API where available, else documents the `rails runner`/super-admin
  fallback (a Chatwoot version detail the plan pins). Reversible: re-run with the inverse set.

Turning a flag off makes its nav/route/UI hide itself — this removes the bulk with no code.

### Lever 2 — frontend patch `0008-strip-enterprise-cruft`
For the hard-coded, non-flag-gated leftovers:
- **Kill the unconditional `/enterprise/api/v1/accounts/:id/limits` probe** (the `getLimits`
  call in `dashboard/store/modules/accounts.js` / `api/account.js`) so it never 404s.
- **Remove the Billing + Upgrade nav entries and their routes** (`billing.routes.js`, the
  upgrade route) so they can't be navigated to even directly.
- Guarded/additive like every other patch; reversible by dropping the patch.

## Scope

**In:** `provision_features.py` + tests; patch `0008`; wiring into the tenant provisioning docs.
**Out:** deleting any *reusable* capability (SLA/audit/roles/etc. stay); building the reused
features out (that is the roadmap phases); anything under `/enterprise/` server code (never
touched — the fork rule).

## Testing

- `provision_features.py`: unit tests (idempotency, correct enable/disable set) mirroring
  `test_provision_labels.py`.
- Patch `0008`: `git apply --check` cumulative with 0001–0007; image build (vite compile gate);
  local browser smoke — no `/enterprise/limits` 404, no Billing/Upgrade nav, no Captain menu;
  SLA/audit/reports still present.

## Multi-tenant / rollout

Additive per the fork model: the patch ships in the shared image; `provision_features.py` runs
per tenant (like the label/app scripts). Reversible (drop patch / re-run inverse).

## Open decisions

- **D1 — feature toggle mechanism:** platform API vs `rails runner`/super-admin. Resolve at
  plan time against the pinned Chatwoot v4.15.1 (confirm which endpoint toggles
  `enabled_features`). The script abstracts this behind one function.
- **D2 — keep `campaigns`/`linear`/`ip_lookup`?** Default: keep (they're capabilities, not
  cruft). Flip to remove only if you don't want them.
