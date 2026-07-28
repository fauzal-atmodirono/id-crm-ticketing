# Agent Priorities in the Collaborators tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Phase-5 per-agent channel-priority editor from the standalone Knowledge → Agent Priorities page into a "Channel priorities" section of the native Settings → Inboxes → Collaborators tab.

**Architecture:** Regenerate fork patch 0024 so its net effect is: a reusable `AgentPrioritiesEditor.vue` (extracted from the current full-page `AgentPriorities.vue`, chrome removed) rendered inside `CollaboratorsPage.vue`; the Knowledge nav entry, host section, and standalone page are removed. UI-only — reuses the already-deployed `/routing/{agents,priorities}` endpoints; no backend/agent change.

**Tech Stack:** Vue 3 SPA fork patch (Chatwoot v4.15.1), reconstruct-tree patch authoring, Cloud Build.

**Spec:** `docs/superpowers/specs/2026-07-28-agent-priorities-in-collaborators-design.md`

## Global Constraints

- Single home = Collaborators tab; the standalone Knowledge → Agent Priorities page (nav entry + host section + `AgentPriorities.vue`) is REMOVED. Editor becomes `components/proton/AgentPrioritiesEditor.vue`.
- Channels: `whatsapp`, `call`, `email`, `social`, `web`. Save shape: `channel_priorities = [primary, ...also.filter(c => c !== primary)]`; `!primary` → `useAlert` + skip. Priorities are per-agent, account-wide (hint says so).
- Native round-robin toggle + native "Upgrade to Business" upsell left untouched (our section sits after the Conversation Assignment accordion).
- `protonKnowledge.js` routing helpers (`getRoutingAgents`/`getRoutingPriorities`/`setRoutingPriority`) unchanged — now consumed by the editor.
- No `{{ }}` in static template TEXT (only dynamic `{{ expr }}` bindings) — literal tokens break the vite SFC compile (prior gotcha; use `<code v-pre>` if ever needed).
- Reconstruct-tree authoring: use the LOCAL `chatwoot/chatwoot:v4.15.1` image + `/opt/homebrew/bin/git` for `git diff` (the rtk wrapper mangles it). No backend/agent change.
- Commit by explicit path only (never `git add -A`; unrelated escalation WIP is uncommitted in the working tree).

---

### Task 1: Regenerate patch 0024 — editor in Collaborators, remove Knowledge page

**Files:**
- Modify: `deploy/chatwoot-fork/patches/0024-agent-priorities.patch` (regenerate)

**Interfaces:** consumes `/routing/agents`, `/routing/priorities` (GET), `/routing/priorities` (POST `{agent_id, channel_priorities}`) — already served. New end-state files inside the patch: `components/proton/AgentPrioritiesEditor.vue` (new), edits to `api/protonKnowledge.js` (helpers, unchanged from current 0024) and `routes/dashboard/settings/inbox/settingsPage/CollaboratorsPage.vue`.

- [ ] **Step 1: Reconstruct the tree with the CURRENT 0024 applied**
```bash
rm -rf /tmp/cw_ap2 && mkdir -p /tmp/cw_ap2 && cd /tmp/cw_ap2
GIT=/opt/homebrew/bin/git
CID=$(docker create chatwoot/chatwoot:v4.15.1); docker cp "$CID:/app/app" ./app >/dev/null 2>&1; docker rm "$CID" >/dev/null
$GIT init -q && $GIT add -A && $GIT commit -q -m base
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/00[0-2][0-9]-*.patch; do
  case "$p" in *0024-*) continue;; esac   # apply 0001-0023 only, defer 0024
  $GIT apply --whitespace=fix "$p" || { echo "FAIL $p"; exit 1; }
done
$GIT add -A && $GIT commit -q -m "base 0001-0023"      # <-- this is the diff baseline
$GIT apply --whitespace=fix /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0024-agent-priorities.patch
```
(Now the working tree has the CURRENT 0024 applied on top of the committed 0001-0023 baseline.)

- [ ] **Step 2: Read the current full-page component to extract from**

Read `app/javascript/dashboard/components/proton/AgentPriorities.vue` (created by the applied 0024). It contains: `CHANNELS`, the mount `Promise.all` + join into rows, `toggleAlso`, `save` (with `!primary` guard + `channel_priorities` build), `savingRows`, load/empty/error states, and a template with a page toolbar (`flex flex-col h-full`, a header bar with a refresh button + agent count, a helper bar) then a table. You will move the DATA/METHODS + the TABLE into a chrome-less editor; discard the page toolbar/`h-full`.

