# Per-inbox Inactivity Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators set per-inbox conversation idle warn/close/confirm timing from Chatwoot's native inbox settings, stored in our backend and honored by the agent lifecycle scanner (falling back to env defaults).

**Architecture:** A new backend `InboxTimingStore` (Port + InMemory + Firestore, collection `inbox_timing`) holds four optional ints per inbox. Two new endpoints (`GET`/`PUT /kb/inboxes/{inbox_id}/timing`) serve the native-settings SPA card; the four values are also embedded in the existing `GET /kb/inboxes` list rows so the agent reads them from the response it already caches. The agent overrides each env default only when a per-inbox value is set. Business hours stay 100% native Chatwoot (untouched).

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, Firestore (`google-cloud-firestore`), pytest + respx (agent), FastAPI TestClient (backend), Vue 3 SPA fork patch (Chatwoot v4.15.1).

**Spec:** `docs/superpowers/specs/2026-07-27-per-inbox-inactivity-timing-design.md`

## Global Constraints

- The four timing keys, verbatim: `idle_warn_minutes`, `idle_close_grace_minutes`, `idle_close_out_of_hours_grace_minutes`, `confirm_grace_minutes`.
- Env-default fallbacks (agent `app/config.py`): `lifecycle_idle_warn_minutes=10`, `lifecycle_idle_close_grace_minutes=5`, `lifecycle_idle_close_out_of_hours_grace_minutes=0`, `lifecycle_confirm_grace_minutes=10`.
- Semantics: a value that is `None`/absent = inherit the env default; an explicit `0` is a valid value (kept distinct from unset). Precedence: per-inbox value (incl. `0`) > env default.
- Validation range for every value: integer `0..1440` inclusive.
- All new read paths fail-open (return `None`/`{}`/defaults, never raise). The agent lifecycle scan with no stored timing must behave byte-identically to today.
- `PUT` uses **full-replace** semantics (body is the complete desired state; `null`/omitted field = unset; no set fields → delete the doc).
- No Chatwoot Ruby patches. No new env vars. `assigned_idle_resolve_minutes` is NOT exposed.
- Backend tests: `cd backend/apps/backend && .venv/bin/pytest <path> -v`. Agent tests: `cd agent && pytest <path> -v` (asyncio_mode=auto — async tests need no decorator).
- Conventional commits (`<type>(<scope>): <desc>`).

---

### Task 1: Backend — `InboxTimingStore`

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py`

**Interfaces:**
- Consumes: nothing (new leaf module). Mirrors `inbox_assignment_store.py`.
- Produces:
  - `TIMING_KEYS: tuple[str, ...]` = the four keys in the order above.
  - `InboxTimingStorePort` (Protocol): `get_all() -> dict[int, dict[str, int]]`, `get(inbox_id: int) -> dict[str, int] | None`, `set(inbox_id: int, timing: dict[str, int]) -> None`, `delete(inbox_id: int) -> None`.
  - `InMemoryInboxTimingStore`, `FirestoreInboxTimingStore` (collection `inbox_timing`), `build_inbox_timing_store(settings) -> InMemoryInboxTimingStore | FirestoreInboxTimingStore`.
  - Stored dict contains only set keys (values are plain ints). `get`/`get_all` return only stored keys (no `None` padding — the router/agent normalize).

- [ ] **Step 1: Write the failing test**

Create `backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py`:

```python
"""Tests for InMemoryInboxTimingStore (round-trip, partial, delete, keys)."""
from __future__ import annotations

from chatbot.features.chat.adapters.inbox_timing_store import (
    TIMING_KEYS,
    InboxTimingStorePort,
    InMemoryInboxTimingStore,
)


def test_timing_keys_are_the_four_lifecycle_fields():
    assert TIMING_KEYS == (
        "idle_warn_minutes",
        "idle_close_grace_minutes",
        "idle_close_out_of_hours_grace_minutes",
        "confirm_grace_minutes",
    )


def test_inmemory_satisfies_port():
    assert isinstance(InMemoryInboxTimingStore(), InboxTimingStorePort)


async def test_set_get_roundtrip_full():
    store = InMemoryInboxTimingStore()
    await store.set(7, {
        "idle_warn_minutes": 12,
        "idle_close_grace_minutes": 3,
        "idle_close_out_of_hours_grace_minutes": 0,
        "confirm_grace_minutes": 8,
    })
    assert await store.get(7) == {
        "idle_warn_minutes": 12,
        "idle_close_grace_minutes": 3,
        "idle_close_out_of_hours_grace_minutes": 0,
        "confirm_grace_minutes": 8,
    }


async def test_set_partial_stores_only_given_keys():
    store = InMemoryInboxTimingStore()
    await store.set(7, {"idle_warn_minutes": 15})
    assert await store.get(7) == {"idle_warn_minutes": 15}


