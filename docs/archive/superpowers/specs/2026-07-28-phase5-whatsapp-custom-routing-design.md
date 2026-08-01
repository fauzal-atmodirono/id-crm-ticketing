# Phase 5 custom auto-assignment for the WhatsApp handoff

**Date:** 2026-07-28
**Status:** Approved (design)
**Scope:** bridge the already-built backend Phase 5 routing engine to the agent-service WhatsApp handoff, add a canonical channel taxonomy, a native admin UI for per-agent channel priorities, and activate it on proton. Replaces Chatwoot's Business-plan-gated "custom assignment policies" with our own priority routing.

## Problem

The Phase 5 routing engine exists (`backend/.../features/routing/`: `RoutingService.pick_agent(channel)` 3-tier, `PresenceFetcher`, `ChannelPriorityStore`, `/routing/{agents,priorities}` CRUD) and is wired into the **backend's** `ChatwootAdapter._assign_conversation` (its own `open_handoff`/`create_ticket`), gated by `routing_enabled`.

But the **WhatsApp handoff runs in the *agent* service** — `orchestrator._handoff_to_human_via_chatwoot` only does `toggle_status(open)`, so assignment falls to Chatwoot's **native round-robin, not Phase 5**. Also, the backend's channel resolver returns the inbox *name* (e.g. "Twilio Proton"), which does not match agent priority keys like `whatsapp`; there is no operator UI for priorities.

## Decisions (from brainstorming)

- One spec covering bridge + canonical channels + activation + native UI.
- **Phase 5 authoritative, native round-robin as fallback:** on handoff Phase 5 explicitly assigns the priority agent; if it finds none (or errors), native round-robin stands. No inbox setting change.
- Canonical channels: **`whatsapp`, `call`, `email`, `social`, `web`**.
- Admin UI model: per agent a **Primary channel** (single) + **Also handles** (multi); stored as `channel_priorities = [primary, …also]`.

## Non-goals

- No change to `RoutingService.pick_agent` (3-tier logic) or the `ChannelPriorityStore` schema.
- No change to Chatwoot native auto-assign settings (kept as fallback).
- Not building presence/availability editing (agents set their own online/offline in Chatwoot).

## Design

### 1. Canonical channel mapping — `backend/.../features/routing/channels.py` (new)

```python
CANONICAL_CHANNELS = ("whatsapp", "call", "email", "social", "web")

def canonical_channel(channel_type: str | None) -> str:
    ct = (channel_type or "").lower()
    if "whatsapp" in ct or "twiliosms" in ct:  # Twilio-WhatsApp == Channel::TwilioSms
        return "whatsapp"
    if "voice" in ct:
        return "call"
    if "email" in ct:
        return "email"
    if "facebook" in ct or "instagram" in ct:
        return "social"
    return "web"
```

`Channel::TwilioSms` → `whatsapp` mirrors the agent's existing WhatsApp detection (`_WHATSAPP_CHANNELS`), so no `medium` lookup is needed. Also update the backend adapter's `_resolve_conv_channel` (`chatwoot.py`) to return `canonical_channel(inbox channel_type)` instead of the inbox name, so the backend's own escalation path uses the same taxonomy as the new agent path.

### 2. `RoutingAssigner` — `backend/.../features/routing/assigner.py` (new)

Mirrors `PresenceFetcher`'s Chatwoot plumbing (`_base`, `_request`, fail-open → `None`):
- `resolve_channel(conversation_id: int) -> str`: `GET /conversations/{id}` → `inbox_id` → `GET /inboxes/{id}` → `canonical_channel(channel_type)`. Any failure → `"web"`.
- `assign(conversation_id: int, agent_id: int) -> None`: `POST /conversations/{id}/assignments` `{"assignee_id": agent_id}`.

### 3. `POST /routing/assign` + auth alignment — `routing/router.py`, `main.py`

- Extend `build_routing_router(settings, store, presence, routing_svc, assigner)` with a new endpoint:
  ```python
  @router.post("/routing/assign")
  async def assign_conversation(body: _AssignIn, x_api_key=Header(None)):
      _authorize(x_api_key)
      if not settings.routing_enabled:
          return {"assigned_agent_id": None, "disabled": True}
      channel = await assigner.resolve_channel(body.conversation_id)
      agent_id = await routing_svc.pick_agent(channel)
      if agent_id is not None:
          await assigner.assign(body.conversation_id, agent_id)
      return {"assigned_agent_id": agent_id, "channel": channel}
  ```
  Body `_AssignIn { conversation_id: int }`.
