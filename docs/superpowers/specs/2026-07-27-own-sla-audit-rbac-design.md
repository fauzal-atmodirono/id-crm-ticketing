# Own SLA + Audit + RBAC — replace Chatwoot enterprise surfaces (design)

**Date:** 2026-07-27 · **Re-verified:** 2026-08-01 (all technical claims re-checked
against current code — still accurate; no RBAC/roles work has landed since) ·
**Status:** Approved · **Companion:** `2026-07-19-enterprise-cleanup-design.md`,
`2026-07-18-crm-enhancement-program-spec.md`. Roadmap item #2 in
`docs/roadmap/2026-08-01-next-development-roadmap.md`, approved to implement
together with `2026-08-01-case-categories-subcategories-design.md` in one run.

**2026-08-01 update:** D2 resolved — SLA policy is **per-inbox from day one**
(see Feature 1 and D2 below), superseding this doc's original "global first"
recommendation.

## Problem

The forked CRM still surfaces three Chatwoot **enterprise-licensed** settings
pages — `/settings/security`, `/settings/sla`, `/settings/custom-roles` —
because `chatwoot-config/provision_features.py` deliberately `enable_features`
turns `sla`, `audit_logs`, and `custom_roles` **on**. Two problems:

1. **Redundancy.** We already run our **own** SLA engine
   (`backend/.../features/chat/sla.py`) and our **own** audit trail (backend
   Firestore `case_audit_log` + agent `ai_actions`). We are lighting up
   Chatwoot's enterprise UI on top of capabilities we already own.
2. **Licensing.** That SLA / audit / custom-roles code lives in Chatwoot's
   `enterprise/` tree, under Chatwoot's **commercial** license (not MIT).
   Shipping it in a resold, productizable multi-tenant CRM is a licensing risk,
   even though self-hosted never "subscribes."

## Goal

Stop surfacing Chatwoot's enterprise SLA/audit/roles UI, rely on
**license-clean capabilities we own**, and fill the one genuine gap (RBAC).
Keep the platform's **fail-open / default-preserving** convention: empty config
or no roles assigned ⇒ today's behavior, byte-identical.

Guiding principle: *stop lighting up Chatwoot's enterprise-licensed UI; replace
each enterprise surface with a surface we own.*

## Current state (verified in-repo, 2026-07-27)

| Capability | Chatwoot enterprise | Our own | Status |
|---|---|---|---|
| **SLA** | native (flag `sla`) | `backend/apps/backend/src/chatbot/features/chat/sla.py` — no-response + unresolved breach detection, per-channel ACK windows, PIC WhatsApp alert, APScheduler scans | **Engine exists**; policy is env-only today |
| **Audit** | native (flag `audit_logs`) | backend Firestore `case_audit_log` (`AuditLogPort`, `GET /cases/{id}/audit`) + agent `ai_actions` table (`agent/app/db/models.py`) | **Stores exist**; per-case read only |
| **Custom roles / RBAC** | enterprise (flag `custom_roles`) | none | **Genuine gap** (noted in `docs/proton-requirements-gap-analysis.md`) |

Neither `agent/` nor `backend/` currently reads Chatwoot roles or checks any
per-user permission; routing is by static team assignment + labels only.

## Scope

**In:**
- `provision_features.py`: move `sla`, `audit_logs`, `custom_roles` from
  `ENABLE` → `DISABLE` (reversible; removes the three enterprise pages).
- **SLA:** operator-editable policy store (backend per-tenant Postgres) read by
  the existing `sla.py`; new "SLA Policies" settings page (fork patch).
- **Audit:** backend list/filter endpoint for a global/admin view; new "Audit
  Log" viewer page (fork patch).
- **RBAC:** role model + `/authz` in the backend (per-tenant Postgres);
  enforcement at four surfaces; a "Roles & Permissions" admin page (fork patch).

**Out (explicit):**
- Rebuilding SLA or audit **storage** (Firestore stays for audit — deferred
  productization item, not this spec).