async def test_get_missing_returns_none():
    store = InMemoryInboxTimingStore()
    assert await store.get(999) is None


async def test_get_all_returns_copies():
    store = InMemoryInboxTimingStore()
    await store.set(1, {"confirm_grace_minutes": 5})
    all_ = await store.get_all()
    assert all_ == {1: {"confirm_grace_minutes": 5}}
    all_[1]["confirm_grace_minutes"] = 999  # mutating the copy must not leak
    assert await store.get(1) == {"confirm_grace_minutes": 5}


async def test_delete_removes_entry():
    store = InMemoryInboxTimingStore()
    await store.set(1, {"idle_warn_minutes": 10})
    await store.delete(1)
    assert await store.get(1) is None
    await store.delete(1)  # idempotent, no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/adapters/test_inbox_timing_store.py -v`
Expected: FAIL (`ModuleNotFoundError: inbox_timing_store`).

- [ ] **Step 3: Write minimal implementation**

Create `backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py`:

```python
"""Per-inbox conversation-lifecycle timing store.

Maps each Chatwoot inbox_id to a subset of the four lifecycle timing values
(`idle_warn_minutes`, `idle_close_grace_minutes`,
`idle_close_out_of_hours_grace_minutes`, `confirm_grace_minutes`). Only the
keys an operator explicitly sets are stored; an absent key means "inherit the
agent's global env default" (resolved in the agent, not here). An explicit 0 is
a valid, stored value.

Deliberately SEPARATE from `inbox_assignment_store`: an inbox can use the
default assistant (no stored assignment) yet still need custom timing, so the
two must not be coupled.

Follows the same Port + InMemory + Firestore pattern as
`inbox_assignment_store.py`. Firestore collection `inbox_timing`, one doc per
inbox_id (doc id = str(inbox_id)). Sync SDK via asyncio.to_thread; every read
degrades to None/{} on failure — never raises.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

TIMING_KEYS: tuple[str, ...] = (
    "idle_warn_minutes",
    "idle_close_grace_minutes",
    "idle_close_out_of_hours_grace_minutes",
    "confirm_grace_minutes",
)


@runtime_checkable
class InboxTimingStorePort(Protocol):
    """Read-and-write interface for per-inbox lifecycle timing.

    Reads never raise (get -> None, get_all -> {}); writes swallow after logging.
    """

    async def get_all(self) -> dict[int, dict[str, int]]: ...

    async def get(self, inbox_id: int) -> dict[str, int] | None: ...

    async def set(self, inbox_id: int, timing: dict[str, int]) -> None: ...

    async def delete(self, inbox_id: int) -> None: ...


class InMemoryInboxTimingStore:
    """Volatile timing store — for tests and local dev."""

    def __init__(self) -> None:
        self._data: dict[int, dict[str, int]] = {}

    async def get_all(self) -> dict[int, dict[str, int]]:
        return {k: dict(v) for k, v in self._data.items()}

    async def get(self, inbox_id: int) -> dict[str, int] | None:
        entry = self._data.get(inbox_id)
        return dict(entry) if entry is not None else None

    async def set(self, inbox_id: int, timing: dict[str, int]) -> None:
        self._data[inbox_id] = {k: int(v) for k, v in timing.items() if k in TIMING_KEYS}

    async def delete(self, inbox_id: int) -> None:
        self._data.pop(inbox_id, None)


class FirestoreInboxTimingStore:
    """Firestore-backed timing store (collection `inbox_timing`)."""

    _COLLECTION = "inbox_timing"

    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        _log.info(
            "firestore_inbox_timing_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )

    def _collection(self) -> Any:
        return self._client.collection(self._COLLECTION)

    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for k in TIMING_KEYS:
            v = data.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                out[k] = v
        return out

    async def get_all(self) -> dict[int, dict[str, int]]:
        def _read() -> dict[int, dict[str, int]]:
            result: dict[int, dict[str, int]] = {}
            for doc in self._collection().stream():
                try:
                    inbox_id = int(doc.id)
                except ValueError:
                    continue
                result[inbox_id] = self._clean(doc.to_dict() or {})
            return result

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("inbox_timing_get_all_failed", error=str(e))
            return {}

    async def get(self, inbox_id: int) -> dict[str, int] | None:
        def _read() -> dict[str, int] | None:
            snap = self._collection().document(str(inbox_id)).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            return self._clean(data) if isinstance(data, dict) else None

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("inbox_timing_get_failed", inbox_id=inbox_id, error=str(e))
            return None

    async def set(self, inbox_id: int, timing: dict[str, int]) -> None:
        cleaned = {k: int(v) for k, v in timing.items() if k in TIMING_KEYS}

        def _write() -> None:
            self._collection().document(str(inbox_id)).set(cleaned)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("inbox_timing_set_failed", inbox_id=inbox_id, error=str(e))

    async def delete(self, inbox_id: int) -> None:
        def _delete() -> None:
            self._collection().document(str(inbox_id)).delete()

        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            _log.error("inbox_timing_delete_failed", inbox_id=inbox_id, error=str(e))


def build_inbox_timing_store(
    settings: Settings,
) -> InMemoryInboxTimingStore | FirestoreInboxTimingStore:
    """Firestore when firestore_project_id is set, else InMemory (tests/dev)."""
    if settings.firestore_project_id:
        try:
            return FirestoreInboxTimingStore(settings)
        except Exception as e:
            _log.warning("firestore_inbox_timing_store_init_failed", error=str(e))
    return InMemoryInboxTimingStore()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/adapters/test_inbox_timing_store.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py \
        backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py
git commit -m "feat(backend): add per-inbox InboxTimingStore (Port + InMemory + Firestore)"
```

