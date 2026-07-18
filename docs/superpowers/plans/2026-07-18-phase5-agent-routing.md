# Phase 5 — Agent Routing & Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static `chatwoot_agent_team_id` assignment with a status-aware routing service that reads live agent availability from Chatwoot, picks the best available agent honoring per-agent channel priority, and cascades to idle agents when all priority-matched agents are busy.

**Architecture:** Three layered additions to the existing `proton-conversational-ai` backend: (1) a `PresenceFetcher` that extends `ChatwootAdapter` with two new async methods (`fetch_agents` and `fetch_agent_availability`) using the existing `_request`/`_base` pattern; (2) a `RoutingService` with a `ChannelPriorityStore` (Firestore-backed, keyed `agent_id → [channel, ...]`) plus a `pick_agent` function implementing two-tier selection; (3) a `routing_router.py` FastAPI router exposing the priority-config API and wiring the routing call into the `create_ticket` / `open_handoff` assignment paths. A companion single-file HTML config nav-app (`chatwoot-routing-admin/index.html`) mirrors the FAQ admin pattern to let ops edit per-agent priorities without writing code.

**Tech Stack:** Python 3.12, FastAPI, httpx (already used by `ChatwootAdapter`), `google-cloud-firestore` (already used for handoff/FAQ store), pytest-asyncio, vanilla JS + CSS (config nav-app — no build step, mirrors `chatwoot-faq-admin`).

## Global Constraints

- **Never unlock** the Chatwoot `/enterprise` folder — all routing logic lives in the backend.
- **Reuse** `ChatwootAdapter._request` / `_base` exactly (same httpx + dual-auth-header pattern) — no new HTTP client.
- **Reuse** existing Firestore `Settings` fields (`firestore_project_id`, `firestore_database_id`) — no new GCP project.
- **Additive** — do not break the existing `chatwoot_agent_team_id` fallback path; it activates when routing returns no agent.
- **Cloud Run** deployment for the backend; the routing router mounts on the existing FastAPI `main.py` app.
- **Config nav-app** is OUR HTML only — no Chatwoot source code reproduced.
- **Tests use mocked `_request`** with the same `_FakeClient` monkey-patch pattern used throughout the codebase (see `test_chatwoot_ticketing.py`).
- **Python minimum:** 3.12. **No new PyPI dependencies** beyond what is already in `pyproject.toml`.
- **`x-api-key` auth** on admin write endpoints (same as FAQ admin — `routing_admin_api_key` setting).
- **Inbox channel name** is the Chatwoot inbox label string; priority lists are ordered `[first-choice, second-choice, ...]`.

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `apps/backend/src/chatbot/features/routing/presence.py` | `PresenceFetcher` — two async methods on top of `ChatwootAdapter._request`/`_base`: `fetch_agents()` and `fetch_agent_availability(agent_id)`. |
| `apps/backend/src/chatbot/features/routing/store.py` | `ChannelPriorityStore` — Firestore CRUD for `agent_id → channel_priorities` documents in collection `routing_priorities`. |
| `apps/backend/src/chatbot/features/routing/service.py` | `RoutingService.pick_agent(conv_channel)` — two-tier selection logic (priority tier then idle-fallback tier). |
| `apps/backend/src/chatbot/features/routing/router.py` | FastAPI `routing_router` exposing `GET/POST/PUT/DELETE /routing/priorities` and `GET /routing/agents` (proxies Chatwoot agents list). |
| `apps/backend/src/chatbot/features/routing/__init__.py` | Package marker (empty). |
| `apps/backend/src/chatbot/features/chat/test_routing_presence.py` | pytest tests for `PresenceFetcher`. |
| `apps/backend/src/chatbot/features/chat/test_routing_store.py` | pytest tests for `ChannelPriorityStore` (mocked Firestore). |
| `apps/backend/src/chatbot/features/chat/test_routing_service.py` | pytest tests for `RoutingService.pick_agent` (mocked presence + store). |
| `apps/backend/src/chatbot/features/chat/test_routing_router.py` | pytest tests for the routing API router. |
| `apps/chatwoot-routing-admin/index.html` | Single-file config nav-app for editing per-agent channel priorities. |

### Modified files

| File | Change |
|---|---|
| `apps/backend/src/chatbot/platform/config.py` | Add `routing_admin_api_key: str = ""` and `routing_enabled: bool = False` settings. |
| `apps/backend/src/chatbot/features/chat/adapters/chatwoot.py` | Call `RoutingService.pick_agent` inside `create_ticket` and `open_handoff` when `routing_enabled` is True; fall back to existing `chatwoot_agent_team_id` team assignment otherwise. |
| `apps/backend/src/chatbot/main.py` | Mount `routing_router` on the FastAPI app. |

---

## Task 1: Add `routing_enabled` and `routing_admin_api_key` to Settings

**Files:**
- Modify: `apps/backend/src/chatbot/platform/config.py`

**Interfaces:**
- Produces: `Settings.routing_enabled: bool` and `Settings.routing_admin_api_key: str` consumed by Tasks 3, 4, and 5.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_routing_config.py`:

```python
from chatbot.platform.config import Settings


def test_routing_defaults_off() -> None:
    s = Settings()
    assert s.routing_enabled is False
    assert s.routing_admin_api_key == ""


def test_routing_can_be_enabled() -> None:
    s = Settings(routing_enabled=True, routing_admin_api_key="secret")
    assert s.routing_enabled is True
    assert s.routing_admin_api_key == "secret"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/chat/test_routing_config.py -v
```

Expected: `FAILED` — `Settings` has no `routing_enabled` attribute.

- [ ] **Step 3: Add settings fields**

In `apps/backend/src/chatbot/platform/config.py`, add after the `faq_admin_api_key` block (around line 102):

```python
    # --- Phase 5: Agent routing & presence ---
    # Master switch: when False (default) the routing service is bypassed and
    # the static chatwoot_agent_team_id team assignment remains active.
    routing_enabled: bool = False
    # API key that guards write access to the /routing/priorities endpoints.
    # An empty value 401s every write (no unauthenticated mutation).
    routing_admin_api_key: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest src/chatbot/features/chat/test_routing_config.py -v
```

Expected: `PASSED` (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/chatbot/platform/config.py \
        apps/backend/src/chatbot/features/chat/test_routing_config.py
git commit -m "feat(routing): add routing_enabled and routing_admin_api_key settings"
```

---

## Task 2: `PresenceFetcher` — fetch agents and availability from Chatwoot

**Files:**
- Create: `apps/backend/src/chatbot/features/routing/__init__.py`
- Create: `apps/backend/src/chatbot/features/routing/presence.py`
- Create: `apps/backend/src/chatbot/features/chat/test_routing_presence.py`

**Interfaces:**
- Consumes: `ChatwootAdapter._request(method, path, payload)` and `ChatwootAdapter._base()` (both already exist).
- Produces:
  - `AgentRecord` dataclass with fields `id: int`, `name: str`, `availability_status: str` (values: `"online"`, `"busy"`, `"offline"`).
  - `PresenceFetcher.fetch_agents(self) -> list[AgentRecord]` — calls `GET /agents` on the Chatwoot account API.
  - `PresenceFetcher.fetch_agent_availability(self, agent_id: int) -> str` — calls `GET /agents` and returns the status for a single agent (returns `"offline"` if not found).

- [ ] **Step 1: Create the package marker**

Create `apps/backend/src/chatbot/features/routing/__init__.py` with empty content (just a newline).

- [ ] **Step 2: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/test_routing_presence.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.platform.config import Settings


class _FakeAdapter:
    """Stub that replaces _request/_base on a PresenceFetcher."""

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.calls: list[tuple[str, str]] = []
        self._responses = responses

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path))
        for (m, sub), resp in self._responses.items():
            if m == method and sub in path:
                return resp
        return None

    def _base(self) -> str:
        return "http://cw/api/v1/accounts/1"


def _fetcher(fake: _FakeAdapter) -> PresenceFetcher:
    f = PresenceFetcher(Settings())
    f._request = fake._request  # type: ignore[method-assign]
    f._base = fake._base  # type: ignore[method-assign]
    return f


