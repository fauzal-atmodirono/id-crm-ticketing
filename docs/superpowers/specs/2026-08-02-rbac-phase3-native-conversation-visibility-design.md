# RBAC Phase 3 — native conversation-visibility gating (design)

**Date:** 2026-08-02 · **Status:** Approved · **Companion:**
`2026-07-27-own-sla-audit-rbac-design.md` (the parent spec — this doc covers
its Feature 3, "Chatwoot Pundit enforcement," Implementation phase 3).
Phases 1 (RBAC core) and 2 (SLA Policy store/UI, Audit Log viewer, Roles &
Permissions admin page) are done and deployed live on the `proton` tenant.

## Problem

Chatwoot's own native access control is untouched by RBAC Phases 1-2 — it's
still governed entirely by Chatwoot's built-in `administrator`/`agent` roles,
regardless of what custom roles an operator defines in the Roles &
Permissions page we shipped in Phase 2. An agent with any Chatwoot `agent`
role can see and reply to every conversation in every inbox they're a member
of; there is no way to restrict an agent to, say, only their assigned
conversations or only unassigned ones.

## Goal

Let an operator, from the existing "Roles & Permissions" admin page, restrict
an agent's conversation visibility to one of: all conversations in their
inboxes/teams (today's behavior), unassigned + their-own only, or
participating-only (assigned to them or a participant). This is the scope the
user confirmed: **agent visibility**, not inbox-administration permissions
(who can create/edit/delete inboxes) and not a full unification of Chatwoot's
native permission vocabulary with our own `sla.manage`-style keys.

## Architectural amendment (vs. the parent spec)

The parent spec's Feature 3 proposed "a fork patch to Chatwoot's Pundit
policies that consults `/authz`" — i.e., patching `app/policies/*.rb` to call
our backend on every conversation/inbox authorization check. Research done at
the start of this phase found a materially lower-risk path and **this spec
supersedes that approach**:

Chatwoot's Community codebase already ships (unconditionally — confirmed not
license-gated; `InjectEnterpriseEditionModule#prepend_mod_with` loads
`enterprise/` extensions regardless of any license check) a complete,
already-tested role-enforcement mechanism that does almost exactly what we
need:

- `account_users.custom_role_id` (FK, present on the Community schema) +
  Chatwoot's `CustomRole` model (`enterprise/app/models/custom_role.rb`, a
  simple `permissions: Array` column, values from a fixed 6-key vocabulary:
  `conversation_manage`, `conversation_unassigned_manage`,
  `conversation_participating_manage`, `contact_manage`, `report_manage`,
  `knowledge_base_manage`).
- `Enterprise::ConversationPolicy` (`enterprise/app/policies/enterprise/conversation_policy.rb`)
  is **already prepended** onto `ConversationPolicy` via `prepend_mod_with`
  and already reads `account_user.custom_role.permissions` to gate `show?`.
  This is currently dormant only because nothing ever creates `CustomRole`
  rows or sets `custom_role_id` — Phase 2 hid the native "Roles" settings UI
  that would otherwise do this.
- The write path is a plain existing REST surface, no license gate: standard
  CRUD at `/api/v1/accounts/:id/custom_roles`
  (`enterprise/app/controllers/api/v1/accounts/custom_roles_controller.rb`),
  and `PATCH /api/v1/accounts/:id/agents/:id` with a **top-level**
  `custom_role_id` param (handled by the prepended
  `Enterprise::Api::V1::Accounts::AgentsController#update`, confirmed by
  reading the source — `@agent.current_account_user.update!(custom_role_id: params[:custom_role_id])`).

So instead of a new Pundit patch calling our backend on every request
(network dependency on the critical path of every conversation page load;
the parent spec's own "heaviest, most upstream-coupled" warning), Phase 3
**mirrors** our role definitions into Chatwoot's own dormant native
mechanism via its existing REST API. Zero Ruby/Pundit code is added or
patched — fully consistent with the parent spec's explicit constraint to
never touch Chatwoot `enterprise/` server code (we call it as a client,
exactly as the SPA itself would).

## Architecture

### Data model

`role_permissions` gains a namespaced slice of permission keys representing
Chatwoot's native vocabulary, prefixed `chatwoot.` so they read as visually
distinct from our own admin-surface keys (`sla.manage`, `audit.view`, etc.)
in the permission registry and the Roles & Permissions page:

- `chatwoot.conversation_manage`
- `chatwoot.conversation_unassigned_manage`
- `chatwoot.conversation_participating_manage`
- `chatwoot.contact_manage`
- `chatwoot.report_manage`
- `chatwoot.knowledge_base_manage`

The three `conversation_*` keys are **mutually exclusive on a role** (a role
carries at most one — Chatwoot's own `CustomRole.permissions` array only
meaningfully uses one conversation visibility level at a time). Enforced at
grant time: the router's grant-permission handler, on seeing a new
`conversation_*` key, first revokes any other `conversation_*` key already on
that role (in the same transaction) before adding the new one — granting is
therefore "set," not "add," for this trio; the two boolean-style groups
(contact/report/kb manage) are unaffected and stack normally. The `roles`
table gains a nullable `chatwoot_custom_role_id` column — set only for roles
that carry at least one native key. Revoking the **last** `chatwoot.*` key
from a role deletes its mirrored `CustomRole` and clears the column back to
null (a role with zero native keys has no mirrored Chatwoot row, not an
empty one).

**Most-permissive-wins resolution.** Our RBAC allows a user to hold multiple
roles; Chatwoot's `account_users.custom_role_id` is a single FK. A new
`AuthzRepository.resolve_native_permissions(chatwoot_user_id)` computes, across
all of a user's roles, the most permissive conversation-visibility level
present (`conversation_manage` > `conversation_unassigned_manage` >
`conversation_participating_manage` > none) plus the union of the three
boolean-style keys (contact/report/kb manage — any role granting one is
enough). This is what gets written to Chatwoot.

### Sync mechanism

New `backend/apps/backend/src/chatbot/features/authz/chatwoot_role_mirror.py`,
using the backend's existing Chatwoot HTTP client (the same admin token
already used elsewhere in `ChatwootAdapter`):