---

### Task 2: Backend — timing endpoints + list-row embedding + bootstrap wiring

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py` (build the store, thread it through `_wire_agent_assist` → `build_kb_inboxes_router`)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py`

**Interfaces:**
- Consumes: `InboxTimingStorePort`, `TIMING_KEYS`, `InMemoryInboxTimingStore`, `build_inbox_timing_store` (Task 1).
- Produces:
  - `build_kb_inboxes_router(...)` gains a new **required** parameter `timing_store: InboxTimingStorePort` (added as the last positional/keyword arg).
  - `GET /kb/inboxes/{inbox_id}/timing` → `{k: int | None for k in TIMING_KEYS}`.
  - `PUT /kb/inboxes/{inbox_id}/timing` (body `InboxTimingBody`, four `int | None = None`) → stored timing normalized to `{k: int | None}`.
  - `GET /kb/inboxes` rows each gain the four keys (`int | None`).

- [ ] **Step 1: Write the failing test**

Create `backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py`:

```python
"""Tests for GET/PUT /kb/inboxes/{id}/timing and timing embedded in list rows."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.assistants_store import (
    Assistant,
    AssistantConfig,
    InMemoryAssistantsStore,
)
from chatbot.features.chat.adapters.inbox_assignment_store import InMemoryInboxAssignmentStore
from chatbot.features.chat.adapters.inbox_timing_store import InMemoryInboxTimingStore
from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.features.chat.kb_inboxes_router import build_kb_inboxes_router
from chatbot.platform.config import Settings

_KEY = "test-api-key"
_H = {"x-api-key": _KEY}
_ALL_NULL = {
    "idle_warn_minutes": None,
    "idle_close_grace_minutes": None,
    "idle_close_out_of_hours_grace_minutes": None,
    "confirm_grace_minutes": None,
}


def _settings() -> Settings:
    return Settings(faq_admin_api_key=_KEY, proton_backend_key=_KEY, chatwoot_enabled=False)


def _default_assistant() -> Assistant:
    return Assistant(
        id="asst_001", name="Anya", description="", product_name="",
        config=AssistantConfig(), enabled=True, is_default=True,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _client(timing_store=None, chatwoot_inboxes=None):
    assistants = InMemoryAssistantsStore()
    assistants._assistants["asst_001"] = _default_assistant()  # seed default
    tenant = InMemoryTenantSettingsStore()
    assignments = InMemoryInboxAssignmentStore()
    timing = timing_store or InMemoryInboxTimingStore()
    cw = MagicMock()
    cw.list_inboxes = AsyncMock(return_value=chatwoot_inboxes or [])
    app = FastAPI()
    app.include_router(
        build_kb_inboxes_router(assignments, assistants, tenant, cw, _settings(), timing)
    )
    return TestClient(app), timing


def test_get_timing_unset_returns_all_null():
    client, _ = _client()
    r = client.get("/kb/inboxes/5/timing", headers=_H)
    assert r.status_code == 200
    assert r.json() == _ALL_NULL


def test_put_then_get_roundtrip():
    client, _ = _client()
    body = {"idle_warn_minutes": 12, "idle_close_grace_minutes": 3,
            "idle_close_out_of_hours_grace_minutes": 0, "confirm_grace_minutes": 8}
    r = client.put("/kb/inboxes/5/timing", json=body, headers=_H)
    assert r.status_code == 200
    assert r.json() == body
    assert client.get("/kb/inboxes/5/timing", headers=_H).json() == body


def test_put_null_field_is_unset():
    client, _ = _client()
    body = {"idle_warn_minutes": 15, "idle_close_grace_minutes": None,
            "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None}
    client.put("/kb/inboxes/5/timing", json=body, headers=_H)
    got = client.get("/kb/inboxes/5/timing", headers=_H).json()
    assert got == {**_ALL_NULL, "idle_warn_minutes": 15}


def test_put_all_null_deletes_doc():
    store = InMemoryInboxTimingStore()
    client, timing = _client(timing_store=store)
    client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": 9}, headers=_H)
    client.put("/kb/inboxes/5/timing", json=_ALL_NULL, headers=_H)
    assert store._data.get(5) is None


def test_put_out_of_range_is_422():
    client, _ = _client()
    r = client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": 5000}, headers=_H)
    assert r.status_code == 422
    r2 = client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": -1}, headers=_H)
    assert r2.status_code == 422


def test_timing_endpoints_require_auth():
    client, _ = _client()
    assert client.get("/kb/inboxes/5/timing").status_code == 401
    assert client.put("/kb/inboxes/5/timing", json=_ALL_NULL).status_code == 401


def test_list_rows_include_timing():
    store = InMemoryInboxTimingStore()
    client, _ = _client(
        timing_store=store,
        chatwoot_inboxes=[{"id": 5, "name": "WA", "channel_type": "Channel::Whatsapp"}],
    )
    client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": 7}, headers=_H)
    rows = client.get("/kb/inboxes", headers=_H).json()["inboxes"]
    row = next(r for r in rows if r["inbox_id"] == 5)
    assert row["idle_warn_minutes"] == 7
    assert row["confirm_grace_minutes"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_kb_inboxes_timing.py -v`