- Rebuilding the SLA breach **engine** (kept as-is).
- Any change under Chatwoot `enterprise/` **server** code (never touched — the
  fork rule).
- Per-user RBAC on fully-autonomous webhook AI actions (no human identity —
  stays policy-gated; see Feature 3, surface 4).

## Architecture

### The flag flip (shared root)

`chatwoot-config/provision_features.py` — move the three flags to `DISABLE`.
Idempotent, per-tenant, reversible (re-run inverse). This alone removes the
enterprise pages; the features below replace them.

### Feature 1 — SLA (wiring + UI; engine unchanged)

- **Keep** `features/chat/sla.py` and its scheduler as-is.
- **New SLA-policy store** in the backend's per-tenant Postgres (the same
  Postgres the KB feature provisions under `KNOWLEDGE_PG_ENABLED`), keyed by
  **`(tenant, inbox_id)`** with `inbox_id` nullable — a null-`inbox_id` row is
  the tenant-wide default; a specific-`inbox_id` row overrides it for that
  inbox. Columns cover the values `sla.py` reads today: response window,
  resolution window, per-channel ACK overrides, PIC WhatsApp number, engine
  on/off. `sla.py`'s lookup order is **inbox-specific row → tenant-default row
  → existing env values (`config.py` `sla_*`)** — so an unpopulated store (no
  rows at all) is still today's behavior, byte-identical.
- **New UI:** an "SLA Policies" page in the forked CRM, added as a new
  `patches/NNNN-*.patch` following the `KnowledgeSettings.vue` pattern (patches
  0013/0022). An inbox picker (plus a "Tenant default" option) selects which
  row is being edited; unset fields on an inbox-specific row inherit from the
  tenant default rather than requiring every field to be re-entered per inbox.
  Reads/writes the policy store via a backend admin endpoint (RBAC gated — see
  Feature 3).

### Feature 2 — Audit (viewer only; stores unchanged)

- **Keep** the backend Firestore `case_audit_log` and the agent `ai_actions`
  table.
- **New backend endpoint:** a list/filter query over `case_audit_log`
  (by case id, actor, date range) — today only `GET /cases/{id}/audit`
  (per-case) exists.
- **New UI:** an "Audit Log" viewer page in the fork (new patch), filterable,
  RBAC gated.
- Firestore remains the audit backend for now (storage rebuild is out of scope).

### Feature 3 — RBAC (the build)

**Home & model.** Role data lives in the backend's per-tenant Postgres. Tables:
- `roles` — id, name, description, tenant-scoped.
- `permissions` — a registry of permission-key strings.
- `role_permissions` — role ↔ permission.
- `user_roles` — Chatwoot user id ↔ role.

Idempotent seeding creates default roles mirroring Chatwoot's `administrator`
(all permissions) and `agent` (minimal), so an unconfigured tenant behaves
exactly as today. Permission keys are strings, e.g. `knowledge.edit`,
`kb.ingest`, `persona.edit`, `sla.manage`, `audit.view`, `inbox.access:<id>`,
`roles.manage`.

**Identity.** Agents already authenticate to **Chatwoot** — we add
*authorization*, not a new login. The forked SPA forwards the caller's Chatwoot
access token; the backend validates it against Chatwoot (`/api/v1/profile`,
short-TTL cached) to resolve the Chatwoot user id, then resolves roles →
permission set. Failure to validate ⇒ deny (with the default-preserving
fallback to Chatwoot admin/agent semantics when RBAC is unconfigured).

**`/authz` API (backend).** `check(user, permission) → allow/deny` and
`permissions(user) → set` for the SPA to gate nav. Plus role-admin CRUD
endpoints (`roles.manage`-gated) backing the admin UI.

**Enforcement, per surface:**
1. **Our custom admin UIs** — fork patch gates nav entries + pages on the
   user's permission set (hide/disable). Backed by `/authz/permissions`.
2. **Backend admin endpoints** — a FastAPI `require_permission("...")`
   dependency, replacing today's single shared API key (e.g. `faq_admin_api_key`).
