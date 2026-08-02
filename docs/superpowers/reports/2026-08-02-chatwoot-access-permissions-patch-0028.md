# Report: patch 0028 "Chatwoot access" native-permission group + builder-stage compile verification

**Date:** 2026-08-02
**Branch:** dev-yuda
**Commit:** `5991893` feat(chatwoot-fork): add 'Chatwoot access' native-permission group to Roles & Permissions page

## What was done

Added `deploy/chatwoot-fork/patches/0028-chatwoot-access-permissions.patch`, which
extends `app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue` (introduced
by patch 0027) with a dedicated "Chatwoot access" section for the 6 native
`chatwoot.*` permission keys registered in RBAC Phase 3 (commit `28ac00c` and
follow-ons through `b9d1a28`):

- A radio group for the 3 mutually-exclusive conversation-visibility keys
  (`chatwoot.conversation_manage`, `chatwoot.conversation_unassigned_manage`,
  `chatwoot.conversation_participating_manage`) — selecting one calls
  `grantRolePermission` only; the backend's `grant_role_permission` handler
  enforces "set not add" server-side, revoking the other two.
- Checkboxes for the 3 boolean keys (`chatwoot.contact_manage`,
  `chatwoot.report_manage`, `chatwoot.knowledge_base_manage`) using the page's
  existing `togglePermission`.
- The generic permission list below the new section (`otherPermissions`
  computed) filters out these 6 keys so they aren't shown twice with
  conflicting interaction styles.

No new API calls were introduced — the patch reuses
`grantRolePermission`/`revokeRolePermission`/`togglePermission` from
`dashboard/api/protonAdmin` exactly as patch 0027 already wired them.

Per the Dockerfile in `deploy/chatwoot-fork/`, patches are globbed from
`patches/*.patch` and applied in filename order at image-build time, so no
Dockerfile change was needed for the new patch to take effect.

## Docker builder-stage compile check

Ran a local `docker buildx build --target builder` against
`deploy/chatwoot-fork/` (arm64, Docker Desktop) to confirm patch 0028 applies
cleanly on top of 0001–0027 and that the Vue/Vite asset build still compiles
with the new markup/logic.

- **Build ID:** `wg56xkxks0hf6o2yjxabciq9y`
- **Target:** `builder`
- **Base image:** `chatwoot/chatwoot:v4.15.1` (linux/arm64)
- **Result:** `Completed` — **15/15 build steps**, duration 2m 57s
- **Patch apply step** (`RUN for p in /tmp/proton-patches/*.patch; do git apply
  --whitespace=fix "$p" || exit 1; done`): all 28 patches applied in order
  (0001 through 0028) with no `git apply` failure — the loop's `|| exit 1`
  would have failed the build step on any rejected hunk, and it didn't.
- **Vite build step:** completed with `✓ built in 1m 28s`, produced
  `ProtonRolesPermissionsPage-LSj7RTOp.js` in the output bundle (confirms the
  modified component compiled, not just that the patch applied text-wise).
  Only warning emitted was the pre-existing "chunks larger than 500 kB" advice
  on `dashboard-*.js` / `DashboardIcon-*.js` — unrelated to this patch, present
  in prior builds too.
- Image export (`exporting to image`) completed successfully; no errors in
  the full build log.
- Full raw log saved to
  `/private/tmp/claude-501/-Users-yudaadipratama-Archive-id-crm-ticketing/867b79d8-de75-49bc-abbe-b832e476bdd0/scratchpad/build_0028.log`
  (scratchpad — not committed, session-local).

This confirms the **builder stage** (patch-apply + JS asset compile) is sound.
It does **not** cover the full multi-stage image (Rails asset precompile,
final runtime stage) or an `amd64` build — per `CLAUDE.md`, the
production/tenant image must still be built for `amd64` via Cloud Build
(`gcloud builds submit deploy/chatwoot-fork/ --config
deploy/chatwoot-fork/cloudbuild.yaml --substitutions _REGISTRY=<AR repo>`)
before it ships to the VM; a local arm64 `docker build`/push will not produce
a pullable image on the tenant VM.

## Commit

```
5991893 feat(chatwoot-fork): add 'Chatwoot access' native-permission group to Roles & Permissions page
  deploy/chatwoot-fork/patches/0028-chatwoot-access-permissions.patch | 133 +++++++++++++++++++++
  1 file changed, 133 insertions(+)
```

Working tree is clean as of this report (`git status` → "nothing to commit,
working tree clean"); no other files needed to be committed for this task.

(Note: unrelated modified files noted in an earlier session snapshot —
`backend/apps/backend/.env.example`,
`.../chatbot/features/chat/adapters/chatwoot.py`,
`.../chatbot/features/chat/escalation_notifier.py`,
`.../chatbot/features/chat/pic_registry.py`,
`.../chatbot/platform/config.py`, and their test files — show no diff against
HEAD as of this check and were not touched by this task; they belong to a
separate, already-resolved line of work.)

## Status contract

- **Status:** Done. Patch 0028 committed; local arm64 builder-stage
  `docker buildx build --target builder` completed successfully (15/15 steps,
  2m57s) with all 28 patches applying cleanly and the Vite asset build
  succeeding, including the new component code.
- **Commits:** `5991893` — feat(chatwoot-fork): add 'Chatwoot access'
  native-permission group to Roles & Permissions page
  (`deploy/chatwoot-fork/patches/0028-chatwoot-access-permissions.patch`).
- **Patch sequence verification result:** PASS — patches 0001 through 0028
  applied in filename order inside the real `chatwoot/chatwoot:v4.15.1`
  builder image with no `git apply` rejection, and the resulting JS bundle
  compiled cleanly (confirmed via build log, build ID
  `wg56xkxks0hf6o2yjxabciq9y`).
- **Concerns:**
  - This verified the **builder target only**, on **arm64**, locally. The
    full tenant image still needs an `amd64` Cloud Build
    (`deploy/chatwoot-fork/cloudbuild.yaml`) before deployment, per
    `CLAUDE.md`'s deploy notes — not done as part of this check.
  - Pre-existing bundle-size warning on `dashboard-*.js` /
    `DashboardIcon-*.js` (>500 kB post-minification) — not new, not blocking,
    not caused by this patch.
  - The Vue radio-group UX assumes the backend's mutual-exclusivity
    enforcement on `grant_role_permission` continues to hold (it does as of
    commit `13f050c`); if that invariant ever changes server-side, this
    client-side radio group would need revisiting.
- **Report file path:**
  `/Users/yudaadipratama/Archive/id-crm-ticketing/docs/superpowers/reports/2026-08-02-chatwoot-access-permissions-patch-0028.md`