Expected: FAIL (`build_kb_inboxes_router` takes no `timing_store` / routes 404).

- [ ] **Step 3a: Implement the router changes**

In `kb_inboxes_router.py`:

Add imports near the top-level imports:
```python
from pydantic import BaseModel, Field
```
(keep the existing `from pydantic import BaseModel` — merge into one line) and, under the `TYPE_CHECKING` block, add:
```python
    from chatbot.features.chat.adapters.inbox_timing_store import InboxTimingStorePort
```
Add a module-level import (not under TYPE_CHECKING, needed at runtime for the key list):
```python
from chatbot.features.chat.adapters.inbox_timing_store import TIMING_KEYS
```

Add the request model and a normalizer below `InboxAssignmentBody`:
```python
class InboxTimingBody(BaseModel):
    """Full-replace body for PUT /kb/inboxes/{id}/timing.

    Each field is optional; a value that is None (or omitted) means "unset"
    (inherit the agent env default). Non-null values must be 0..1440.
    """

    idle_warn_minutes: int | None = Field(default=None, ge=0, le=1440)
    idle_close_grace_minutes: int | None = Field(default=None, ge=0, le=1440)
    idle_close_out_of_hours_grace_minutes: int | None = Field(default=None, ge=0, le=1440)
    confirm_grace_minutes: int | None = Field(default=None, ge=0, le=1440)


def _normalize_timing(stored: dict[str, int] | None) -> dict[str, int | None]:
    """Return the four keys, each int (if stored) or None (if unset)."""
    stored = stored or {}
    return {k: stored.get(k) for k in TIMING_KEYS}
```

Change the factory signature to accept the store (add as last param):
```python
def build_kb_inboxes_router(
    assignment_store: InboxAssignmentStorePort,
    assistants_store: AssistantsStorePort,
    tenant_settings_store: TenantSettingsStorePort,
    chatwoot_adapter: ChatwootAdapter | None,
    settings: Settings,
    timing_store: InboxTimingStorePort,
) -> APIRouter:
```

In `list_inboxes`, fetch all timing once (after `stored = await assignment_store.get_all()` block) and merge into each row. Add:
```python
        try:
            all_timing = await timing_store.get_all()
        except Exception as exc:
            _log.error("kb_inboxes_timing_store_failed", error=str(exc))
            all_timing = {}
```
Then in BOTH row-append blocks (the Chatwoot-inbox loop and the stored-only loop), spread the normalized timing into the row dict, e.g. change each `rows.append({...})` to include:
```python
                    **_normalize_timing(all_timing.get(inbox_id)),
```
(add that line as the last entry inside each row dict literal).

Add the two new endpoints before `return router`:
```python
    @router.get("/kb/inboxes/{inbox_id}/timing")
    async def get_inbox_timing(
        inbox_id: int,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, int | None]:
        """Return the four lifecycle timing values for an inbox (null = unset)."""
        _authorize(x_api_key)
        return _normalize_timing(await timing_store.get(inbox_id))

    @router.put("/kb/inboxes/{inbox_id}/timing")
    async def put_inbox_timing(
        inbox_id: int,
        body: InboxTimingBody,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, int | None]:
        """Full-replace the inbox timing. Non-null fields are stored; if none are
        set the doc is deleted (revert to env defaults)."""
        _authorize(x_api_key)
        to_store = {
            k: v for k, v in body.model_dump().items() if v is not None
        }
        if to_store:
            await timing_store.set(inbox_id, to_store)
        else:
            await timing_store.delete(inbox_id)
        return _normalize_timing(await timing_store.get(inbox_id))
```

- [ ] **Step 3b: Wire the store into bootstrap**