- `ensure_custom_role(role) -> chatwoot_custom_role_id` — create or update the
  mirrored `CustomRole` (name, description, permissions array), store the
  returned id on `roles.chatwoot_custom_role_id`.
- `delete_custom_role(chatwoot_custom_role_id)` — Chatwoot's own
  `has_many :account_users, dependent: :nullify` clears `custom_role_id` on
  affected users automatically.
- `set_agent_custom_role(chatwoot_user_id, chatwoot_custom_role_id | None)` —
  `PATCH .../agents/:id` with the top-level `custom_role_id` param.

Trigger points, all inside the existing Phase 2 `/authz` router handlers,
synchronous within the same request:

1. Grant/revoke a `chatwoot.*` permission on a role → upsert or clear the
   mirrored `CustomRole.permissions`.
2. Assign/unassign a user to/from a role → recompute
   `resolve_native_permissions` for that user, call `set_agent_custom_role`.
3. Delete a role that has a `chatwoot_custom_role_id` → `delete_custom_role`.

### Consistency: fail-closed

This is an access-control surface, so a stale mirror (our system claims a
user has restricted visibility, but Chatwoot never actually updated
`custom_role_id`, or vice versa) is worse than a rejected admin action. If
the Chatwoot-side call fails, the whole `/authz` request returns an error and
the local DB change is rolled back in the same transaction — grant/assign
never partially applies. No background reconciliation job; the mirror is
only ever written synchronously, in the same request as the admin's action.

### Frontend

Extends the existing fork patch `0027-roles-permissions-admin.patch` (no new
page, no new route). `ProtonRolesPermissionsPage.vue`'s permission list gains
a "Chatwoot access" group at the top of each role's permission editor: a
radio group (None / Manage all conversations / Unassigned conversations only
/ My conversations only) plus three checkboxes (Contacts / Reports /
Knowledge Base), visually separated from the existing our-own-admin-page
permission checkboxes below it. `protonAdmin.js`'s existing grant/revoke
calls carry the new keys; no new API client file.

## Error handling / safety

- **Default-preserving.** A tenant with `RBAC_ENABLED=false`, or a tenant
  where no role ever grants a `chatwoot.*` key, never calls the mirror —
  byte-identical to today. Matches every prior phase's convention.
- **Fail-closed on the mirror**, matching Phase 1's "backend admin endpoints
  fail closed on authz" boundary — this is human admin access to a security
  control, not AI-orchestration fail-open territory.
- **No Ruby/Pundit patch, no new fork patch beyond extending 0027** — the
  parent spec's "never touch Chatwoot `enterprise/` server code" constraint
  holds; we're a client of its existing REST API, same as the SPA.

## Testing

- Backend: TDD on `chatwoot_role_mirror.py` with a fake/stubbed Chatwoot HTTP
  client (mirrors this repo's existing respx-stub convention). Router tests
  for: rollback-on-mirror-failure, most-permissive-wins resolution across 2-3
  conflicting roles, mutual-exclusivity enforcement on the three
  `conversation_*` keys, role-delete clearing the mirrored `CustomRole`.
- Fork patch: `git apply --check` cumulative with existing patches; local
  vite builder-stage build (compile gate) before a full Cloud Build.
- Manual smoke (needs live stack, deferred to rollout): create a role with
  "unassigned conversations only," assign a test agent, confirm in Chatwoot
  the agent's `custom_role_id` is set and they genuinely can't see
  conversations assigned to others; revoke and confirm access reverts to
  normal whole-inbox agent visibility.

## Rollout

Only affects agents an operator explicitly assigns to a role carrying a
`chatwoot.*` key going forward — existing agents keep today's behavior
(plain `administrator`/`agent`, no `custom_role_id`) until opted in via the
Roles & Permissions page. Per-tenant, same as every other RBAC surface
(`rbac_database_url` scoping already established in Phase 1).

## Out of scope (explicit)

- Inbox-administration permissions (create/edit/delete inbox, connect
  channels, routing config) — the user's stated goal for this phase is agent
  visibility, not admin-tier narrowing. A future phase could extend the same
  mirror mechanism to `InboxPolicy`'s administrator-only actions if needed,
  but Chatwoot has no native non-administrator inbox-management permission to
  mirror onto today.
- Unifying Chatwoot's native permission vocabulary with our own
  `sla.manage`-style keys into one system — deliberately kept as two
  namespaces (`chatwoot.*` vs our own) since they gate genuinely different
  surfaces (native Chatwoot UI vs. our own admin pages) and forcing one
  vocabulary would mean either limiting our own keys to Chatwoot's fixed 6,
  or patching Pundit after all (Approach B, rejected above).
