# Move Agent Priorities into the native Collaborators tab

**Date:** 2026-07-28
**Status:** Approved (design)
**Scope:** UI-only. Relocate the Phase-5 per-agent channel-priority editor from the separate Knowledge → Agent Priorities page into Chatwoot's native Settings → Inboxes → **Collaborators** tab (next to Conversation Assignment). Regenerate fork patch **0024**. No backend/agent change — reuses the already-deployed `/routing/{agents,priorities}` endpoints.

## Problem

Phase 5 shipped the priority editor as a standalone **Knowledge → Agent Priorities** page (patch 0024). But operators look for assignment config in **Settings → Inboxes → Collaborators → Conversation Assignment**, where Chatwoot shows its native round-robin toggle + an "Upgrade to Business" upsell for custom policies. Our custom priority routing should live there, and there should be a single home for it.

## Decision (from brainstorming)

- **Single home = the Collaborators tab.** Remove the Knowledge → Agent Priorities page (nav entry + host section + full-page component). Render a shared editor in Collaborators.
- Native round-robin toggle stays (it's the fallback). The native "Upgrade to Business" upsell is left in place (it's inside the upstream assignment accordion; cleanly removing it would fight the upstream template — our section beside it makes it moot).
- Channel priorities are **per-agent, account-wide** (not per-inbox); a one-line hint states this.

## Non-goals

- No backend/agent change (endpoints + routing engine already live).
- Not hiding/altering the native round-robin toggle or the native upsell.
- Not making priorities per-inbox (they stay account-wide per agent, matching the store + `pick_agent`).

## Design

Regenerate patch **0024** so its net effect becomes:

### 1. `AgentPrioritiesEditor.vue` (new, `components/proton/`)
Extract the reusable editor from the current `AgentPriorities.vue`: on mount `Promise.all([getRoutingAgents(), getRoutingPriorities()])` → join by agent id into rows `{id, name, availability_status, primary, also[]}` (`primary = channel_priorities[0]||''`, `also = channel_priorities.slice(1)`); a table with per-row **Primary** `<select>` (blank + `whatsapp/call/email/social/web`) + **Also-handles** checkboxes + per-row **Save** (`channel_priorities = [primary, ...also.filter(c=>c!==primary)]` → `setRoutingPriority`; `if(!primary)` → `useAlert` + skip); loading / empty / error states; `useAlert` feedback. **Drop the full-page chrome** (`h-full`, the top toolbar/refresh/count bar) so it embeds cleanly; keep a one-line hint: "Priorities apply account-wide per agent; used to auto-assign a priority agent on handoff." No `{{ }}` in static template text (dynamic `{{ expr }}` bindings only) so vite compiles.

### 2. `CollaboratorsPage.vue` (upstream) — embed the editor
Patch the native Collaborators settings page (Vue 3 `setup`): import `AgentPrioritiesEditor` and render it in a new `SettingsFieldSection` (native-styled) titled **"Channel priorities"** placed **directly after** the Conversation Assignment accordion block. The section header/description note it's account-wide and drives Phase-5 priority auto-assignment.

### 3. Remove the Knowledge → Agent Priorities page
Drop from patch 0024's footprint: the `Sidebar.vue` "Agent Priorities" nav child, the `ProtonKnowledgeHost.vue` `'agent-priorities'` SECTIONS entry + import + `v-else-if`, and the standalone `AgentPriorities.vue` full-page component (superseded by `AgentPrioritiesEditor.vue`).

### 4. `protonKnowledge.js` — unchanged
`getRoutingAgents` / `getRoutingPriorities` / `setRoutingPriority` stay (now consumed by the editor).

Net: regenerated `0024-agent-priorities.patch` touches `protonKnowledge.js` (helpers, as before), new `AgentPrioritiesEditor.vue`, and `CollaboratorsPage.vue` — and no longer touches Sidebar/ProtonKnowledgeHost or creates `AgentPriorities.vue`. Dockerfile LABEL already lists 0024 (no change).

## Testing

- Full patch stack `git apply --check` 0001-0024 clean on a fresh tree.
- Local builder-stage `vite build` compiles (0 errors).
- Manual smoke: Settings → Inboxes → (inbox) → Collaborators shows a "Channel priorities" section with the agent table; set an agent's Primary=whatsapp, Save, reload → persists (`GET /routing/priorities`); the Knowledge nav no longer shows "Agent Priorities". (Functional routing already verified in Phase 5.)

## Rollout

Cloud Build the chatwoot image + `pull` + `up -d --force-recreate chatwoot-rails chatwoot-sidekiq`. No backend/agent redeploy. Backward-safe: the routing endpoints + engine are unchanged; only the SPA placement moves.