In `backend/apps/backend/src/chatbot/main.py`:

1. Add the import next to the assignment-store import (line ~16):
```python
from chatbot.features.chat.adapters.inbox_timing_store import build_inbox_timing_store
```
2. In `_wire_agent_assist(...)` signature (line ~162), add a param after `assignment_store: object,`:
```python
    timing_store: object,
```
3. In that function's `build_kb_inboxes_router(...)` call (line ~202), pass it as the final arg:
```python
        build_kb_inboxes_router(
            assignment_store,  # type: ignore[arg-type]
            assistants_store,  # type: ignore[arg-type]
            tenant_settings_store,  # type: ignore[arg-type]
            chatwoot_adapter_for_inboxes,
            settings,
            timing_store,  # type: ignore[arg-type]
        )
```
4. Build the shared store next to the others (after line ~370 `_shared_assignment_store = ...`):
```python
    _shared_timing_store = build_inbox_timing_store(settings)
```
5. In the `_wire_agent_assist(...)` call (line ~409), pass it after `assignment_store=_shared_assignment_store,`:
```python
        timing_store=_shared_timing_store,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_kb_inboxes_timing.py src/chatbot/features/chat/test_kb_inboxes_router.py -v`
Expected: PASS (new file green; existing router test still green — note it constructs the router directly and will now need the extra arg ONLY if it calls the factory; if `test_kb_inboxes_router.py` fails on the new required arg, update its `build_kb_inboxes_router(...)` calls to pass `InMemoryInboxTimingStore()` as the final arg, then re-run).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py \
        backend/apps/backend/src/chatbot/main.py \
        backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py \
        backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_router.py
git commit -m "feat(backend): serve per-inbox timing via /kb/inboxes/{id}/timing + list rows"
```

---

### Task 3: Agent — `get_assistant_lifecycle_timing` on ProtonConfigClient

**Files:**
- Modify: `agent/app/clients/proton.py`
- Test: `agent/tests/test_proton_client.py` (append)

**Interfaces:**
- Consumes: the `GET /kb/inboxes` response rows now carry the four timing keys (Task 2). Uses the existing `self._fetch_cached("/kb/inboxes")`.
- Produces: `ProtonConfigClient.get_assistant_lifecycle_timing(inbox_id: int) -> dict[str, int | None] | None` — returns `{k: int | None}` for the four keys when a row for `inbox_id` exists, else `None`. Never raises. Non-int values coerce to `None`.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_proton_client.py`. First extend the module-level `INBOXES_RESPONSE` (near the top) so inbox 10 carries timing and inbox 20 has none:

```python
# Add timing to the existing INBOXES_RESPONSE rows (edit in place):
#   inbox 10 -> full timing incl. an explicit 0; inbox 20 -> no timing keys.
```

Then append these tests at the end of the file:

```python
_TIMING_INBOXES = {
    "inboxes": [
        {"inbox_id": 10, "assistant_id": "asst-abc",
         "idle_warn_minutes": 12, "idle_close_grace_minutes": 3,
         "idle_close_out_of_hours_grace_minutes": 0, "confirm_grace_minutes": 8},
        {"inbox_id": 20, "assistant_id": "asst-xyz"},  # no timing keys
        {"inbox_id": 30, "idle_warn_minutes": "bad"},  # non-int -> None
    ]
}


@respx.mock
async def test_lifecycle_timing_full_row():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=_TIMING_INBOXES)
    )
    client = _make_client()
    assert await client.get_assistant_lifecycle_timing(10) == {
        "idle_warn_minutes": 12,
        "idle_close_grace_minutes": 3,
        "idle_close_out_of_hours_grace_minutes": 0,
        "confirm_grace_minutes": 8,
    }


@respx.mock
async def test_lifecycle_timing_row_without_keys_is_all_none():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=_TIMING_INBOXES)
    )
    client = _make_client()
    assert await client.get_assistant_lifecycle_timing(20) == {
        "idle_warn_minutes": None,
        "idle_close_grace_minutes": None,
        "idle_close_out_of_hours_grace_minutes": None,
        "confirm_grace_minutes": None,
    }


@respx.mock
async def test_lifecycle_timing_coerces_non_int_to_none():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=_TIMING_INBOXES)
    )
    client = _make_client()
    assert (await client.get_assistant_lifecycle_timing(30))["idle_warn_minutes"] is None


@respx.mock
async def test_lifecycle_timing_unknown_inbox_returns_none():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=_TIMING_INBOXES)
    )
    client = _make_client()
    assert await client.get_assistant_lifecycle_timing(404) is None


@respx.mock
async def test_lifecycle_timing_http_error_returns_none():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(500, json={})
    )
    client = _make_client()
    assert await client.get_assistant_lifecycle_timing(10) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_proton_client.py -k lifecycle_timing -v`