- [ ] **Step 3: Create `components/proton/AgentPrioritiesEditor.vue`**

A self-contained editor (Options API, matching `AgentPriorities.vue`'s style) with NO page chrome:
- `data()`: `channels: CHANNELS`, `rows: []`, `loading: true`, `loadError: '', savingRows: new Set()` (mirror the current component).
- `mounted()`: call the same load logic (`Promise.all([getRoutingAgents(), getRoutingPriorities()])`, join by id → rows; set `loading=false`; on error set `loadError` + `useAlert`). Import `getRoutingAgents, getRoutingPriorities, setRoutingPriority` from `dashboard/api/protonKnowledge` and `useAlert` from `dashboard/composables`.
- `methods`: `toggleAlso(row, ch)` and `save(row)` VERBATIM from the current `AgentPriorities.vue` (`if(!row.primary){useAlert('Pick a primary channel before saving.');return}`, `channel_priorities=[row.primary, ...row.also.filter(c=>c!==row.primary)]`, `setRoutingPriority(row.id, channel_priorities)`, success/error `useAlert`, `savingRows` add/delete via reassignment).
- `<template>`: a one-line hint `<p class="mb-3 text-xs text-n-slate-11">Priorities apply account-wide per agent; used to auto-assign a priority agent on handoff.</p>`, then the loading/error/empty states and the SAME agent table (Primary `<select>` blank+5 channels; Also-handles checkboxes; per-row Save `woot-button`/native button with `:is-loading`/disabled from `savingRows`). Wrap in a plain `<div>` (NOT `h-full`/toolbar). Keep dynamic `{{ row.name }}` etc.; no literal `{{ }}` in static text.

- [ ] **Step 4: Embed the editor in `CollaboratorsPage.vue`**

In `app/javascript/dashboard/routes/dashboard/settings/inbox/settingsPage/CollaboratorsPage.vue`:
- Add import near the other component imports (~line 13): `import AgentPrioritiesEditor from 'dashboard/components/proton/AgentPrioritiesEditor.vue';`
- In the `<template>`, immediately AFTER the Conversation Assignment `</SettingsAccordion>` (the block that ends ~line 669), add a native-styled section:
```vue
    <SettingsFieldSection
      label="Channel priorities"
      description="Auto-assign a priority agent on handoff by channel (Proton). Applies account-wide per agent."
    >
      <AgentPrioritiesEditor />
    </SettingsFieldSection>
```
(`SettingsFieldSection` is already imported in this file. If it is a Composition-API `setup` component with no `components:` block, the `<script setup>`-style import registers it automatically; if the file uses an explicit `components:` map, add `AgentPrioritiesEditor` there. Verify against the file's actual style.)

- [ ] **Step 5: Remove the standalone Knowledge page**
- Delete the full-page component: `rm app/javascript/dashboard/components/proton/AgentPriorities.vue`.
- Revert the nav + host additions the current 0024 made, back to the 0001-0023 baseline (so the "Agent Priorities" nav child + host `'agent-priorities'` section disappear):
```bash
cd /tmp/cw_ap2
/opt/homebrew/bin/git checkout "base 0001-0023" -- \
  app/javascript/dashboard/components-next/sidebar/Sidebar.vue \
  app/javascript/dashboard/routes/dashboard/settings/inbox/views/ProtonKnowledgeHost.vue 2>/dev/null || true
```
(If the current 0024 touched different files for the nav/host, `git checkout` the same files it modified back to the baseline — confirm via `git show` of the current 0024's file list. `protonKnowledge.js` must NOT be reverted — its routing helpers stay.)

- [ ] **Step 6: Generate the new patch + verify**
```bash
cd /tmp/cw_ap2
/opt/homebrew/bin/git add -N app/javascript/dashboard/components/proton/AgentPrioritiesEditor.vue
/opt/homebrew/bin/git diff "base 0001-0023" > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0024-agent-priorities.patch
echo "=== new 0024 touches: ==="; grep "^diff --git" /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0024-agent-priorities.patch
```
Expected files in the diff: `protonKnowledge.js`, `AgentPrioritiesEditor.vue` (new), `CollaboratorsPage.vue`. NOT Sidebar.vue / ProtonKnowledgeHost.vue / AgentPriorities.vue.

Verify the full stack applies clean on a FRESH tree AND vite compiles:
```bash
rm -rf /tmp/cw_ap2_v && mkdir -p /tmp/cw_ap2_v && cd /tmp/cw_ap2_v
CID=$(docker create chatwoot/chatwoot:v4.15.1); docker cp "$CID:/app/app" ./app >/dev/null 2>&1; docker rm "$CID" >/dev/null
/opt/homebrew/bin/git init -q && /opt/homebrew/bin/git add -A && /opt/homebrew/bin/git commit -q -m base
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/*.patch; do /opt/homebrew/bin/git apply --check --whitespace=fix "$p" && /opt/homebrew/bin/git apply --whitespace=fix "$p" || { echo "FAIL $p"; exit 1; }; done
echo "STACK APPLIES CLEAN"
grep -rc "AgentPrioritiesEditor" app/javascript/dashboard/routes/dashboard/settings/inbox/settingsPage/CollaboratorsPage.vue
test -f app/javascript/dashboard/components/proton/AgentPriorities.vue && echo "STALE PAGE STILL PRESENT (bad)" || echo "standalone page removed (good)"
cd /Users/yudaadipratama/Archive/id-crm-ticketing && docker build --target builder -t proton-chatwoot-verify:local deploy/chatwoot-fork/ 2>&1 | tail -15
```
Expected: "STACK APPLIES CLEAN"; CollaboratorsPage references `AgentPrioritiesEditor` (≥1); standalone page removed; a successful `vite build` (0 errors). Clean up `/tmp/cw_ap2*` + the verify image.

- [ ] **Step 7: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0024-agent-priorities.patch
git commit -m "feat(chatwoot-fork): move Agent Priorities into the native Collaborators tab"
```

---

### Task 2: Deploy to proton VM + smoke

**Files:** none (deploy). No backend/agent change — Chatwoot image only.

- [ ] **Step 1: Cloud Build + recreate chatwoot**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
gcloud builds submit deploy/chatwoot-fork/ --config deploy/chatwoot-fork/cloudbuild.yaml \
  --substitutions _REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
cd /opt/platform/deploy
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env pull chatwoot-rails chatwoot-sidekiq
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -6
'
```

- [ ] **Step 2: Verify deploy**
```bash
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
echo "rails:$(docker inspect proton-chatwoot-rails --format {{.State.Health.Status}})"
docker exec proton-chatwoot-rails sh -c "grep -rl \"Channel priorities\" /app/public/vite/assets 2>/dev/null | grep -E \"\\.js\$\" | wc -l"
'
```
Expected: rails healthy; "Channel priorities" label present in the live bundle (≥1).

- [ ] **Step 3: Live smoke (human)**

Settings → Inboxes → (a WhatsApp inbox) → **Collaborators**: a **"Channel priorities"** section appears below Conversation Assignment with the agent table. Set an agent's **Primary = whatsapp** (agent must be **Online** for routing to pick them), Save → reload → persists. Confirm the Knowledge nav no longer shows "Agent Priorities". Then a WhatsApp handoff auto-assigns that online priority agent.

---

## Self-Review

**Spec coverage:**
- Reusable `AgentPrioritiesEditor.vue` (chrome-less) → Task 1 Steps 2-3. ✓
- Embed in `CollaboratorsPage.vue` below Conversation Assignment → Task 1 Step 4. ✓
- Remove Knowledge page (nav + host + standalone component) → Task 1 Step 5. ✓
- protonKnowledge helpers unchanged → Task 1 (not reverted). ✓
- Native toggle/upsell untouched; account-wide hint → Steps 3-4. ✓
- Patch applies clean + vite compiles → Step 6. ✓
- Deploy + smoke → Task 2. ✓
- Non-goals: no backend/agent change; not per-inbox; upsell/toggle left alone. ✓

**Placeholder scan:** none — concrete reconstruct procedure + exact insertion point + editor spec. The editor body is defined by extraction from the current `AgentPriorities.vue` (its exact save/toggle logic is the source of truth, read in Step 2) rather than re-transcribed, to avoid drift — this is intentional, not a placeholder.

**Type consistency:** `AgentPrioritiesEditor` component name used in the import (Step 4) matches the created file (Step 3). `channel_priorities = [primary, ...also.filter(c=>c!==primary)]` matches the store schema + `pick_agent` first-element logic. `getRoutingAgents/getRoutingPriorities/setRoutingPriority` match the protonKnowledge helpers from the prior 0024. The diff baseline (`base 0001-0023` commit) is used consistently in Steps 1 and 6.