3. **Chatwoot conversations/inboxes** — a fork patch to Chatwoot's Pundit
   policies that consults `/authz`. **Heaviest, most upstream-coupled — phased
   last.**
4. **Agent/backend AI actions** — human-triggered AI actions already route
   through the backend, so they are covered by surface (2). Fully-autonomous
   webhook-triggered actions have no per-user identity and remain
   **policy-gated, not RBAC-gated** (explicit boundary).

**Role-admin UI:** a "Roles & Permissions" page in the fork (new patch) to CRUD
roles and assign them to Chatwoot users; `roles.manage`-gated.

## Data flow (RBAC request)

```
Fork SPA (has Chatwoot session)
  └─ calls backend admin endpoint, forwarding Chatwoot access token
       └─ backend require_permission dependency
            ├─ validate token → Chatwoot /api/v1/profile (cached) → user id
            ├─ user id → user_roles → role_permissions → permission set
            └─ permission present? proceed : 403
Chatwoot Rails (conversations/inboxes)
  └─ Pundit policy patch → backend /authz/check → allow/deny
```

## Error handling / safety

- **Default-preserving:** unpopulated SLA store = env defaults; no custom roles
  = Chatwoot admin/agent semantics; empty audit filter = per-case behavior
  unchanged.
- **Backend admin endpoints fail closed** on authz (a permission check that
  can't resolve ⇒ deny) — these are the security teeth. This is distinct from
  the agent↔backend *fail-open* contract, which governs AI orchestration, not
  human admin access.
- **Token-validation cache** short-TTL to bound the Chatwoot round-trip; a
  validation error ⇒ deny, never silent allow.

## Testing

- **Backend unit tests:** authz allow/deny matrix; idempotent role seeding;
  SLA-policy store read/seed/override, including the inbox-specific →
  tenant-default → env-fallback resolution order; audit list/filter endpoint.
  Mirror the existing `pytest` + `respx` conventions (Chatwoot profile stubbed).
- **Fork patches:** `git apply --check` cumulative with existing patches; image
  build (vite compile gate); browser smoke — the three enterprise pages gone,
  the new SLA/Audit/Roles pages present and permission-gated.
- **Provisioning:** `provision_features.py` dry-run asserts the three flags are
  in `DISABLE`.

## Implementation phases (one spec, built one-by-one)

1. **RBAC core** — Postgres model + seeding + `/authz` + backend-endpoint
   `require_permission` enforcement. Foundation; gates the new UIs.
2. **Flag flip + SLA policy store/UI + Audit viewer UI** — disable the three
   flags; add the SLA policy store + "SLA Policies" page; add the audit
   list/filter endpoint + "Audit Log" page; add the "Roles & Permissions" admin
   page. All new pages RBAC-gated (depends on phase 1).
3. **Chatwoot Pundit enforcement** — fork patch gating Chatwoot
   conversations/inboxes via `/authz`. Heaviest, most upstream-coupled; last.

Each phase gets its own implementation plan.

## Multi-tenant / rollout

Per-tenant throughout: backend per-tenant Postgres holds roles + SLA policy;
`provision_features.py` runs per tenant (as the label/app scripts do). Additive
and reversible — drop the new patches / re-run the inverse flag set.

## Open decisions

- **D1 — Identity mechanism.** Recommended: forward Chatwoot access token,
  validate via `/api/v1/profile` (cached). Alternative (rejected): trust an
  SPA-supplied user-id header behind Caddy (spoofable).
- **D2 — SLA policy scope. RESOLVED 2026-08-01: per-inbox from day one.**
  Policy store keyed by `(tenant, inbox_id)` with `inbox_id` nullable as the
  tenant-wide default; `sla.py` resolves inbox-specific → tenant-default →
  env fallback. See Feature 1 above (supersedes this doc's original
  "global first, per-inbox later" recommendation).
- **D3 — Audit storage.** Firestore retained now; migrating audit to per-tenant
  Postgres for a dependency-free resale build is a deferred item.