Expected: FAIL (`AttributeError: get_assistant_lifecycle_timing`).

- [ ] **Step 3: Write minimal implementation**

In `agent/app/clients/proton.py`, add a module-level constant near the top (after imports) and the method on `ProtonConfigClient` (place it after `get_assistant_persona`):

```python
_LIFECYCLE_TIMING_KEYS = (
    "idle_warn_minutes",
    "idle_close_grace_minutes",
    "idle_close_out_of_hours_grace_minutes",
    "confirm_grace_minutes",
)
```

```python
    async def get_assistant_lifecycle_timing(
        self, inbox_id: int
    ) -> dict[str, int | None] | None:
        """Per-inbox lifecycle timing overrides, or None. Fail-open.

        Reads the four timing keys from the row for *inbox_id* in the cached
        GET /kb/inboxes response (shares the same fetch/TTL as the mode + message
        resolvers, so no extra HTTP round-trip). Each value is an int when set,
        else None (inherit the agent's env default). Returns None when no row
        matches or on any error — never raises.
        """
        try:
            data = await self._fetch_cached("/kb/inboxes")
            if not isinstance(data, dict):
                return None
            inboxes = data.get("inboxes")
            if not isinstance(inboxes, list):
                return None
            row = next(
                (r for r in inboxes if isinstance(r, dict) and r.get("inbox_id") == inbox_id),
                None,
            )
            if row is None:
                return None
            result: dict[str, int | None] = {}
            for key in _LIFECYCLE_TIMING_KEYS:
                v = row.get(key)
                result[key] = v if isinstance(v, int) and not isinstance(v, bool) else None
            return result
        except Exception:
            logger.debug(
                "proton_config: error fetching lifecycle timing for inbox %s",
                inbox_id,
                exc_info=True,
            )
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_proton_client.py -v`
Expected: PASS (new timing tests + all existing tests still green).

- [ ] **Step 5: Commit**

```bash
git add agent/app/clients/proton.py agent/tests/test_proton_client.py
git commit -m "feat(agent): read per-inbox lifecycle timing from cached /kb/inboxes"
```

---

### Task 4: Agent — lifecycle wrapper + scanner override

**Files:**
- Modify: `agent/app/services/lifecycle.py` (add `_fetch_lifecycle_timing`)
- Modify: `agent/app/services/lifecycle_scanner.py` (override env defaults with per-inbox values in `_process_one`)
- Test: `agent/tests/test_lifecycle_scanner.py` (append)

**Interfaces:**
- Consumes: `ProtonConfigClient.get_assistant_lifecycle_timing` (Task 3), existing `get_proton_config_client()`.
- Produces:
  - `lifecycle._fetch_lifecycle_timing(inbox_id: int | None) -> dict[str, int | None] | None` (fail-open None).
  - `lifecycle_scanner._process_one` uses per-inbox values (incl. `0`) when present, else `settings.*` env defaults.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_lifecycle_scanner.py`:

```python
async def test_scan_uses_per_inbox_warn_override(wired, monkeypatch):
    # Env default warn=10; conversation is idle 12 min. Override warn to 20 min
    # (via per-inbox timing) so the conversation is NOT yet warned.
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"idle_warn_minutes": 20, "idle_close_grace_minutes": None,
                "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "active"  # 12 < 20 -> no warn
    wired.create_message.assert_not_awaited()


async def test_scan_falls_back_to_env_default_when_no_timing(wired, monkeypatch):
    # No per-inbox timing -> env default warn=10; idle 12 -> warned (today's behavior).
    from app.services import lifecycle

    async def _timing(inbox_id):
        return None

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "idle_warned"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_scanner.py -k per_inbox_warn -v`
Expected: FAIL (`AttributeError: _fetch_lifecycle_timing`, or override not applied → state is `idle_warned`).

- [ ] **Step 3a: Add the fetch wrapper**

In `agent/app/services/lifecycle.py`, add after `_fetch_assistant_messages` (near line ~90):

```python
async def _fetch_lifecycle_timing(inbox_id: int | None) -> dict | None:
    """Fetch per-inbox lifecycle timing overrides for inbox_id, fail-open to None."""
    proton = get_proton_config_client()
    if proton is None or inbox_id is None:
        return None
    try:
        return await proton.get_assistant_lifecycle_timing(inbox_id)
    except Exception:
        logger.debug(
            "lifecycle: could not fetch lifecycle timing for inbox %s", inbox_id,
            exc_info=True,
        )
        return None
```

- [ ] **Step 3b: Apply the override in the scanner**

In `agent/app/services/lifecycle_scanner.py::_process_one`, replace the timing block (currently ~lines 143–151):

```python
    in_hours = business_hours.is_within_business_hours(inbox, now)
    warn_after = settings.lifecycle_idle_warn_minutes
    grace = (
        settings.lifecycle_idle_close_grace_minutes
        if in_hours
        else settings.lifecycle_idle_close_out_of_hours_grace_minutes
    )
    close_after = warn_after + grace
    confirm_after = settings.lifecycle_confirm_grace_minutes
