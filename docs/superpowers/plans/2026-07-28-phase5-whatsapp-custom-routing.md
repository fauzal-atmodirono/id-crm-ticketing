# Phase 5 custom auto-assignment (WhatsApp handoff) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent-service WhatsApp handoff auto-assign the highest-priority online agent via the existing backend Phase 5 routing engine, with a canonical channel taxonomy and a native admin UI for per-agent channel priorities.

**Architecture:** A new backend `POST /routing/assign` (given a conversation → resolve its canonical channel → `RoutingService.pick_agent` → assign via Chatwoot) reuses the existing `RoutingService`/`PresenceFetcher`/`ChannelPriorityStore`. The agent calls it on handoff (fail-open; native round-robin is the fallback). A canonical channel map unifies the taxonomy; a native fork page manages priorities.

**Tech Stack:** Python 3.12, FastAPI, pydantic, Firestore, httpx/respx, FastAPI TestClient; Vue 3 SPA fork patch (Chatwoot v4.15.1).

**Spec:** `docs/superpowers/specs/2026-07-28-phase5-whatsapp-custom-routing-design.md`

## Global Constraints

- Canonical channels: `whatsapp`, `call`, `email`, `social`, `web`. `Channel::TwilioSms` and any `whatsapp` → `whatsapp`; `voice` → `call`; `email` → `email`; `facebook`/`instagram` → `social`; else `web`.
- Phase 5 authoritative, native round-robin fallback: on handoff, assign the picked agent explicitly; `pick_agent` returns None (or `routing_enabled=false`, or any error) → no assign, native stands.
- `/routing/assign` and the `/routing/priorities` writes accept `x-api-key` == any of `routing_admin_api_key`, `faq_admin_api_key`, or `proton_backend_key` (constant-time). No accepted key → 401.
- No change to `RoutingService.pick_agent` (3-tier) or `ChannelPriorityStore` schema (`{agent_id:int, channel_priorities:[str]}`).
- Admin UI model: per agent Primary channel (single) + Also-handles (multi) → `channel_priorities = [primary, …others]`.
- Fail-open + backward-compatible: `routing_enabled=false` → `/routing/assign` no-op → agent handoff byte-identical to today.
- Backend tests: `cd backend/apps/backend && .venv/bin/pytest <path> -v`. Agent tests: `cd agent && .venv/bin/pytest <path> -v` (bare `pytest` not on PATH). asyncio_mode=auto. `ChannelPriorityStore` is Firestore-only → tests inject `AsyncMock` stores/presence/assigner.
- Commit only each task's files, by explicit path (never `git add -A`).

---

### Task 1: Canonical channel mapping

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/channels.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py` (`_resolve_conv_channel`)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_routing_channels.py`

**Interfaces:**
- Produces: `canonical_channel(channel_type: str | None) -> str`, `CANONICAL_CHANNELS: tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

Create `test_routing_channels.py`:
```python
from chatbot.features.routing.channels import canonical_channel, CANONICAL_CHANNELS


def test_canonical_channels_tuple():
    assert CANONICAL_CHANNELS == ("whatsapp", "call", "email", "social", "web")


def test_mapping():
    assert canonical_channel("Channel::TwilioSms") == "whatsapp"
    assert canonical_channel("Channel::Whatsapp") == "whatsapp"
    assert canonical_channel("Channel::Voice") == "call"
    assert canonical_channel("Channel::Email") == "email"
    assert canonical_channel("Channel::FacebookPage") == "social"
    assert canonical_channel("Channel::Instagram") == "social"
    assert canonical_channel("Channel::WebWidget") == "web"
    assert canonical_channel("Channel::Api") == "web"
    assert canonical_channel(None) == "web"
    assert canonical_channel("") == "web"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_routing_channels.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `channels.py`**