- **Auth alignment:** the write endpoints (`POST/PUT/DELETE /routing/priorities`) and the new `/routing/assign` currently require `routing_admin_api_key` only. Broaden the auth to also accept `faq_admin_api_key` / `proton_backend_key` (the keys the native SPA and the agent already send via `x-api-key`), matching the other `/kb/*` admin routers, so the native UI and the agent can call them. `routing_admin_api_key` stays accepted for back-compat.
- `main.py`: construct `RoutingAssigner(settings)` and pass it + `_routing_svc` into `build_routing_router(...)` (both already exist at the routing wiring block).

### 4. Agent bridge — `agent/app/clients/proton.py`, `agent/app/services/orchestrator.py`

- `ProtonConfigClient.assign_agent(conversation_id: int) -> None`: `POST /routing/assign {conversation_id}`; fail-open (any error → debug-log, return). Not cached.
- In `orchestrator._handoff_to_human_via_chatwoot`, **after** `await chatwoot.toggle_status(conversation_id, "open")`:
  ```python
  proton = get_proton_config_client()
  if proton is not None:
      await proton.assign_agent(conversation_id)  # fail-open; None/disabled → native round-robin stands
  ```
  This runs on every Chatwoot-only handoff (`handoff_to_human` + `escalate_to_ticket`). Byte-identical when the backend has `routing_enabled=false` (endpoint returns a no-op) or is unreachable.

### 5. Native admin UI — fork patch `0024-agent-priorities.patch` (new)

- `protonKnowledge.js` helpers: `getRoutingAgents()` (`GET /routing/agents`), `getRoutingPriorities()` (`GET /routing/priorities`), `setRoutingPriority(agentId, channelPriorities)` (`POST /routing/priorities` `{agent_id, channel_priorities}`).
- New page `components/proton/AgentPriorities.vue`: table of agents (id, name, availability) joined with their stored priorities; per row a **Primary channel** `<select>` (one of the five) + **Also handles** multi-checkboxes; a per-row Save posts `channel_priorities = [primary, …checked-others-excluding-primary]`. Empty primary → allow clearing (delete priority) — MVP: require a primary to save.
- Nav + route under the Proton/Knowledge nav (mirror `KnowledgeInboxes` patch 0017 registration), label **"Agent Priorities"**.

### 6. Activation + smoke (deploy)

- proton env: `ROUTING_ENABLED=true` (+ keep `ROUTING_ADMIN_API_KEY` set for back-compat; not required for the SPA now).
- Configure priorities in the new UI (e.g. an agent Primary=`whatsapp`).
- Smoke: WhatsApp handoff → conversation auto-assigned to the highest-priority online agent for `whatsapp`; when no eligible agent, native round-robin assigns.

## Testing (TDD)

- **backend channels:** `canonical_channel` table (whatsapp/twiliosms/voice/email/facebook/instagram/unknown).
- **backend assigner:** `resolve_channel` (conv→inbox→canonical; fail-open web) + `assign` (POST shape), respx/httpx-mocked.
- **backend router:** `/routing/assign` — routing-off no-op (`disabled:true`, no assign call); pick returns agent → assign called; pick None → no assign; auth (401 without an accepted key; 200 with proton/faq key). Priorities write accepts the aligned keys.
- **backend adapter:** `_resolve_conv_channel` returns canonical key.
- **agent:** `assign_agent` fail-open (error → None, no raise); `_handoff_to_human_via_chatwoot` calls `assign_agent` after `toggle_status(open)` (and still reopens if assign fails).
- **UI:** manual smoke (patches not unit-tested).

## Rollout

- Backend + agent via the normal `docker compose … up -d --build backend agent` sync path; UI via Cloud Build + recreate chatwoot.
- Fully backward-compatible: `routing_enabled=false` → `/routing/assign` is a no-op and the agent handoff is byte-identical to today (native round-robin). Existing `RoutingService`/store/presence untouched.