@pytest.mark.asyncio
async def test_fetch_agents_returns_list() -> None:
    fake = _FakeAdapter(
        {
            ("GET", "/agents"): [
                {"id": 1, "name": "Alice", "availability_status": "online"},
                {"id": 2, "name": "Bob", "availability_status": "busy"},
                {"id": 3, "name": "Carol", "availability_status": "offline"},
            ]
        }
    )
    fetcher = _fetcher(fake)
    agents = await fetcher.fetch_agents()
    assert len(agents) == 3
    assert agents[0] == AgentRecord(id=1, name="Alice", availability_status="online")
    assert agents[1] == AgentRecord(id=2, name="Bob", availability_status="busy")
    assert agents[2] == AgentRecord(id=3, name="Carol", availability_status="offline")


@pytest.mark.asyncio
async def test_fetch_agents_tolerates_none_response() -> None:
    fake = _FakeAdapter({})  # _request returns None for unknown paths
    fetcher = _fetcher(fake)
    agents = await fetcher.fetch_agents()
    assert agents == []


@pytest.mark.asyncio
async def test_fetch_agent_availability_found() -> None:
    fake = _FakeAdapter(
        {
            ("GET", "/agents"): [
                {"id": 7, "name": "Dana", "availability_status": "online"},
            ]
        }
    )
    fetcher = _fetcher(fake)
    status = await fetcher.fetch_agent_availability(7)
    assert status == "online"


@pytest.mark.asyncio
async def test_fetch_agent_availability_not_found_returns_offline() -> None:
    fake = _FakeAdapter(
        {("GET", "/agents"): [{"id": 1, "name": "Alice", "availability_status": "online"}]}
    )
    fetcher = _fetcher(fake)
    status = await fetcher.fetch_agent_availability(999)
    assert status == "offline"


@pytest.mark.asyncio
async def test_fetch_agents_calls_correct_path() -> None:
    fake = _FakeAdapter({("GET", "/agents"): []})
    fetcher = _fetcher(fake)
    await fetcher.fetch_agents()
    assert ("GET", "/api/v1/accounts/1/agents") in fake.calls
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest src/chatbot/features/chat/test_routing_presence.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'chatbot.features.routing'`.

- [ ] **Step 4: Implement `PresenceFetcher`**

Create `apps/backend/src/chatbot/features/routing/presence.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AgentRecord:
    id: int
    name: str
    availability_status: str  # "online" | "busy" | "offline"


class PresenceFetcher:
    """Reads agent availability from the Chatwoot account API.

    Inherits the _request / _base pattern from ChatwootAdapter so the same
    dual-auth-header httpx pattern is used without duplication. In production
    a ChatwootAdapter instance is passed and its _request/_base are used
    directly; in tests a _FakeAdapter stub is monkey-patched in.
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        # Deferred import avoids a circular dependency between the routing
        # package and the chat adapter package.
        import httpx

        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        url = f"{self._base()}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(
                    method, url, json=payload, headers=headers, timeout=10.0
                )
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("presence_request_failed", method=method, path=path, error=str(e))
            return None

    async def fetch_agents(self) -> list[AgentRecord]:
        """Return all agents on this Chatwoot account with their availability status.

        Calls GET /api/v1/accounts/{account_id}/agents. Returns an empty list
        when Chatwoot is unreachable or the response is not a list.
        """
        res = await self._request("GET", "/agents")
        if not isinstance(res, list):
            _log.warning("presence_fetch_agents_unexpected_response", response_type=type(res).__name__)
            return []
        agents: list[AgentRecord] = []
        for item in res:
            if not isinstance(item, dict):
                continue
            agent_id = item.get("id")
            name = item.get("name") or ""
            status = item.get("availability_status") or "offline"
            if agent_id is None:
                continue
            agents.append(AgentRecord(id=int(agent_id), name=name, availability_status=status))
        return agents

    async def fetch_agent_availability(self, agent_id: int) -> str:
        """Return the availability status string for a single agent.

        Returns ``"offline"`` when the agent is not found or Chatwoot is
        unreachable — a safe default that prevents routing to unknown agents.
        """
        agents = await self.fetch_agents()
        for agent in agents:
            if agent.id == agent_id:
                return agent.availability_status
        return "offline"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest src/chatbot/features/chat/test_routing_presence.py -v
```

Expected: `PASSED` (5 tests).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/features/routing/__init__.py \
        apps/backend/src/chatbot/features/routing/presence.py \
        apps/backend/src/chatbot/features/chat/test_routing_presence.py
git commit -m "feat(routing): add PresenceFetcher for Chatwoot agent availability"
```

---

## Task 3: `ChannelPriorityStore` — Firestore CRUD for per-agent channel priorities

**Files:**
- Create: `apps/backend/src/chatbot/features/routing/store.py`
- Create: `apps/backend/src/chatbot/features/chat/test_routing_store.py`

**Interfaces:**
- Consumes: `Settings.firestore_project_id`, `Settings.firestore_database_id` (both existing).
- Produces:
  - `AgentPriority` dataclass with fields `agent_id: int`, `channel_priorities: list[str]`.
  - `ChannelPriorityStore.get(agent_id: int) -> AgentPriority | None`
  - `ChannelPriorityStore.set(agent_id: int, channel_priorities: list[str]) -> None`
  - `ChannelPriorityStore.delete(agent_id: int) -> None`
  - `ChannelPriorityStore.list_all() -> list[AgentPriority]`

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/test_routing_store.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.platform.config import Settings


def _store() -> ChannelPriorityStore:
    return ChannelPriorityStore(
        Settings(
            firestore_project_id="proj",
            firestore_database_id="db",
        )
    )


def _make_snap(exists: bool, data: dict[str, Any] | None = None) -> MagicMock:
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    return snap


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found() -> None:
    snap = _make_snap(exists=False)
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _store().get(42)
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_priority_when_found() -> None:
    snap = _make_snap(
        exists=True, data={"agent_id": 7, "channel_priorities": ["WhatsApp", "email"]}
    )
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _store().get(7)
    assert result == AgentPriority(agent_id=7, channel_priorities=["WhatsApp", "email"])


@pytest.mark.asyncio
async def test_set_writes_document() -> None:
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _store().set(5, ["web", "email"])
    doc.set.assert_called_once_with({"agent_id": 5, "channel_priorities": ["web", "email"]})


@pytest.mark.asyncio
async def test_delete_removes_document() -> None:
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _store().delete(3)
    doc.delete.assert_called_once()


@pytest.mark.asyncio
async def test_list_all_returns_all_priorities() -> None:
    with patch(
        "chatbot.features.routing.store.firestore.Client", autospec=True
    ) as MockClient:
        snap_a = MagicMock()
        snap_a.to_dict.return_value = {"agent_id": 1, "channel_priorities": ["WhatsApp"]}
        snap_b = MagicMock()
        snap_b.to_dict.return_value = {"agent_id": 2, "channel_priorities": ["email", "web"]}
        col = MagicMock()
        col.stream.return_value = [snap_a, snap_b]
        MockClient.return_value.collection.return_value = col
        results = await _store().list_all()
    assert len(results) == 2
    assert AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]) in results
    assert AgentPriority(agent_id=2, channel_priorities=["email", "web"]) in results
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest src/chatbot/features/chat/test_routing_store.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'chatbot.features.routing.store'`.

- [ ] **Step 3: Implement `ChannelPriorityStore`**

Create `apps/backend/src/chatbot/features/routing/store.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "routing_priorities"


@dataclass(frozen=True)
class AgentPriority:
    agent_id: int
    channel_priorities: list[str]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AgentPriority):
            return NotImplemented
        return self.agent_id == other.agent_id and list(self.channel_priorities) == list(
            other.channel_priorities
        )

    def __hash__(self) -> int:
        return hash((self.agent_id, tuple(self.channel_priorities)))


class ChannelPriorityStore:
    """Firestore-backed store for per-agent channel priority lists.

    Each document lives in the ``routing_priorities`` collection keyed by the
    string agent_id. The document schema is:
        { "agent_id": int, "channel_priorities": [str, ...] }

    All I/O is wrapped in ``asyncio.to_thread`` (same pattern as the handoff
    and FAQ stores) so the async FastAPI event loop is not blocked.
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self, agent_id: int) -> firestore.DocumentReference:
        return self._client().collection(_COLLECTION).document(str(agent_id))

    async def get(self, agent_id: int) -> AgentPriority | None:
        """Return the priority record for ``agent_id``, or ``None`` if absent."""
        try:
            snap = await asyncio.to_thread(self._doc_ref(agent_id).get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return AgentPriority(
                agent_id=int(data.get("agent_id", agent_id)),
                channel_priorities=list(data.get("channel_priorities") or []),
            )
        except Exception as e:
            _log.error("routing_store_get_failed", agent_id=agent_id, error=str(e))
            return None

    async def set(self, agent_id: int, channel_priorities: list[str]) -> None:
        """Upsert the priority list for ``agent_id``."""
        try:
            await asyncio.to_thread(
                self._doc_ref(agent_id).set,
                {"agent_id": agent_id, "channel_priorities": channel_priorities},
            )
        except Exception as e:
            _log.error("routing_store_set_failed", agent_id=agent_id, error=str(e))

    async def delete(self, agent_id: int) -> None:
        """Remove the priority record for ``agent_id`` (no-op if absent)."""
        try:
            await asyncio.to_thread(self._doc_ref(agent_id).delete)
        except Exception as e:
            _log.error("routing_store_delete_failed", agent_id=agent_id, error=str(e))

    async def list_all(self) -> list[AgentPriority]:
        """Return all stored agent priority records."""
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_COLLECTION).stream())
            )
            results: list[AgentPriority] = []
            for snap in snaps:
                data = snap.to_dict() or {}
                agent_id = data.get("agent_id")
                if agent_id is None:
                    continue
                results.append(
                    AgentPriority(
                        agent_id=int(agent_id),
                        channel_priorities=list(data.get("channel_priorities") or []),
                    )
                )
            return results
        except Exception as e:
            _log.error("routing_store_list_failed", error=str(e))
            return []
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest src/chatbot/features/chat/test_routing_store.py -v
```

Expected: `PASSED` (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/chatbot/features/routing/store.py \
        apps/backend/src/chatbot/features/chat/test_routing_store.py
git commit -m "feat(routing): add ChannelPriorityStore (Firestore per-agent channel priorities)"
```