```python
"""Canonical channel taxonomy for Phase 5 routing.

Maps a Chatwoot inbox channel_type to one of five canonical keys used by
per-agent channel priorities. Channel::TwilioSms -> whatsapp mirrors the agent
service's WhatsApp detection (Twilio-WhatsApp is modelled as TwilioSms)."""

from __future__ import annotations

CANONICAL_CHANNELS: tuple[str, ...] = ("whatsapp", "call", "email", "social", "web")


def canonical_channel(channel_type: str | None) -> str:
    ct = (channel_type or "").lower()
    if "whatsapp" in ct or "twiliosms" in ct:
        return "whatsapp"
    if "voice" in ct:
        return "call"
    if "email" in ct:
        return "email"
    if "facebook" in ct or "instagram" in ct:
        return "social"
    return "web"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_routing_channels.py -v`
Expected: PASS.

- [ ] **Step 5: Use it in the backend adapter's `_resolve_conv_channel`**

In `chatwoot.py`, add the import near the top-level imports:
```python
from chatbot.features.routing.channels import canonical_channel
```
In `_resolve_conv_channel`, replace the inbox-name resolution so it returns the canonical key. The current body finds the inbox for `chatwoot_inbox_id` and returns `inbox.get("name") or inbox.get("channel_type")`. Change the matched-inbox branch to:
```python
            if inbox.get("id") == inbox_id:
                channel = canonical_channel(inbox.get("channel_type"))
                self._channel_cache = channel
                return channel
```
(Keep the `return "web"` fallback at the end.) If a pre-existing routing test (`test_routing_assignment.py`) asserted the old inbox-name channel string, update its expectation to the canonical key.

- [ ] **Step 6: Run the routing suites**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/ -k routing -q`
Expected: PASS (channels test + existing routing tests; fix any channel-string assertion as above).

- [ ] **Step 7: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/routing/channels.py backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py backend/apps/backend/src/chatbot/features/chat/test_routing_channels.py
git commit -m "feat(backend): canonical channel taxonomy for Phase 5 routing"
```

---

### Task 2: RoutingAssigner (resolve channel + assign)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/routing/assigner.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_routing_assigner.py`

**Interfaces:**
- Consumes: `canonical_channel` (Task 1).
- Produces: `RoutingAssigner(settings)` with `async resolve_channel(conversation_id: int) -> str` and `async assign(conversation_id: int, agent_id: int) -> None`.

- [ ] **Step 1: Write the failing test**

Create `test_routing_assigner.py`:
```python
from unittest.mock import AsyncMock

from chatbot.features.routing.assigner import RoutingAssigner
from chatbot.platform.config import Settings


def _settings():
    return Settings(chatwoot_api_url="http://cw", chatwoot_account_id=1, chatwoot_api_token="t")


async def test_resolve_channel_conv_to_inbox_to_canonical():
    a = RoutingAssigner(_settings())
    a._request = AsyncMock(side_effect=[
        {"id": 5, "inbox_id": 3},                       # GET /conversations/5
        {"id": 3, "channel_type": "Channel::TwilioSms"},  # GET /inboxes/3
    ])
    assert await a.resolve_channel(5) == "whatsapp"


async def test_resolve_channel_failopen_web():
    a = RoutingAssigner(_settings())
    a._request = AsyncMock(return_value=None)  # conv fetch fails
    assert await a.resolve_channel(5) == "web"


async def test_assign_posts_assignment():
    a = RoutingAssigner(_settings())
    a._request = AsyncMock(return_value={})
    await a.assign(5, 9)
    a._request.assert_awaited_once_with(
        "POST", "/conversations/5/assignments", {"assignee_id": 9}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_routing_assigner.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `assigner.py`** (mirror `presence.py`'s `_base`/`_request`)
```python
"""Resolve a conversation's canonical channel and assign an agent — the Chatwoot
side of the Phase 5 /routing/assign endpoint. Mirrors PresenceFetcher's plumbing;
every request is fail-open (returns None) so a Chatwoot blip never raises."""

from __future__ import annotations

from typing import Any

import structlog

from chatbot.features.routing.channels import canonical_channel

_log = structlog.get_logger(__name__)