```

with:

```python
    in_hours = business_hours.is_within_business_hours(inbox, now)

    # Per-inbox overrides (fail-open): a value present (incl. 0) wins over the
    # env default; None/absent inherits the global default so behavior with no
    # stored timing is byte-identical to before.
    timing = await lifecycle._fetch_lifecycle_timing(inbox_id) or {}

    def _pick(key: str, default: int) -> int:
        v = timing.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else default

    warn_after = _pick("idle_warn_minutes", settings.lifecycle_idle_warn_minutes)
    grace = (
        _pick("idle_close_grace_minutes", settings.lifecycle_idle_close_grace_minutes)
        if in_hours
        else _pick(
            "idle_close_out_of_hours_grace_minutes",
            settings.lifecycle_idle_close_out_of_hours_grace_minutes,
        )
    )
    close_after = warn_after + grace
    confirm_after = _pick("confirm_grace_minutes", settings.lifecycle_confirm_grace_minutes)
```

(`lifecycle` is already imported in `lifecycle_scanner.py` — confirm the existing `from app.services import ... lifecycle ...` import at the top; it is used at line ~159.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_lifecycle_scanner.py -v`
Expected: PASS (new override + fallback tests, and all pre-existing scanner tests still green).

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/lifecycle.py agent/app/services/lifecycle_scanner.py \
        agent/tests/test_lifecycle_scanner.py
git commit -m "feat(agent): apply per-inbox lifecycle timing overrides in scanner"
```

---

### Task 5: UI — native inbox-settings "Inactivity & auto-close" card (fork patch)

**Files:**
- Create: `deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch`
- (Reference, not in this checkout) modifies in the built SPA:
  `app/javascript/dashboard/api/protonKnowledge.js` and the Chatwoot v4.15.1
  inbox business-hours settings Vue component.

**Interfaces:**
- Consumes: `GET`/`PUT /kb/inboxes/{inbox_id}/timing` (Task 2) via the existing `kbRequest` bridge.
- Produces: an operator-facing card in Settings → Inboxes → (inbox) that reads/writes the four timing values.

> This task cannot be fully unit-tested here (the Chatwoot SPA source is not in this checkout — it is pulled from the pinned `chatwoot/chatwoot:v4.15.1` image at build time). It is a research-then-patch task with a manual verification gate.

- [ ] **Step 1: Locate the upstream insertion point (research)**

Extract the SPA source for the pinned version and find the inbox business-hours settings component and the settings-page tab host:

```bash
# from repo root
docker create --name cw-src chatwoot/chatwoot:v4.15.1
docker cp cw-src:/app/app/javascript/dashboard/routes/dashboard/settings/inbox ./_cw_inbox_settings
docker rm cw-src
grep -rln "working_hours\|WeeklyAvailability\|business" ./_cw_inbox_settings
```

Identify: (a) the component that renders the Business Hours settings view (likely under `settings/inbox/`), and (b) how it fetches/knows the current `inbox.id`. Record the exact file path + the line to insert the card after. Delete `./_cw_inbox_settings` when done.

- [ ] **Step 2: Add the API helper**

Add to `app/javascript/dashboard/api/protonKnowledge.js` (mirror the existing `getInboxes`/`setInbox` helpers from patch 0017):

```javascript
// GET /kb/inboxes/{id}/timing -> { idle_warn_minutes, idle_close_grace_minutes,
//   idle_close_out_of_hours_grace_minutes, confirm_grace_minutes } (each int|null)
export function getInboxTiming(inboxId) {
  return kbRequest(`/kb/inboxes/${encodeURIComponent(inboxId)}/timing`);
}