---

## Task 4: `RoutingService` — status-aware two-tier agent selection

**Files:**
- Create: `apps/backend/src/chatbot/features/routing/service.py`
- Create: `apps/backend/src/chatbot/features/chat/test_routing_service.py`

**Interfaces:**
- Consumes:
  - `PresenceFetcher.fetch_agents() -> list[AgentRecord]` (from Task 2)
  - `ChannelPriorityStore.list_all() -> list[AgentPriority]` (from Task 3)
  - `AgentRecord.id: int`, `AgentRecord.availability_status: str`
  - `AgentPriority.agent_id: int`, `AgentPriority.channel_priorities: list[str]`
- Produces:
  - `RoutingService(presence: PresenceFetcher, store: ChannelPriorityStore)`
  - `RoutingService.pick_agent(conv_channel: str) -> int | None`
    - Tier 1 (priority): agents whose `channel_priorities[0]` matches `conv_channel` AND `availability_status == "online"`. If any exist, return the first.
    - Tier 2 (fallback): agents whose `channel_priorities` contains `conv_channel` at any position AND `availability_status == "online"`. If any exist, return the first.
    - Tier 3 (idle fallback per spec item 7): agents who have no priority configured but are `"online"`. Return the first.
    - Returns `None` when all agents are busy/offline.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/test_routing_service.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.service import RoutingService
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.platform.config import Settings


def _make_fetcher(agents: list[AgentRecord]) -> PresenceFetcher:
    fetcher = PresenceFetcher(Settings())

    async def _fetch() -> list[AgentRecord]:
        return agents

    fetcher.fetch_agents = _fetch  # type: ignore[method-assign]
    return fetcher


def _make_store(priorities: list[AgentPriority]) -> ChannelPriorityStore:
    store = ChannelPriorityStore(Settings())

    async def _list_all() -> list[AgentPriority]:
        return priorities

    store.list_all = _list_all  # type: ignore[method-assign]
    return store


def _svc(
    agents: list[AgentRecord], priorities: list[AgentPriority]
) -> RoutingService:
    return RoutingService(
        presence=_make_fetcher(agents),
        store=_make_store(priorities),
    )