class RoutingAssigner:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        import httpx  # noqa: PLC0415

        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        url = f"{self._base()}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(method, url, json=payload, headers=headers, timeout=10.0)
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("routing_assigner_request_failed", method=method, path=path, error=str(e))
            return None

    async def resolve_channel(self, conversation_id: int) -> str:
        conv = await self._request("GET", f"/conversations/{conversation_id}")
        inbox_id = (conv or {}).get("inbox_id") if isinstance(conv, dict) else None
        if inbox_id is None:
            return "web"
        inbox = await self._request("GET", f"/inboxes/{inbox_id}")
        channel_type = inbox.get("channel_type") if isinstance(inbox, dict) else None
        return canonical_channel(channel_type)

    async def assign(self, conversation_id: int, agent_id: int) -> None:
        await self._request(
            "POST", f"/conversations/{conversation_id}/assignments", {"assignee_id": agent_id}
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_routing_assigner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/routing/assigner.py backend/apps/backend/src/chatbot/features/chat/test_routing_assigner.py
git commit -m "feat(backend): RoutingAssigner resolves conversation channel + assigns"
```

---

### Task 3: `/routing/assign` endpoint + auth alignment + wiring

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/routing/router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py` (routing wiring block, ~line 405-411)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_routing_router.py` (extend)

**Interfaces:**
- Consumes: `RoutingService` (existing), `RoutingAssigner` (Task 2).
- Produces: `build_routing_router(settings, store, presence, routing_svc, assigner)` (two new params, appended) with `POST /routing/assign`. Auth accepts `routing_admin_api_key` / `faq_admin_api_key` / `proton_backend_key`.

- [ ] **Step 1: Write the failing test**

Append to `test_routing_router.py` (mirror its existing app-building helper; if it builds the router directly, add the two new args). Use `AsyncMock` for `routing_svc`/`assigner`:
```python
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatbot.features.routing.router import build_routing_router
from chatbot.platform.config import Settings


def _app(routing_enabled, pick_result, key="k"):
    settings = Settings(routing_admin_api_key=key, routing_enabled=routing_enabled,
                        chatwoot_api_url="http://cw", chatwoot_account_id=1, chatwoot_api_token="t")
    store = AsyncMock(); presence = AsyncMock()
    routing_svc = AsyncMock(); routing_svc.pick_agent = AsyncMock(return_value=pick_result)
    assigner = AsyncMock(); assigner.resolve_channel = AsyncMock(return_value="whatsapp")
    assigner.assign = AsyncMock()
    app = FastAPI()
    app.include_router(build_routing_router(settings, store, presence, routing_svc, assigner))
    return TestClient(app), assigner, routing_svc


def test_assign_picks_and_assigns():
    client, assigner, svc = _app(True, 9)
    r = client.post("/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "k"})
    assert r.status_code == 200 and r.json()["assigned_agent_id"] == 9
    assigner.assign.assert_awaited_once_with(5, 9)


def test_assign_no_agent_no_assign():
    client, assigner, svc = _app(True, None)
    r = client.post("/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "k"})
    assert r.json()["assigned_agent_id"] is None
    assigner.assign.assert_not_awaited()


def test_assign_disabled_noop():
    client, assigner, svc = _app(False, 9)
    r = client.post("/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "k"})
    assert r.json() == {"assigned_agent_id": None, "disabled": True}
    svc.pick_agent.assert_not_awaited(); assigner.assign.assert_not_awaited()


def test_assign_auth_401_without_key():
    client, _, _ = _app(True, 9)
    assert client.post("/routing/assign", json={"conversation_id": 5}).status_code == 401


def test_assign_accepts_proton_backend_key():
    settings = Settings(proton_backend_key="pk", routing_enabled=True,
                        chatwoot_api_url="http://cw", chatwoot_account_id=1, chatwoot_api_token="t")
    store = AsyncMock(); presence = AsyncMock()
    svc = AsyncMock(); svc.pick_agent = AsyncMock(return_value=None)
    assigner = AsyncMock(); assigner.resolve_channel = AsyncMock(return_value="whatsapp")
    app = FastAPI(); app.include_router(build_routing_router(settings, store, presence, svc, assigner))
    client = TestClient(app)
    assert client.post("/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "pk"}).status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_routing_router.py -v`
Expected: FAIL (`build_routing_router` arity / no `/routing/assign`).

- [ ] **Step 3: Implement the router changes**

In `router.py`:
- Add a body model near the others:
```python
class _AssignIn(BaseModel):
    conversation_id: int
```
- Replace `_require_api_key`'s single-key check with a multi-key constant-time check:
```python
def _require_api_key(settings: Settings):
    def _check(x_api_key: str | None = Header(default=None)) -> None:
        if not x_api_key:
            raise HTTPException(status_code=401, detail="Missing or invalid API key")
        candidates = [
            settings.routing_admin_api_key,
            settings.faq_admin_api_key,
            settings.proton_backend_key,
        ]
        for key in candidates:
            if key and hmac.compare_digest(x_api_key, key):
                return
        raise HTTPException(status_code=401, detail="Missing or invalid API key")

    return _check
```
- Extend the factory signature and add the endpoint. Change `def build_routing_router(settings, store, presence):` to:
```python
def build_routing_router(settings, store, presence, routing_svc, assigner):
```
and add before `return router`:
```python
    @router.post("/routing/assign", dependencies=[Depends(auth)])
    async def assign_conversation(body: _AssignIn) -> dict:
        if not settings.routing_enabled:
            return {"assigned_agent_id": None, "disabled": True}
        channel = await assigner.resolve_channel(body.conversation_id)
        agent_id = await routing_svc.pick_agent(channel)
        if agent_id is not None:
            await assigner.assign(body.conversation_id, agent_id)
        return {"assigned_agent_id": agent_id, "channel": channel}
```
(`auth = _require_api_key(settings)` already exists in the factory.)

- [ ] **Step 4: Wire it in `main.py`**

At the routing block (~line 405-411), add the assigner import at the top with the other routing imports:
```python
from chatbot.features.routing.assigner import RoutingAssigner
```
and change the `build_routing_router(...)` call (currently `build_routing_router(settings, _routing_priority_store, _routing_presence)`) to:
```python
    _routing_assigner = RoutingAssigner(settings)
    app.include_router(
        build_routing_router(
            settings, _routing_priority_store, _routing_presence, _routing_svc, _routing_assigner
        )
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_routing_router.py src/chatbot/features/chat/test_routing_mount.py -v`
Expected: PASS (new assign tests + existing router/mount tests; if `test_routing_mount.py` calls `build_routing_router` with the old arity, update it to pass the two new args — `AsyncMock()` for each).

- [ ] **Step 6: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/routing/router.py backend/apps/backend/src/chatbot/main.py backend/apps/backend/src/chatbot/features/chat/test_routing_router.py backend/apps/backend/src/chatbot/features/chat/test_routing_mount.py
git commit -m "feat(backend): POST /routing/assign + accept kb/agent api keys"
```

---

### Task 4: Agent bridge — assign on handoff

**Files:**
- Modify: `agent/app/clients/proton.py` (`assign_agent`)
- Modify: `agent/app/services/orchestrator.py` (`_handoff_to_human_via_chatwoot`)
- Test: `agent/tests/test_proton_client.py` (extend), `agent/tests/test_orchestrator_handoff_assign.py` (new)

**Interfaces:**
- Consumes: `POST /routing/assign {conversation_id}` (Task 3); `get_proton_config_client()` (existing).
- Produces: `ProtonConfigClient.assign_agent(conversation_id: int) -> None` (fail-open).

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_proton_client.py`:
```python
@respx.mock
async def test_assign_agent_posts_conversation_id():
    route = respx.post(f"{PROTON_BASE}/routing/assign").mock(
        return_value=httpx.Response(200, json={"assigned_agent_id": 9})
    )
    client = _make_client()
    await client.assign_agent(70)
    assert route.called
    assert route.calls.last.request.content == b'{"conversation_id": 70}'


@respx.mock
async def test_assign_agent_failopen_on_error():
    respx.post(f"{PROTON_BASE}/routing/assign").mock(return_value=httpx.Response(500, json={}))
    client = _make_client()
    await client.assign_agent(70)  # must not raise
```

Create `agent/tests/test_orchestrator_handoff_assign.py`:
```python
from unittest.mock import AsyncMock
from app.services import orchestrator


async def test_handoff_calls_assign_after_reopen(monkeypatch):
    chatwoot = AsyncMock()
    proton = AsyncMock()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: proton)
    await orchestrator._handoff_to_human_via_chatwoot(70, chatwoot, "")
    chatwoot.toggle_status.assert_awaited_once_with(70, "open")
    proton.assign_agent.assert_awaited_once_with(70)


async def test_handoff_reopens_even_if_no_proton(monkeypatch):
    chatwoot = AsyncMock()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: None)
    await orchestrator._handoff_to_human_via_chatwoot(70, chatwoot, "")
    chatwoot.toggle_status.assert_awaited_once_with(70, "open")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_proton_client.py -k assign_agent tests/test_orchestrator_handoff_assign.py -v`
Expected: FAIL (`assign_agent` missing; handoff doesn't call it).

- [ ] **Step 3: Add `assign_agent` to the proton client**

In `agent/app/clients/proton.py`, add (after `copilot_answer`, mirroring its POST style):
```python
    async def assign_agent(self, conversation_id: int) -> None:
        """Ask the backend to assign the priority agent for this conversation
        (POST /routing/assign). Fail-open: any error is logged and swallowed so
        the handoff (already reopened) is never blocked."""
        try:
            response = await self._client.post(
                "/routing/assign", json={"conversation_id": conversation_id}
            )
            response.raise_for_status()
        except Exception:
            logger.debug("proton_config: assign_agent failed", exc_info=True)
```

- [ ] **Step 4: Call it in the handoff**

In `agent/app/services/orchestrator.py::_handoff_to_human_via_chatwoot`, after the final `await chatwoot.toggle_status(conversation_id, "open")`, append:
```python
    proton = get_proton_config_client()
    if proton is not None:
        await proton.assign_agent(conversation_id)
```
(`get_proton_config_client` is already imported at the top of orchestrator.py.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_proton_client.py tests/test_orchestrator_handoff_assign.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add agent/app/clients/proton.py agent/app/services/orchestrator.py agent/tests/test_proton_client.py agent/tests/test_orchestrator_handoff_assign.py
git commit -m "feat(agent): assign priority agent via /routing/assign on handoff"
```

---

### Task 5: Native admin UI — Agent Priorities page (fork patch 0024)

**Files:**
- Create: `deploy/chatwoot-fork/patches/0024-agent-priorities.patch`
- (Reference, built at reconstruct time) `app/javascript/dashboard/api/protonKnowledge.js` + a new `components/proton/AgentPriorities.vue` + the Proton/Knowledge nav+route registration.

**Interfaces:** consumes `GET /routing/agents`, `GET /routing/priorities`, `POST /routing/priorities` (Tasks 3 + existing). The SPA sends `x-api-key` = the runtime Proton key, now accepted by the routing auth (Task 3).

> The SPA isn't in this checkout — author via reconstruct-tree against the local `chatwoot/chatwoot:v4.15.1` image. Use `/opt/homebrew/bin/git` for `git diff`. Mirror how `0017-knowledge-inboxes.patch` adds a proton page + nav + route (that patch is the template for a first-party Knowledge sub-page).

- [ ] **Step 1: Reconstruct + study the nav/route pattern**
```bash
rm -rf /tmp/cw_ap && mkdir -p /tmp/cw_ap && cd /tmp/cw_ap
GIT=/opt/homebrew/bin/git
CID=$(docker create chatwoot/chatwoot:v4.15.1); docker cp "$CID:/app/app" ./app >/dev/null 2>&1; docker rm "$CID" >/dev/null
$GIT init -q && $GIT add -A && $GIT commit -q -m base
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/*.patch; do $GIT apply --whitespace=fix "$p" || { echo FAIL $p; exit 1; }; done
$GIT add -A && $GIT commit -q -m "patches 0001-0023"
```
Read `app/javascript/dashboard/components/proton/KnowledgeInboxes.vue` and grep the tree for where `KnowledgeInboxes` is registered in the Proton nav + routes (the files patch 0017 touched) — replicate that wiring for a new `AgentPriorities` entry.

- [ ] **Step 2: Add API helpers to `protonKnowledge.js`**
```javascript
// GET /routing/agents -> [{ id, name, availability_status }]
export function getRoutingAgents() { return kbRequest('/routing/agents'); }
// GET /routing/priorities -> [{ agent_id, channel_priorities: [] }]
export function getRoutingPriorities() { return kbRequest('/routing/priorities'); }
// POST /routing/priorities  body: { agent_id, channel_priorities: [] }
export function setRoutingPriority(agentId, channelPriorities) {
  return kbRequest('/routing/priorities', {
    method: 'POST',
    body: { agent_id: agentId, channel_priorities: channelPriorities },
  });
}
```

- [ ] **Step 3: Create `components/proton/AgentPriorities.vue`**

Options-API page. `CHANNELS = ['whatsapp','call','email','social','web']`. On mount, `Promise.all([getRoutingAgents(), getRoutingPriorities()])`, join by agent id into rows `{ id, name, availability_status, primary, also: [] }` where `primary = channel_priorities[0] || ''` and `also = channel_priorities.slice(1)`. Render a table: name · availability · a `<select>` for **Primary** (blank + 5 channels) · a set of checkboxes for **Also handles** · a Save button per row. Save builds `channel_priorities = [row.primary, ...row.also.filter(c => c !== row.primary)]` and calls `setRoutingPriority(row.id, channel_priorities)`; guard: if `!row.primary`, `useAlert('Pick a primary channel')` and skip. Use `useAlert` for success/error and the `n-*`/tailwind classes used by the other proton pages (match `KnowledgeInboxes.vue`).

- [ ] **Step 4: Register nav + route**

Add an "Agent Priorities" item to the Proton/Knowledge nav and a route pointing at `AgentPriorities.vue`, mirroring the `KnowledgeInboxes` registration found in Step 1 (same files, same shape, new label/route/permission meta).

- [ ] **Step 5: Generate + verify the patch**
```bash
cd /tmp/cw_ap
# make the edits above in the working tree (on top of the 0001-0023 commit), then:
/opt/homebrew/bin/git add -N app/javascript/dashboard/components/proton/AgentPriorities.vue
/opt/homebrew/bin/git diff > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0024-agent-priorities.patch
grep "^diff --git" /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0024-agent-priorities.patch
```
Update `deploy/chatwoot-fork/Dockerfile` LABEL `org.proton.chatwoot.patch=...` to append `,0024`.
**Verify vite compiles** (catches SFC errors before Cloud Build — remember `{{token}}` literals need `v-pre`): a local builder-stage build:
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
docker build --target builder -t proton-chatwoot-verify:local deploy/chatwoot-fork/ 2>&1 | tail -20
```
Must reach a successful `vite build`. Also verify the full stack applies clean on a fresh tree (`git apply --check` every `patches/*.patch` 0001-0024). Clean up `/tmp/cw_ap` + the verify image.

- [ ] **Step 6: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0024-agent-priorities.patch deploy/chatwoot-fork/Dockerfile
git commit -m "feat(chatwoot-fork): native Agent Priorities page for Phase 5 routing"
```

---

### Task 6: Deploy to proton VM + smoke

**Files:** none (deploy). Ends with a live smoke gate.

- [ ] **Step 1: Sync + rebuild backend + agent**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
tar --exclude='__pycache__' -czf /tmp/phase5.tgz \
  backend/apps/backend/src/chatbot/features/routing/channels.py \
  backend/apps/backend/src/chatbot/features/routing/assigner.py \
  backend/apps/backend/src/chatbot/features/routing/router.py \
  backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py \
  backend/apps/backend/src/chatbot/main.py \
  agent/app/clients/proton.py \
  agent/app/services/orchestrator.py
gcloud compute scp /tmp/phase5.tgz crm-ticketing:/tmp/phase5.tgz --zone asia-southeast2-a
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
set -e
TS=$(date +%s); tar -czf /tmp/src-backup-$TS.tgz -C /opt/platform backend/apps/backend/src/chatbot/features/routing backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py backend/apps/backend/src/chatbot/main.py agent/app/clients/proton.py agent/app/services/orchestrator.py 2>/dev/null || true
tar -xzf /tmp/phase5.tgz -C /opt/platform 2>&1 | grep -v xattr || true
# Activate routing (Phase 5 authoritative, native fallback). Idempotent.
sudo sed -i "/^ROUTING_ENABLED=/d" /opt/platform/deploy/tenants/proton.env
echo "ROUTING_ENABLED=true" | sudo tee -a /opt/platform/deploy/tenants/proton.env >/dev/null
cd /opt/platform/deploy
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend agent 2>&1 | tail -5
'
```

- [ ] **Step 2: Build + deploy the Chatwoot image (patch 0024)**
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

- [ ] **Step 3: Verify deploy**
```bash
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
echo "agent:$(docker inspect proton-agent --format {{.State.Health.Status}}) backend:$(docker inspect proton-backend --format {{.State.Health.Status}}) rails:$(docker inspect proton-chatwoot-rails --format {{.State.Health.Status}})"
docker exec proton-agent sh -c "grep -c assign_agent /app/app/services/orchestrator.py"
docker exec proton-backend sh -c "grep -c \"routing/assign\" /app/src/chatbot/features/routing/router.py"
sudo grep ROUTING_ENABLED /opt/platform/deploy/tenants/proton.env
docker exec proton-chatwoot-rails sh -c "grep -rl \"Agent Priorities\" /app/public/vite/assets 2>/dev/null | grep -E \"\\.js\$\" | wc -l"
'
```
Expected: all healthy; both greps ≥1; `ROUTING_ENABLED=true`; the nav label in the live bundle ≥1.

- [ ] **Step 4: Live smoke (human)**
1. Open the native **Agent Priorities** page; set an online agent's **Primary = whatsapp**, Save.
2. From WhatsApp, trigger a handoff ("talk to a human"). Confirm the conversation is **auto-assigned to that agent** (Chatwoot conversation assignee).
3. Set the agent offline (or clear priorities) → a new handoff falls back to native round-robin (still assigned, not stuck).

---

## Self-Review

**Spec coverage:**
- Canonical mapping + `_resolve_conv_channel` → Task 1. ✓
- `RoutingAssigner` (resolve_channel + assign, fail-open) → Task 2. ✓
- `/routing/assign` + auth alignment (routing/faq/proton keys) + main wiring → Task 3. ✓
- Agent `assign_agent` + handoff call after reopen (fail-open) → Task 4. ✓
- Native Agent Priorities UI (Primary + Also-handles → channel_priorities) → Task 5. ✓
- Activation (`ROUTING_ENABLED=true`) + smoke → Task 6. ✓
- Non-goals honored: no `pick_agent`/store schema change; native auto-assign untouched (fallback). ✓

**Placeholder scan:** none — code steps carry concrete content; Task 5's SPA specifics are reconstruct-gated (SPA not in checkout) with the exact helpers/model + the 0017 template + a vite-compile verification (v-pre reminder).

**Type consistency:** `canonical_channel(str|None)->str` (Task 1) used by `RoutingAssigner.resolve_channel` (Task 2) and consumed by the `/routing/assign` handler (Task 3). `build_routing_router(settings, store, presence, routing_svc, assigner)` new arity defined in Task 3 and wired in Task 3 Step 4 + fixed in `test_routing_mount.py`. `assign_agent(conversation_id:int)` (Task 4) posts `{conversation_id}` to the endpoint Task 3 serves. `channel_priorities = [primary, …also]` (Task 5) matches the store schema and `pick_agent`'s first-element Tier-1 logic.