// PUT /kb/inboxes/{id}/timing  body: the four fields (null = inherit default)
export function setInboxTiming(inboxId, body) {
  return kbRequest(`/kb/inboxes/${encodeURIComponent(inboxId)}/timing`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}
```

- [ ] **Step 3: Add the card to the settings view**

Insert an "Inactivity & auto-close" card into the component found in Step 1. Use the tenant's inbox id (`this.inbox.id` / the route param — match how the surrounding component references it). Card contents:

```vue
<!-- Inactivity & auto-close (Proton) — per-inbox override of the AI lifecycle
     idle timing. Empty field = inherit the global default. Saves to the Proton
     backend, not Chatwoot. -->
<div class="proton-inactivity-card">
  <h4>Inactivity &amp; auto-close</h4>
  <p class="text-muted">
    Warn the customer after N idle minutes, close after the grace period
    (separate in-hours vs out-of-hours grace), and the resolution-confirm grace.
    Leave a field empty to use the global default.
  </p>
  <label>Warn after idle (min)
    <input type="number" min="0" max="1440" step="1" v-model.number="timing.idle_warn_minutes" placeholder="10" />
  </label>
  <label>Close grace — in business hours (min)
    <input type="number" min="0" max="1440" step="1" v-model.number="timing.idle_close_grace_minutes" placeholder="5" />
  </label>
  <label>Close grace — out of hours (min)
    <input type="number" min="0" max="1440" step="1" v-model.number="timing.idle_close_out_of_hours_grace_minutes" placeholder="0" />
  </label>
  <label>Resolution-confirm grace (min)
    <input type="number" min="0" max="1440" step="1" v-model.number="timing.confirm_grace_minutes" placeholder="10" />
  </label>
  <woot-button @click="saveTiming" :is-loading="timingSaving">Save timing</woot-button>
</div>
```

Script (adapt to the component's Options/Composition API style):

```javascript
import { getInboxTiming, setInboxTiming } from 'dashboard/api/protonKnowledge';
// data(): timing: { idle_warn_minutes: null, idle_close_grace_minutes: null,
//   idle_close_out_of_hours_grace_minutes: null, confirm_grace_minutes: null },
//   timingSaving: false
// mounted()/created(): const t = await getInboxTiming(this.inbox.id); if (t) this.timing = t;
// methods.saveTiming():
async saveTiming() {
  this.timingSaving = true;
  try {
    // Empty inputs come through as '' or null via v-model.number; normalize '' -> null.
    const norm = k => (this.timing[k] === '' || this.timing[k] == null ? null : this.timing[k]);
    await setInboxTiming(this.inbox.id, {
      idle_warn_minutes: norm('idle_warn_minutes'),
      idle_close_grace_minutes: norm('idle_close_grace_minutes'),
      idle_close_out_of_hours_grace_minutes: norm('idle_close_out_of_hours_grace_minutes'),
      confirm_grace_minutes: norm('confirm_grace_minutes'),
    });
    this.$store.dispatch('...'); // or useAlert — match the component's toast convention
  } catch (e) {
    // surface via the component's existing error/alert convention
  } finally {
    this.timingSaving = false;
  }
}
```

- [ ] **Step 4: Generate the patch file**

Produce the unified diff against the extracted upstream sources and save it as `deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch` (the Dockerfile globs `patches/*.patch`, so it is auto-included). Verify it applies cleanly:

```bash
# in a throwaway checkout of chatwoot v4.15.1 SPA sources
git apply --check deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
```

- [ ] **Step 5: Manual verification (build + smoke)**

Build the custom image off-VM for amd64 (per root `CLAUDE.md`) and deploy, then verify end-to-end:

1. Settings → Inboxes → (a WhatsApp inbox) shows the "Inactivity & auto-close" card.
2. Set `Warn after idle = 2`, Save → reload → value persists (confirms `GET`/`PUT`).
3. `curl -s -H "x-api-key: $KEY" $BACKEND/kb/inboxes/<id>/timing` returns `idle_warn_minutes: 2`.
4. With the agent lifecycle scan enabled, a conversation idle >2 min in that inbox warns early (vs the 10-min default), proving the agent read path.
5. Clear the field → Save → `GET` returns all-null and the 10-min default resumes.

- [ ] **Step 6: Commit**

```bash
git add deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
git commit -m "feat(chatwoot-fork): inbox-settings inactivity & auto-close timing card"
```

---

## Self-Review

**Spec coverage:**
- Storage (`InboxTimingStore`, `inbox_timing` collection, None/absent semantics, separate from assignments) → Task 1. ✓
- API (`GET`/`PUT …/timing`, full-replace, 0..1440 validation, list-row embedding) → Task 2. ✓
- Bootstrap wiring → Task 2 Step 3b. ✓
- Agent read (`get_assistant_lifecycle_timing`, cached `/kb/inboxes`, no extra HTTP) → Task 3. ✓
- Agent apply (`_fetch_lifecycle_timing`, scanner override, precedence, fail-open) → Task 4. ✓
- UI (native Settings → Inboxes card, empty=inherit, saves to backend, v4.15.1 research step) → Task 5. ✓
- Non-goals honored: no Ruby patches, no new env vars, business hours untouched, `assigned_idle_resolve_minutes` not exposed. ✓

**Placeholder scan:** No TBD/TODO/"add error handling"; every code step has concrete content. Task 5's SPA specifics are intentionally research-gated (SPA not in checkout) but ship concrete helper + card code and an exact extraction procedure. ✓

**Type consistency:** `TIMING_KEYS` / `_LIFECYCLE_TIMING_KEYS` hold the same four names; `get`/`set`/`get_all`/`delete` signatures match across store, router, client, and scanner; `_normalize_timing` and the client both return `{key: int | None}`; the scanner `_pick` and the store both guard `isinstance(v, int) and not isinstance(v, bool)`. ✓