@pytest.mark.asyncio
async def test_picks_tier1_priority_match_online() -> None:
    """Agent whose first-priority channel matches and is online is picked."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob", availability_status="online"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 1  # Alice: WhatsApp is first-priority


@pytest.mark.asyncio
async def test_skips_busy_tier1_falls_to_tier2() -> None:
    """When tier-1 agents (first-priority match) are all busy, pick a tier-2 agent."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),   # tier-1 but busy
        AgentRecord(id=2, name="Bob", availability_status="online"),   # tier-2 (second priority)
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp", "email"]),
        AgentPriority(agent_id=2, channel_priorities=["email", "WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 2  # Bob: WhatsApp at position 1, but online


@pytest.mark.asyncio
async def test_fallback_to_unprioritised_idle_agent() -> None:
    """When all prioritised agents are busy, pick an online agent with no priority config (tier 3)."""
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=3, name="Carol", availability_status="online"),  # no priority record
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 3  # Carol: online, no priority config → idle fallback


@pytest.mark.asyncio
async def test_returns_none_when_all_busy_or_offline() -> None:
    agents = [
        AgentRecord(id=1, name="Alice", availability_status="busy"),
        AgentRecord(id=2, name="Bob", availability_status="offline"),
    ]
    priorities = [
        AgentPriority(agent_id=1, channel_priorities=["WhatsApp"]),
        AgentPriority(agent_id=2, channel_priorities=["WhatsApp"]),
    ]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result is None


@pytest.mark.asyncio
async def test_no_priorities_configured_picks_any_online() -> None:
    """When no priorities are stored at all, fall back to any online agent."""
    agents = [
        AgentRecord(id=5, name="Eve", availability_status="online"),
        AgentRecord(id=6, name="Frank", availability_status="offline"),
    ]
    result = await _svc(agents, []).pick_agent("email")
    assert result == 5


@pytest.mark.asyncio
async def test_channel_match_is_case_insensitive() -> None:
    agents = [AgentRecord(id=1, name="Alice", availability_status="online")]
    priorities = [AgentPriority(agent_id=1, channel_priorities=["whatsapp"])]
    result = await _svc(agents, priorities).pick_agent("WhatsApp")
    assert result == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest src/chatbot/features/chat/test_routing_service.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'chatbot.features.routing.service'`.

- [ ] **Step 3: Implement `RoutingService`**

Create `apps/backend/src/chatbot/features/routing/service.py`:

```python
from __future__ import annotations

import structlog

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore

_log = structlog.get_logger(__name__)


class RoutingService:
    """Status-aware agent selection with two-tier routing + idle fallback.

    Tier 1 — Priority-first match:
        Agents whose ``channel_priorities[0]`` (first preference) equals the
        inbound channel AND whose availability_status is ``"online"``.

    Tier 2 — Priority-secondary match:
        Agents whose ``channel_priorities`` list contains the channel at any
        position AND whose availability_status is ``"online"``.

    Tier 3 — Idle fallback (spec item 7):
        Online agents with NO priority record configured. These are agents the
        ops team has not yet assigned a channel preference — treated as a
        generic overflow pool.

    Returns ``None`` when all three tiers are exhausted (every agent is busy
    or offline). The caller should fall back to the static team assignment.
    """

    def __init__(self, presence: PresenceFetcher, store: ChannelPriorityStore) -> None:
        self._presence = presence
        self._store = store

    async def pick_agent(self, conv_channel: str) -> int | None:
        """Return the best available agent id for ``conv_channel``, or ``None``."""
        agents = await self._presence.fetch_agents()
        priorities = await self._store.list_all()

        channel_lower = conv_channel.lower()
        online = {a.id: a for a in agents if a.availability_status == "online"}
        priority_map: dict[int, list[str]] = {
            p.agent_id: [c.lower() for c in p.channel_priorities] for p in priorities
        }
        prioritised_agent_ids = set(priority_map.keys())

        # Tier 1: first-priority channel matches AND online
        for agent_id, chans in priority_map.items():
            if agent_id in online and chans and chans[0] == channel_lower:
                _log.info(
                    "routing_tier1_pick",
                    agent_id=agent_id,
                    channel=conv_channel,
                )
                return agent_id

        # Tier 2: channel appears anywhere in priority list AND online
        for agent_id, chans in priority_map.items():
            if agent_id in online and channel_lower in chans:
                _log.info(
                    "routing_tier2_pick",
                    agent_id=agent_id,
                    channel=conv_channel,
                )
                return agent_id

        # Tier 3: online agents with no priority configured (idle overflow pool)
        for agent_id in online:
            if agent_id not in prioritised_agent_ids:
                _log.info(
                    "routing_tier3_idle_fallback",
                    agent_id=agent_id,
                    channel=conv_channel,
                )
                return agent_id

        _log.warning("routing_no_available_agent", channel=conv_channel)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest src/chatbot/features/chat/test_routing_service.py -v
```

Expected: `PASSED` (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/chatbot/features/routing/service.py \
        apps/backend/src/chatbot/features/chat/test_routing_service.py
git commit -m "feat(routing): add RoutingService with three-tier agent selection"
```

---

## Task 5: Routing API router — priority CRUD + agents proxy

**Files:**
- Create: `apps/backend/src/chatbot/features/routing/router.py`
- Create: `apps/backend/src/chatbot/features/chat/test_routing_router.py`

**Interfaces:**
- Consumes:
  - `ChannelPriorityStore.get`, `set`, `delete`, `list_all` (Task 3)
  - `PresenceFetcher.fetch_agents() -> list[AgentRecord]` (Task 2)
  - `Settings.routing_admin_api_key` (Task 1)
- Produces: `routing_router: APIRouter` with routes:
  - `GET  /routing/agents` — return Chatwoot agents list (id + name + availability_status). No auth required (read-only, used by config UI).
  - `GET  /routing/priorities` — return all stored `AgentPriority` records. No auth required.
  - `POST /routing/priorities` — body `{"agent_id": int, "channel_priorities": [str]}`. Requires `x-api-key` header.
  - `PUT  /routing/priorities/{agent_id}` — body `{"channel_priorities": [str]}`. Requires `x-api-key`.
  - `DELETE /routing/priorities/{agent_id}` — Requires `x-api-key`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/test_routing_router.py`:

```python
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.router import build_routing_router
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.platform.config import Settings


def _app(api_key: str = "secret") -> tuple[TestClient, ChannelPriorityStore]:
    settings = Settings(routing_admin_api_key=api_key)
    store = ChannelPriorityStore(settings)
    store_data: dict[int, AgentPriority] = {}

    async def _list_all() -> list[AgentPriority]:
        return list(store_data.values())

    async def _get(agent_id: int) -> AgentPriority | None:
        return store_data.get(agent_id)

    async def _set(agent_id: int, channel_priorities: list[str]) -> None:
        store_data[agent_id] = AgentPriority(
            agent_id=agent_id, channel_priorities=channel_priorities
        )

    async def _delete(agent_id: int) -> None:
        store_data.pop(agent_id, None)

    store.list_all = _list_all  # type: ignore[method-assign]
    store.get = _get  # type: ignore[method-assign]
    store.set = _set  # type: ignore[method-assign]
    store.delete = _delete  # type: ignore[method-assign]

    fetcher = PresenceFetcher(settings)

    async def _fetch_agents() -> list[AgentRecord]:
        return [
            AgentRecord(id=1, name="Alice", availability_status="online"),
            AgentRecord(id=2, name="Bob", availability_status="busy"),
        ]

    fetcher.fetch_agents = _fetch_agents  # type: ignore[method-assign]

    app = FastAPI()
    app.include_router(build_routing_router(settings, store, fetcher))
    return TestClient(app), store


def test_get_agents_returns_list() -> None:
    client, _ = _app()
    resp = client.get("/routing/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["availability_status"] == "online"


def test_get_priorities_empty() -> None:
    client, _ = _app()
    resp = client.get("/routing/priorities")
    assert resp.status_code == 200
    assert resp.json() == []


def test_post_priority_requires_api_key() -> None:
    client, _ = _app()
    resp = client.post(
        "/routing/priorities",
        json={"agent_id": 1, "channel_priorities": ["WhatsApp"]},
    )
    assert resp.status_code == 401


def test_post_priority_creates_entry() -> None:
    client, _ = _app()
    resp = client.post(
        "/routing/priorities",
        json={"agent_id": 1, "channel_priorities": ["WhatsApp", "email"]},
        headers={"x-api-key": "secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == 1
    assert body["channel_priorities"] == ["WhatsApp", "email"]


def test_put_priority_updates_entry() -> None:
    client, _ = _app()
    client.post(
        "/routing/priorities",
        json={"agent_id": 2, "channel_priorities": ["email"]},
        headers={"x-api-key": "secret"},
    )
    resp = client.put(
        "/routing/priorities/2",
        json={"channel_priorities": ["web", "email"]},
        headers={"x-api-key": "secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["channel_priorities"] == ["web", "email"]


def test_delete_priority_removes_entry() -> None:
    client, _ = _app()
    client.post(
        "/routing/priorities",
        json={"agent_id": 3, "channel_priorities": ["WhatsApp"]},
        headers={"x-api-key": "secret"},
    )
    resp = client.delete("/routing/priorities/3", headers={"x-api-key": "secret"})
    assert resp.status_code == 200
    resp2 = client.get("/routing/priorities")
    ids = [p["agent_id"] for p in resp2.json()]
    assert 3 not in ids


def test_delete_requires_api_key() -> None:
    client, _ = _app()
    resp = client.delete("/routing/priorities/1")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest src/chatbot/features/chat/test_routing_router.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'chatbot.features.routing.router'`.

- [ ] **Step 3: Implement the routing router**

Create `apps/backend/src/chatbot/features/routing/router.py`:

```python
from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class _PriorityIn(BaseModel):
    agent_id: int
    channel_priorities: list[str]


class _PriorityUpdate(BaseModel):
    channel_priorities: list[str]


class _AgentOut(BaseModel):
    id: int
    name: str
    availability_status: str


class _PriorityOut(BaseModel):
    agent_id: int
    channel_priorities: list[str]


def _require_api_key(settings: "Settings"):
    """Return a FastAPI dependency that 401s when the x-api-key header is wrong."""

    def _check(x_api_key: str | None = Header(default=None)) -> None:
        expected = settings.routing_admin_api_key
        if not expected or not x_api_key:
            raise HTTPException(status_code=401, detail="Missing or invalid API key")
        if not hmac.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=401, detail="Missing or invalid API key")

    return _check


def build_routing_router(
    settings: "Settings",
    store: ChannelPriorityStore,
    presence: PresenceFetcher,
) -> APIRouter:
    """Build and return the routing config FastAPI router."""
    router = APIRouter(tags=["routing"])
    auth = _require_api_key(settings)

    @router.get("/routing/agents", response_model=list[_AgentOut])
    async def list_agents() -> list[AgentRecord]:
        return await presence.fetch_agents()

    @router.get("/routing/priorities", response_model=list[_PriorityOut])
    async def list_priorities() -> list[AgentPriority]:
        return await store.list_all()

    @router.post(
        "/routing/priorities",
        response_model=_PriorityOut,
        dependencies=[Depends(auth)],
    )
    async def create_priority(body: _PriorityIn) -> AgentPriority:
        await store.set(body.agent_id, body.channel_priorities)
        return AgentPriority(
            agent_id=body.agent_id, channel_priorities=body.channel_priorities
        )

    @router.put(
        "/routing/priorities/{agent_id}",
        response_model=_PriorityOut,
        dependencies=[Depends(auth)],
    )
    async def update_priority(agent_id: int, body: _PriorityUpdate) -> AgentPriority:
        await store.set(agent_id, body.channel_priorities)
        return AgentPriority(agent_id=agent_id, channel_priorities=body.channel_priorities)

    @router.delete("/routing/priorities/{agent_id}", dependencies=[Depends(auth)])
    async def delete_priority(agent_id: int) -> dict[str, str]:
        await store.delete(agent_id)
        return {"status": "deleted", "agent_id": str(agent_id)}

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest src/chatbot/features/chat/test_routing_router.py -v
```

Expected: `PASSED` (7 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/chatbot/features/routing/router.py \
        apps/backend/src/chatbot/features/chat/test_routing_router.py
git commit -m "feat(routing): add routing API router (priorities CRUD + agents proxy)"
```

---

## Task 6: Wire `RoutingService` into `ChatwootAdapter` assignment

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`
- Create: `apps/backend/src/chatbot/features/chat/test_routing_assignment.py`

**Interfaces:**
- Consumes:
  - `RoutingService.pick_agent(conv_channel: str) -> int | None` (Task 4)
  - `Settings.routing_enabled: bool` (Task 1)
  - `ChatwootAdapter._request("POST", f"/conversations/{conv_id}/assignments", {"assignee_id": agent_id})` — the Chatwoot individual-agent assignment endpoint.
  - Existing `Settings.chatwoot_agent_team_id` — still used as fallback when routing returns `None` or is disabled.
- Produces: modified `create_ticket` and `open_handoff` that call `RoutingService.pick_agent` when `routing_enabled` is True, then assign via individual-agent assignment (`assignee_id`) instead of team assignment; fall back to team assignment when routing is disabled or returns `None`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/test_routing_assignment.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.routing.service import RoutingService
from chatbot.platform.config import Settings


class _FakeClient:
    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._responses = responses

    async def _request(
        self, method: str, path: str, payload: Any = None
    ) -> Any:
        self.calls.append((method, path, payload))
        for (m, sub), resp in self._responses.items():
            if m == method and sub in path:
                return resp
        return {}


def _adapter(
    fake: _FakeClient,
    *,
    routing_enabled: bool = False,
    routing_svc: RoutingService | None = None,
    team_id: int = 3,
) -> ChatwootAdapter:
    a = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_agent_team_id=team_id,
            routing_enabled=routing_enabled,
        )
    )
    a._request = fake._request  # type: ignore[method-assign]
    if routing_svc is not None:
        a._routing_service = routing_svc  # type: ignore[attr-defined]
    return a


def _make_routing_svc(agent_id: int | None) -> RoutingService:
    svc = RoutingService.__new__(RoutingService)

    async def pick(channel: str) -> int | None:
        return agent_id

    svc.pick_agent = pick  # type: ignore[method-assign]
    return svc


@pytest.mark.asyncio
async def test_routing_disabled_uses_team_assignment() -> None:
    """When routing_enabled=False, the existing team assignment is used."""
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = _adapter(fake, routing_enabled=False, team_id=5)
    await adapter.create_ticket(
        session_id="s1", title="T", body="B", urgency="medium"
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"team_id": 5}


@pytest.mark.asyncio
async def test_routing_enabled_uses_agent_assignment() -> None:
    """When routing picks an agent, assignment uses assignee_id not team_id."""
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    svc = _make_routing_svc(agent_id=42)
    adapter = _adapter(fake, routing_enabled=True, routing_svc=svc, team_id=5)
    await adapter.create_ticket(
        session_id="s2", title="T", body="B", urgency="medium"
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"assignee_id": 42}


@pytest.mark.asyncio
async def test_routing_returns_none_falls_back_to_team() -> None:
    """When routing picks no agent (all busy), fall back to team assignment."""
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    svc = _make_routing_svc(agent_id=None)
    adapter = _adapter(fake, routing_enabled=True, routing_svc=svc, team_id=5)
    await adapter.create_ticket(
        session_id="s3", title="T", body="B", urgency="medium"
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"team_id": 5}


@pytest.mark.asyncio
async def test_open_handoff_uses_routing_when_enabled() -> None:
    """open_handoff also routes via RoutingService when enabled."""
    from chatbot.features.chat.models import HandoffOpenPayload

    fake = _FakeClient({("POST", "/conversations"): {"id": 77}})
    svc = _make_routing_svc(agent_id=9)
    adapter = _adapter(fake, routing_enabled=True, routing_svc=svc, team_id=5)
    await adapter.open_handoff(
        HandoffOpenPayload(
            session_id="s4",
            ai_summary="help",
            transcript=[],
            urgency="low",
            reason="help_request",
            language="en",
            live_chat_available=True,
            category=None,
            subcategory=None,
            division=None,
            department=None,
            sla_minutes=None,
            customer_name=None,
            customer_phone=None,
        )
    )
    assignment_calls = [
        (m, p, pl) for m, p, pl in fake.calls if "assignments" in p
    ]
    assert len(assignment_calls) == 1
    _, _, payload = assignment_calls[0]
    assert payload == {"assignee_id": 9}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest src/chatbot/features/chat/test_routing_assignment.py -v
```

Expected: `FAILED` — `ChatwootAdapter` has no `_routing_service` attribute and does not call `pick_agent`.

- [ ] **Step 3: Modify `ChatwootAdapter` to wire routing**

In `apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`:

Add the import block near the top (after `if TYPE_CHECKING:`):

```python
if TYPE_CHECKING:
    from chatbot.features.chat.adapters.zammad import ZammadClient
    from chatbot.features.routing.service import RoutingService
    from chatbot.platform.config import Settings
```

Add `_routing_service` attribute in `__init__`:

```python
    def __init__(self, settings: Settings, zammad: ZammadClient | None = None) -> None:
        self._settings = settings
        self._zammad = zammad
        self._paused_sessions: set[str] = set()
        self._conv_by_session: dict[str, str] = {}
        self._routing_service: RoutingService | None = None
```

Add a private helper method after `_is_complaint`:

```python
    async def _assign_conversation(self, conv_id: str, conv_channel: str = "web") -> None:
        """Assign a conversation to an agent (routing) or team (fallback).

        When ``routing_enabled`` is True and a ``RoutingService`` is wired, ask
        the service to pick an available agent honoring channel priority. If no
        agent is available (all busy/offline) or routing is disabled, fall back
        to the static team assignment configured in ``chatwoot_agent_team_id``.
        """
        if self._settings.routing_enabled and self._routing_service is not None:
            agent_id = await self._routing_service.pick_agent(conv_channel)
            if agent_id is not None:
                await self._request(
                    "POST",
                    f"/conversations/{conv_id}/assignments",
                    {"assignee_id": agent_id},
                )
                return
        # Fallback: static team assignment (pre-routing behaviour).
        if self._settings.chatwoot_agent_team_id:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/assignments",
                {"team_id": self._settings.chatwoot_agent_team_id},
            )
```

In `create_ticket`, replace the existing assignment block:

```python
        # Old code to REMOVE:
        # if self._settings.chatwoot_agent_team_id:
        #     await self._request(
        #         "POST",
        #         f"/conversations/{conv_id}/assignments",
        #         {"team_id": self._settings.chatwoot_agent_team_id},
        #     )

        # New code:
        await self._assign_conversation(conv_id, conv_channel="web")
```

The exact string to find and replace in `create_ticket` (around line 380):

```python
        if self._settings.chatwoot_agent_team_id:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/assignments",
                {"team_id": self._settings.chatwoot_agent_team_id},
            )
        await self.add_private_note(conv_id, f"[AI escalation] {title}\n\n{body}")
```

Replace with:

```python
        await self._assign_conversation(conv_id, conv_channel="web")
        await self.add_private_note(conv_id, f"[AI escalation] {title}\n\n{body}")
```

In `open_handoff`, find the equivalent block (around line 587):

```python
        if self._settings.chatwoot_agent_team_id:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/assignments",
                {"team_id": self._settings.chatwoot_agent_team_id},
            )
        if payload.sla_minutes is not None:
```

Replace with:

```python
        await self._assign_conversation(conv_id, conv_channel="web")
        if payload.sla_minutes is not None:
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest src/chatbot/features/chat/test_routing_assignment.py -v
```

Expected: `PASSED` (4 tests).

- [ ] **Step 5: Run full suite to catch regressions**

```bash
pytest src/chatbot/features/chat/ -v --tb=short 2>&1 | tail -30
```

Expected: all previously passing tests still pass; no regressions.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/features/chat/adapters/chatwoot.py \
        apps/backend/src/chatbot/features/chat/test_routing_assignment.py
git commit -m "feat(routing): wire RoutingService into ChatwootAdapter assignment (additive)"
```

---

## Task 7: Mount routing router on `main.py`

**Files:**
- Modify: `apps/backend/src/chatbot/main.py`
- Create: `apps/backend/src/chatbot/features/chat/test_routing_mount.py`

**Interfaces:**
- Consumes: `build_routing_router(settings, store, presence) -> APIRouter` (Task 5), `RoutingService(presence, store)` (Task 4), `ChatwootAdapter._routing_service` (Task 6).
- Produces: FastAPI app with `/routing/agents` and `/routing/priorities` routes live; `ChatwootAdapter` instance has `_routing_service` wired when `routing_enabled` is True.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_routing_mount.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_routing_agents_route_exists() -> None:
    """The /routing/agents endpoint is reachable on the main app."""
    from chatbot.main import create_app
    from chatbot.platform.config import Settings

    # Use a minimal settings object; chatwoot_enabled=False prevents real HTTP calls.
    settings = Settings(chatwoot_enabled=False, routing_enabled=False)
    app = create_app(settings)
    client = TestClient(app)
    # The endpoint exists even when routing_enabled=False (read-only, safe).
    resp = client.get("/routing/agents")
    # 200 or 500 (Chatwoot unreachable in test) — both prove the route is mounted.
    assert resp.status_code in (200, 500)


def test_routing_priorities_route_exists() -> None:
    from chatbot.main import create_app
    from chatbot.platform.config import Settings

    settings = Settings(chatwoot_enabled=False, routing_enabled=False)
    app = create_app(settings)
    client = TestClient(app)
    resp = client.get("/routing/priorities")
    # Returns empty list or error — either way the route is mounted.
    assert resp.status_code in (200, 500)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest src/chatbot/features/chat/test_routing_mount.py -v
```

Expected: `FAILED` if `create_app` does not exist or the routes are not mounted. (If `main.py` already has a `create_app` factory, adjust import path accordingly.)

- [ ] **Step 3: Add routing router and service wiring to `main.py`**

In `apps/backend/src/chatbot/main.py`, locate the app-construction section and add:

```python
from chatbot.features.routing.presence import PresenceFetcher
from chatbot.features.routing.router import build_routing_router
from chatbot.features.routing.service import RoutingService
from chatbot.features.routing.store import ChannelPriorityStore
```

After building the `ChatwootAdapter` instance (wherever `settings.crm_provider == "chatwoot"` sets up the adapter), add:

```python
    # Phase 5: agent routing service (wired only when routing_enabled=True)
    _presence = PresenceFetcher(settings)
    _priority_store = ChannelPriorityStore(settings)
    _routing_svc = RoutingService(presence=_presence, store=_priority_store)
    if settings.routing_enabled and hasattr(chatwoot_adapter, "_routing_service"):
        chatwoot_adapter._routing_service = _routing_svc

    app.include_router(
        build_routing_router(settings, _priority_store, _presence)
    )
```

(Replace `chatwoot_adapter` with the actual variable name used in `main.py` for the `ChatwootAdapter` instance — check `main.py` line ~30–120.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest src/chatbot/features/chat/test_routing_mount.py -v
```

Expected: `PASSED` (2 tests).

- [ ] **Step 5: Run full suite**

```bash
pytest src/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/main.py \
        apps/backend/src/chatbot/features/chat/test_routing_mount.py
git commit -m "feat(routing): mount routing router and wire RoutingService on main.py"
```

---

## Task 8: Channel priority config nav-app

**Files:**
- Create: `apps/chatwoot-routing-admin/index.html`

**Interfaces:**
- Consumes (at runtime): `GET /routing/agents` → `[{id, name, availability_status}]`, `GET /routing/priorities` → `[{agent_id, channel_priorities}]`, `POST/PUT/DELETE /routing/priorities` (all from Tasks 5/7).
- Query-string config: `?apiKey=<routing_admin_api_key>&backendBaseUrl=<url>` — identical pattern to `chatwoot-faq-admin`.

- [ ] **Step 1: Create the config nav-app**

Create `apps/chatwoot-routing-admin/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Proton Routing Admin</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: #f5f5f5; color: #333; font-size: 13px;
    }
    .container { padding: 16px; max-width: 1000px; margin: 0 auto; }
    .header {
      margin-bottom: 16px; border-bottom: 1px solid #ddd; padding-bottom: 12px;
      display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap;
    }
    .header h2 { font-size: 16px; font-weight: 600; color: #222; }
    .status { font-size: 12px; color: #666; }
    button {
      padding: 6px 12px; border: 1px solid #ddd; border-radius: 3px; background: white;
      cursor: pointer; font-size: 12px; font-weight: 500; transition: all 0.15s;
    }
    button:hover { background: #f5f5f5; border-color: #999; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary { background: #1a73e8; color: white; border-color: #1a73e8; }
    .btn-primary:hover { background: #1557b0; border-color: #1557b0; }
    .btn-danger { color: #ea4335; border-color: #f2c4c0; }
    .btn-danger:hover { background: #faf5f4; border-color: #ea4335; }
    .card {
      background: white; border: 1px solid #e0e0e0; border-radius: 6px;
      padding: 16px; margin-bottom: 16px;
    }
    .card h3 { font-size: 14px; font-weight: 600; margin-bottom: 12px; color: #222; }
    .field { margin-bottom: 10px; }
    .field label {
      display: block; font-size: 11px; font-weight: 600; color: #555;
      margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.03em;
    }
    .field select, .field input[type="text"] {
      width: 100%; padding: 8px 10px; border: 1px solid #ccc; border-radius: 4px;
      font-size: 13px; font-family: inherit; color: #333;
    }
    .field select:focus, .field input:focus {
      outline: none; border-color: #1a73e8; box-shadow: 0 0 0 2px rgba(26,115,232,0.15);
    }
    .field .hint { font-size: 10px; color: #999; margin-top: 3px; }
    .form-actions { display: flex; gap: 8px; margin-top: 4px; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
    .table-wrap {
      background: white; border: 1px solid #e0e0e0; border-radius: 6px; overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    thead th {
      text-align: left; padding: 10px 12px; background: #fafafa;
      border-bottom: 1px solid #e0e0e0; font-size: 11px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.03em; color: #666; white-space: nowrap;
    }
    tbody td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }
    tbody tr:last-child td { border-bottom: none; }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .chip {
      background: #eef4fd; color: #33507a; border-radius: 10px;
      padding: 2px 8px; font-size: 10px; white-space: nowrap;
    }
    .status-online { color: #34a853; font-weight: 600; }
    .status-busy { color: #fbbc04; font-weight: 600; }
    .status-offline { color: #aaa; }
    .cell-actions { white-space: nowrap; }
    .cell-actions button { padding: 4px 8px; margin-right: 4px; font-size: 11px; }
    .loading, .empty-state { text-align: center; padding: 28px; color: #999; font-size: 13px; }
    .banner { border-radius: 4px; padding: 12px; margin-bottom: 12px; font-size: 12px; line-height: 1.4; }
    .banner.error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
    .banner.info { background: #eef4fd; border: 1px solid #cfe0fb; color: #33507a; }
    #toasts {
      position: fixed; top: 16px; right: 16px; display: flex; flex-direction: column;
      gap: 8px; z-index: 1000; max-width: 320px;
    }
    .toast {
      padding: 10px 14px; border-radius: 4px; font-size: 12px; color: white;
      box-shadow: 0 2px 8px rgba(0,0,0,0.15); animation: slidein 0.2s ease-out;
    }
    .toast.success { background: #34a853; }
    .toast.error { background: #ea4335; }
    @keyframes slidein { from { transform: translateX(20px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(0,0,0,0.4);
      display: flex; align-items: flex-start; justify-content: center;
      padding: 40px 16px; z-index: 900; overflow-y: auto;
    }
    .modal-backdrop[hidden] { display: none; }
    .modal { background: white; border-radius: 8px; padding: 20px; width: 100%; max-width: 480px; }
    .modal h3 { margin-bottom: 14px; }
  </style>
</head>
<body>
  <div id="toasts"></div>
  <div class="container">
    <div class="header">
      <h2>Proton Routing Admin</h2>
      <div class="status" id="status">Initializing...</div>
    </div>
    <div id="banner"></div>

    <div class="card">
      <h3>Assign Channel Priorities to Agent</h3>
      <form id="add-form">
        <div class="field">
          <label for="add-agent">Agent</label>
          <select id="add-agent" required>
            <option value="">Loading agents…</option>
          </select>
        </div>
        <div class="field">
          <label for="add-channels">Channel priorities (comma-separated, first = highest priority)</label>
          <input type="text" id="add-channels" required placeholder="WhatsApp, email, web">
          <div class="hint">Example: WhatsApp, email — agent will be picked first for WhatsApp.</div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn-primary" id="add-submit">Save priority</button>
          <button type="reset">Clear</button>
        </div>
      </form>
    </div>

    <div class="toolbar">
      <button id="refresh-btn" type="button">Refresh</button>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Agent</th>
            <th>Current Status</th>
            <th>Channel Priorities</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="table-body">
          <tr><td colspan="4"><div class="loading">Loading…</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="modal-backdrop" id="edit-modal" hidden>
    <div class="modal">
      <h3>Edit Channel Priorities</h3>
      <form id="edit-form">
        <input type="hidden" id="edit-agent-id">
        <div class="field">
          <label>Agent</label>
          <div id="edit-agent-name" style="padding:8px 0;font-weight:600;"></div>
        </div>
        <div class="field">
          <label for="edit-channels">Channel priorities (comma-separated)</label>
          <input type="text" id="edit-channels" required>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn-primary" id="edit-submit">Save</button>
          <button type="button" id="edit-cancel">Cancel</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    const DEFAULT_BACKEND = 'https://proton-backend-247165654737.asia-southeast1.run.app';
    const params = new URLSearchParams(window.location.search);
    const backendBaseUrl = (params.get('backendBaseUrl') || DEFAULT_BACKEND).replace(/\/+$/, '');
    const apiKey = params.get('apiKey') || '';

    let agentsById = {};
    let prioritiesByAgentId = {};

    function requestContext() {
      try { window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*'); } catch(e) {}
    }
    window.addEventListener('message', () => {});

    function headers(write) {
      const h = { 'Content-Type': 'application/json' };
      if (write && apiKey) h['x-api-key'] = apiKey;
      return h;
    }

    async function apiFetch(path, options, write) {
      const res = await fetch(`${backendBaseUrl}${path}`, { ...options, headers: headers(write) });
      if (!res.ok) {
        if (res.status === 401) {
          showBanner('error', 'Unauthorized. Add <code>?apiKey=&lt;ROUTING_ADMIN_API_KEY&gt;</code> to the URL.');
          throw new Error('unauthorized');
        }
        let detail = res.statusText;
        try { const b = await res.json(); if (b && b.detail) detail = JSON.stringify(b.detail); } catch(e) {}
        throw new Error(`${res.status}: ${detail}`);
      }
      const text = await res.text();
      return text ? JSON.parse(text) : {};
    }

    async function loadAll() {
      setStatus('Loading…');
      clearBanner();
      document.getElementById('table-body').innerHTML =
        '<tr><td colspan="4"><div class="loading">Loading…</div></td></tr>';
      try {
        const [agentsList, prioritiesList] = await Promise.all([
          apiFetch('/routing/agents', { method: 'GET' }, false),
          apiFetch('/routing/priorities', { method: 'GET' }, false),
        ]);
        agentsById = {};
        (Array.isArray(agentsList) ? agentsList : []).forEach(a => { agentsById[a.id] = a; });
        prioritiesByAgentId = {};
        (Array.isArray(prioritiesList) ? prioritiesList : []).forEach(p => {
          prioritiesByAgentId[p.agent_id] = p;
        });
        populateAgentSelect();
        renderTable();
        setStatus(`${Object.keys(agentsById).length} agents`);
      } catch(err) {
        setStatus('Error');
        if (err.message !== 'unauthorized') showBanner('error', 'Failed to load: ' + escapeHtml(err.message));
        document.getElementById('table-body').innerHTML =
          '<tr><td colspan="4"><div class="empty-state">Could not load data.</div></td></tr>';
      }
    }

    function populateAgentSelect() {
      const sel = document.getElementById('add-agent');
      sel.innerHTML = '<option value="">Select agent…</option>';
      Object.values(agentsById).forEach(a => {
        const opt = document.createElement('option');
        opt.value = a.id;
        opt.textContent = `${a.name} (${a.availability_status})`;
        sel.appendChild(opt);
      });
    }

    function statusClass(s) {
      if (s === 'online') return 'status-online';
      if (s === 'busy') return 'status-busy';
      return 'status-offline';
    }

    function renderTable() {
      const body = document.getElementById('table-body');
      const rows = Object.values(agentsById);
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="4"><div class="empty-state">No agents found.</div></td></tr>';
        return;
      }
      body.innerHTML = rows.map(a => {
        const p = prioritiesByAgentId[a.id];
        const chips = p
          ? p.channel_priorities.map((c, i) =>
              `<span class="chip">${i === 0 ? '★ ' : ''}${escapeHtml(c)}</span>`).join('')
          : '<span style="color:#aaa;font-size:11px;">no priority set</span>';
        return `
          <tr data-id="${a.id}">
            <td><strong>${escapeHtml(a.name)}</strong><br><span style="color:#888;font-size:10px;">id: ${a.id}</span></td>
            <td><span class="${statusClass(a.availability_status)}">${escapeHtml(a.availability_status)}</span></td>
            <td><div class="chips">${chips}</div></td>
            <td class="cell-actions">
              <button data-action="edit">Edit</button>
              ${p ? `<button data-action="delete" class="btn-danger">Remove</button>` : ''}
            </td>
          </tr>`;
      }).join('');
      attachRowHandlers();
    }

    function attachRowHandlers() {
      document.querySelectorAll('#table-body tr[data-id]').forEach(row => {
        const id = parseInt(row.dataset.id, 10);
        const editBtn = row.querySelector('button[data-action="edit"]');
        const delBtn = row.querySelector('button[data-action="delete"]');
        if (editBtn) editBtn.addEventListener('click', () => openEdit(id));
        if (delBtn) delBtn.addEventListener('click', () => deletePriority(id));
      });
    }

    document.getElementById('add-form').addEventListener('submit', async ev => {
      ev.preventDefault();
      const submit = document.getElementById('add-submit');
      const agentId = parseInt(document.getElementById('add-agent').value, 10);
      const channels = splitCsv(document.getElementById('add-channels').value);
      if (!agentId || !channels.length) { toast('error', 'Agent and at least one channel are required.'); return; }
      submit.disabled = true;
      const existing = prioritiesByAgentId[agentId];
      try {
        if (existing) {
          await apiFetch(`/routing/priorities/${agentId}`, {
            method: 'PUT', body: JSON.stringify({ channel_priorities: channels })
          }, true);
        } else {
          await apiFetch('/routing/priorities', {
            method: 'POST', body: JSON.stringify({ agent_id: agentId, channel_priorities: channels })
          }, true);
        }
        toast('success', 'Priority saved.');
        document.getElementById('add-form').reset();
        await loadAll();
      } catch(err) {
        if (err.message !== 'unauthorized') toast('error', 'Save failed: ' + err.message);
      } finally {
        submit.disabled = false;
      }
    });

    function openEdit(agentId) {
      const p = prioritiesByAgentId[agentId];
      const a = agentsById[agentId];
      document.getElementById('edit-agent-id').value = agentId;
      document.getElementById('edit-agent-name').textContent = a ? a.name : String(agentId);
      document.getElementById('edit-channels').value = p ? p.channel_priorities.join(', ') : '';
      document.getElementById('edit-modal').hidden = false;
    }

    document.getElementById('edit-cancel').addEventListener('click', () => {
      document.getElementById('edit-modal').hidden = true;
    });
    document.getElementById('edit-modal').addEventListener('click', ev => {
      if (ev.target.id === 'edit-modal') document.getElementById('edit-modal').hidden = true;
    });

    document.getElementById('edit-form').addEventListener('submit', async ev => {
      ev.preventDefault();
      const submit = document.getElementById('edit-submit');
      const agentId = parseInt(document.getElementById('edit-agent-id').value, 10);
      const channels = splitCsv(document.getElementById('edit-channels').value);
      if (!channels.length) { toast('error', 'At least one channel is required.'); return; }
      submit.disabled = true;
      try {
        await apiFetch(`/routing/priorities/${agentId}`, {
          method: 'PUT', body: JSON.stringify({ channel_priorities: channels })
        }, true);
        toast('success', 'Priority updated.');
        document.getElementById('edit-modal').hidden = true;
        await loadAll();
      } catch(err) {
        if (err.message !== 'unauthorized') toast('error', 'Update failed: ' + err.message);
      } finally {
        submit.disabled = false;
      }
    });

    async function deletePriority(agentId) {
      const a = agentsById[agentId];
      const label = a ? a.name : String(agentId);
      if (!window.confirm(`Remove priority config for ${label}?`)) return;
      try {
        await apiFetch(`/routing/priorities/${agentId}`, { method: 'DELETE' }, true);
        toast('success', 'Priority removed.');
        await loadAll();
      } catch(err) {
        if (err.message !== 'unauthorized') toast('error', 'Delete failed: ' + err.message);
      }
    }

    function setStatus(text) { document.getElementById('status').textContent = text; }
    function showBanner(kind, html) {
      document.getElementById('banner').innerHTML = `<div class="banner ${kind}">${html}</div>`;
    }
    function clearBanner() { document.getElementById('banner').innerHTML = ''; }
    function toast(kind, msg) {
      const el = document.createElement('div');
      el.className = `toast ${kind}`;
      el.textContent = msg;
      document.getElementById('toasts').appendChild(el);
      setTimeout(() => el.remove(), 3500);
    }
    function splitCsv(v) {
      return String(v || '').split(',').map(s => s.trim()).filter(Boolean);
    }
    function escapeHtml(t) {
      const m = { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;' };
      return String(t).replace(/[&<>"']/g, c => m[c]);
    }

    document.getElementById('refresh-btn').addEventListener('click', loadAll);
    document.addEventListener('DOMContentLoaded', () => {
      requestContext();
      if (!apiKey) showBanner('info',
        'No API key provided. Add <code>?apiKey=&lt;ROUTING_ADMIN_API_KEY&gt;</code> to the URL.');
      loadAll();
    });
  </script>
</body>
</html>
```

- [ ] **Step 2: Smoke-test the app manually**

Open a terminal and serve the file locally to verify it loads without JS errors:

```bash
cd /path/to/proton-conversational-ai/apps/chatwoot-routing-admin
python3 -m http.server 8081
```

Open `http://localhost:8081/?apiKey=test&backendBaseUrl=http://localhost:8000` in a browser.

Expected: page loads, shows "No agents found." or agent list from the running backend (if available), no console errors.

- [ ] **Step 3: Commit**

```bash
git add apps/chatwoot-routing-admin/index.html
git commit -m "feat(routing): add channel priority config nav-app (routing admin)"
```

---

## Task 9: Smoke test against a real Chatwoot instance

**Files:** No new files.

> This task uses the existing `default` tenant Chatwoot instance. It requires `CHATWOOT_API_URL`, `CHATWOOT_API_TOKEN`, and `CHATWOOT_ACCOUNT_ID` set in the backend `.env`.

- [ ] **Step 1: Set env vars and run the backend**

```bash
cd apps/backend
ROUTING_ENABLED=true \
ROUTING_ADMIN_API_KEY=localtest \
CHATWOOT_ENABLED=true \
uvicorn chatbot.main:app --host 127.0.0.1 --port 8000 --reload
```

Expected: server starts without errors; log shows no import errors from `chatbot.features.routing`.

- [ ] **Step 2: Fetch agents from the live Chatwoot**

```bash
curl -s http://localhost:8000/routing/agents | python3 -m json.tool
```

Expected: JSON array with at least one agent object, each with `id`, `name`, `availability_status`.

- [ ] **Step 3: Set a channel priority**

```bash
curl -s -X POST http://localhost:8000/routing/priorities \
  -H "Content-Type: application/json" \
  -H "x-api-key: localtest" \
  -d '{"agent_id": 1, "channel_priorities": ["WhatsApp", "email"]}' \
  | python3 -m json.tool
```

Expected: `{"agent_id": 1, "channel_priorities": ["WhatsApp", "email"]}`.

- [ ] **Step 4: Read priorities back**

```bash
curl -s http://localhost:8000/routing/priorities | python3 -m json.tool
```

Expected: array containing the entry set in Step 3.

- [ ] **Step 5: Trigger an inbound escalation (if a test conversation exists)**

Use the existing `/webhooks/chatwoot` smoke (or the agent-app "send test escalation" flow) and verify in the Chatwoot UI that the conversation is assigned to the expected agent (not the team), when that agent is `online`.

Expected: conversation assigned to individual agent, not team; private note from the AI is present.

- [ ] **Step 6: Commit smoke-test notes**

No code to commit — record the smoke result as a comment in the PR description.

---

## Self-Review

### 1. Spec coverage

| Spec item | Task |
|---|---|
| #4 — Check who is on duty before escalating (presence read) | Task 2 (`PresenceFetcher.fetch_agents`) |
| #5 — Channel priorities per agent (config app + routing service) | Tasks 3, 5, 8 (`ChannelPriorityStore`, routing router, nav-app) |
| #6 — Polling assignment by agent status (idle/busy/offline) | Tasks 4, 6 (`RoutingService.pick_agent`, `ChatwootAdapter` wire-up) |
| #7 — Intelligent switch to idle agents when priority channel busy | Task 4 (Tier 3 idle fallback in `RoutingService.pick_agent`) |
| Cross-cutting: additive/reversible | Task 6 (`routing_enabled=False` leaves existing team assignment unchanged) |
| Cross-cutting: new backend on Cloud Run, not VM | Task 7 (no change to deploy topology; Cloud Run `main.py` mount) |
| Cross-cutting: secrets in Pydantic config, not committed | Task 1 (`routing_admin_api_key` env var) |
| Config nav-app for ops | Task 8 (channel priority editor with agent list, CRUD) |

No spec gaps found.

### 2. Placeholder scan

No "TBD", "TODO", or "implement later" found. All steps include complete code.

### 3. Type consistency

- `AgentRecord.id: int` produced in Task 2 → consumed in Task 4 (`online: dict[int, AgentRecord]`) ✓
- `AgentPriority.agent_id: int` and `AgentPriority.channel_priorities: list[str]` produced in Task 3 → consumed in Task 4 ✓
- `RoutingService.pick_agent(conv_channel: str) -> int | None` produced in Task 4 → consumed in Task 6 (`agent_id = await self._routing_service.pick_agent(conv_channel)`) ✓
- `build_routing_router(settings, store, presence) -> APIRouter` produced in Task 5 → consumed in Task 7 ✓
- `ChatwootAdapter._routing_service: RoutingService | None` added in Task 6 → set in Task 7 ✓
- `_assign_conversation(conv_id, conv_channel="web")` replaces both `create_ticket` and `open_handoff` team-assignment blocks in Task 6 ✓
