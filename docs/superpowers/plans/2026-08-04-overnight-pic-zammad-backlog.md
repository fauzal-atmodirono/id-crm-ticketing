# Overnight Build Implementation Plan: PIC/Dealer, Zammad Removal, Backlog

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This run is explicitly user-authorized for fully autonomous execution — do not pause for human checkpoints; the controller (you) makes every call that would normally go to the human, and documents the reasoning in the final report.

**Goal:** Ship 5 independent tracks in one autonomous overnight run: a
PIC/Dealer escalation-routing admin UI+backend, full Zammad removal
(code+infra, all tenants), bulk CSV FAQ upload, a round-robin ticket cap
per agent, and a foundational (phone/vehicle-number-keyed) Customer 360
lookup.

**Architecture:** Track 1 establishes an async-store-backed `PicRegistry`
pattern (mirroring the existing `ChannelPriorityStore`). Track 2 builds on
Track 1's already-async PIC code to strip Zammad out cleanly — **Track 2
MUST run after Track 1 completes**, both tasks touch
`adapters/chatwoot.py`'s PIC-resolution code and sequencing avoids rework.
Tracks 3-5 are independent of 1/2 and of each other — build in any order,
in parallel conceptually (though the SDD process still runs one
implementer at a time per its own rules).

**Tech Stack:** Python/FastAPI (`backend/apps/backend/`), Python/FastAPI
(`agent/`), pytest, Vue 3 (Chatwoot fork patches), Firestore.

## Global Constraints

- Every new capability defaults to its documented off/empty state —
  byte-identical to today's behavior when unconfigured.
- New env vars go in both the consuming `config.py` and the relevant
  `example.env`.
- Background-task/webhook code never raises for "nothing to do" cases.
- New admin routers/pages follow the EXACT existing pattern:
  `require_permission()` dependency (see `sla_policy_router.py` for the
  reference shape), a `PERMISSION_REGISTRY` entry in
  `authz/seed.py`, mounted inside `main.py`'s
  `if settings.rbac_enabled and settings.rbac_database_url:` block
  alongside `sla_policy_router`/`audit_router`, and a standalone
  RBAC-gated sidebar icon on the frontend (same pattern as SLA Policies /
  Audit Log / Roles & Permissions).
- Run the full relevant test suite before each commit:
  `cd agent && source .venv/bin/activate && pytest` for agent-side changes,
  `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`
  for backend-side changes.
- For Chatwoot fork patch tasks: author in the clone at
  `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot`, export via
  `git diff`, verify via a full local Docker build of
  `deploy/chatwoot-fork/` (all patches applying + compiling), following the
  exact workflow already used for patches 0031-0038 this session (commit
  the "post-previous-patch" baseline as a temporary local commit before
  diffing your new patch if the clone's HEAD doesn't already reflect prior
  patches from this run — `git reset --soft HEAD~1` afterward to undo the
  temp commit, keeping the working tree).
- Commit after each task (one commit per task). This is a long unattended
  run — frequent commits are the recovery points.
- **No user checkpoints.** If a task's implementer would normally ask a
  question requiring human judgment, the controller answers it directly
  using the spec's stated design and this plan's explicit instructions;
  never block waiting for a response that won't come tonight.

---

## Track 1: PIC/Dealer escalation-routing admin UI + backend

### Task 1: Firestore-backed PicStore + DealerStore

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/pic_store.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_pic_store.py`

**Interfaces:**
- Produces: `PicRecord` dataclass (`department: str, pic_name: str, pic_email: str, pic_whatsapp: str, cc_emails: list[str]`), `DealerRecord` dataclass (`dealer: str, email: str`), `PicStore` and `DealerStore` classes each with `async def get(key: str) -> Record | None`, `async def set(key: str, **fields) -> None`, `async def delete(key: str) -> None`, `async def list_all() -> list[Record]`.

- [ ] **Step 1: Read the reference implementation completely**

Read `backend/apps/backend/src/chatbot/features/routing/store.py` in full
(it's short, ~112 lines) — `ChannelPriorityStore` is the exact pattern to
mirror: Firestore client per-call (not cached), `asyncio.to_thread` for
every I/O call, try/except around each method logging on `_log.error` and
returning a safe default (`None`/`[]`/no-op), one document per key in a
named collection.

- [ ] **Step 2: Write the failing tests**

Mirror `backend/apps/backend/src/chatbot/features/chat/test_routing_store.py`
(read it first for the exact mocking convention — likely a fake Firestore
client/document reference). Write tests for `PicStore`: get on empty store
returns `None`; set then get round-trips all fields; delete removes it;
list_all returns all stored records; a Firestore exception on any method
is swallowed and returns the safe default (verify via a fake client that
raises). Mirror the same test shapes for `DealerStore`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/chat/test_pic_store.py -v`
Expected: FAIL (module doesn't exist yet).

- [ ] **Step 4: Implement `pic_store.py`**

```python
"""Firestore-backed stores for PIC (department -> escalation contact) and
dealer (dealer slug -> email) routing config, editable via the Escalation
Routing admin page. Mirrors routing/store.py's ChannelPriorityStore pattern
exactly: one document per key, asyncio.to_thread for I/O, fail-open.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_PIC_COLLECTION = "escalation_pics"
_DEALER_COLLECTION = "escalation_dealers"


@dataclass(frozen=True)
class PicRecord:
    department: str
    pic_name: str
    pic_email: str
    pic_whatsapp: str
    cc_emails: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DealerRecord:
    dealer: str
    email: str


class PicStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self, department: str) -> firestore.DocumentReference:
        return self._client().collection(_PIC_COLLECTION).document(department.lower())

    async def get(self, department: str) -> PicRecord | None:
        try:
            snap = await asyncio.to_thread(self._doc_ref(department).get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return PicRecord(
                department=str(data.get("department", department)),
                pic_name=str(data.get("pic_name", "")),
                pic_email=str(data.get("pic_email", "")),
                pic_whatsapp=str(data.get("pic_whatsapp", "")),
                cc_emails=list(data.get("cc_emails") or []),
            )
        except Exception as e:
            _log.error("pic_store_get_failed", department=department, error=str(e))
            return None

    async def set(
        self,
        department: str,
        pic_name: str,
        pic_email: str,
        pic_whatsapp: str,
        cc_emails: list[str] | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._doc_ref(department).set,
                {
                    "department": department,
                    "pic_name": pic_name,
                    "pic_email": pic_email,
                    "pic_whatsapp": pic_whatsapp,
                    "cc_emails": cc_emails or [],
                },
            )
        except Exception as e:
            _log.error("pic_store_set_failed", department=department, error=str(e))

    async def delete(self, department: str) -> None:
        try:
            await asyncio.to_thread(self._doc_ref(department).delete)
        except Exception as e:
            _log.error("pic_store_delete_failed", department=department, error=str(e))

    async def list_all(self) -> list[PicRecord]:
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_PIC_COLLECTION).stream())
            )
            results: list[PicRecord] = []
            for snap in snaps:
                data = snap.to_dict() or {}
                department = data.get("department")
                if department is None:
                    continue
                results.append(
                    PicRecord(
                        department=str(department),
                        pic_name=str(data.get("pic_name", "")),
                        pic_email=str(data.get("pic_email", "")),
                        pic_whatsapp=str(data.get("pic_whatsapp", "")),
                        cc_emails=list(data.get("cc_emails") or []),
                    )
                )
            return results
        except Exception as e:
            _log.error("pic_store_list_failed", error=str(e))
            return []


class DealerStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self, dealer: str) -> firestore.DocumentReference:
        return self._client().collection(_DEALER_COLLECTION).document(dealer.lower())

    async def get(self, dealer: str) -> DealerRecord | None:
        try:
            snap = await asyncio.to_thread(self._doc_ref(dealer).get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return DealerRecord(dealer=str(data.get("dealer", dealer)), email=str(data.get("email", "")))
        except Exception as e:
            _log.error("dealer_store_get_failed", dealer=dealer, error=str(e))
            return None

    async def set(self, dealer: str, email: str) -> None:
        try:
            await asyncio.to_thread(self._doc_ref(dealer).set, {"dealer": dealer, "email": email})
        except Exception as e:
            _log.error("dealer_store_set_failed", dealer=dealer, error=str(e))

    async def delete(self, dealer: str) -> None:
        try:
            await asyncio.to_thread(self._doc_ref(dealer).delete)
        except Exception as e:
            _log.error("dealer_store_delete_failed", dealer=dealer, error=str(e))

    async def list_all(self) -> list[DealerRecord]:
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_DEALER_COLLECTION).stream())
            )
            results: list[DealerRecord] = []
            for snap in snaps:
                data = snap.to_dict() or {}
                dealer = data.get("dealer")
                if dealer is None:
                    continue
                results.append(DealerRecord(dealer=str(dealer), email=str(data.get("email", ""))))
            return results
        except Exception as e:
            _log.error("dealer_store_list_failed", error=str(e))
            return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/chat/test_pic_store.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/pic_store.py \
        backend/apps/backend/src/chatbot/features/chat/test_pic_store.py
git commit -m "feat(escalation): add PicStore + DealerStore (Firestore-backed)"
```

---

### Task 2: Make `PicRegistry.lookup()` async, store-first with env-var fallback

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/pic_registry.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py` (dealer lookup gets the same treatment)
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py` (await the now-async lookup calls)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_pic_registry.py`, `test_escalation_notifier.py`

**Interfaces:**
- Consumes: `PicStore`, `DealerStore` from Task 1.
- Produces: `PicRegistry.lookup(department: str) -> PicEntry | None` (now `async`); `EscalationNotifier`'s dealer resolution becomes store-first too.

- [ ] **Step 1: Read the current files completely**

Read `pic_registry.py` in full (short — `PicEntry`, `PicRegistry`,
`build_pic_registry`). Read `escalation_notifier.py`'s
`build_dealer_email_map` and `_send_dealer_forward`. Read every call site
of `PicRegistry.lookup(` and confirm each is inside an `async def` (grep
`adapters/chatwoot.py` and `escalation_notifier.py` — both call sites were
verified async during planning; re-verify against the actual current file
since other tasks may have touched it).

- [ ] **Step 2: Write the failing tests**

Add to `test_pic_registry.py`: `lookup()` returns the store's record when
present (mock `PicStore.get` to return a `PicRecord`); falls back to the
legacy env-var-parsed table when the store has no entry for that
department; returns `None` when neither has an entry. Add to
`test_escalation_notifier.py`: dealer resolution checks `DealerStore.get`
first, falls back to `dealer_email_map_json`-parsed dict when the store
has no entry.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/chat/test_pic_registry.py src/chatbot/features/chat/test_escalation_notifier.py -v`

- [ ] **Step 4: Implement**

In `pic_registry.py`, change `PicRegistry.__init__` to also accept an
optional `store: PicStore | None = None`, and make `lookup` async:

```python
class PicRegistry:
    def __init__(self, table: dict[str, PicEntry], store: PicStore | None = None) -> None:
        self._table = table
        self._store = store

    async def lookup(self, department: str) -> PicEntry | None:
        """Return the PicEntry for *department* (case-insensitive) or None.

        Store-first: checks the Firestore-backed PicStore (the operator-
        editable source of truth) before falling back to the legacy
        PIC_MAP_JSON-parsed table, so a tenant that never touches the new
        admin UI keeps working exactly as before.
        """
        key = department.lower()
        if self._store is not None:
            record = await self._store.get(key)
            if record is not None:
                return PicEntry(
                    pic_name=record.pic_name,
                    pic_email=record.pic_email,
                    pic_whatsapp=record.pic_whatsapp,
                    zammad_group="",
                    cc_emails=record.cc_emails,
                )
        return self._table.get(key)
```

(`zammad_group=""` is a deliberate placeholder — Track 2 removes the field
from `PicEntry` entirely; if Track 2 hasn't landed yet when this task
runs, keep the field but never populate it from the new store, since the
whole point of the store is to not carry Zammad concepts.)

Update `build_pic_registry(settings: Settings, store: PicStore | None = None) -> PicRegistry` to pass `store` through to the constructor.

Update every call site of `.lookup(` to `await` it — in
`chatwoot.py`'s `_pic_label` and `_fire_escalation`, and anywhere else grep
finds. Each of those functions must already be `async def` (verified during
planning); if a call site turns out to be in a sync function, that's a
signal the plan's assumption was wrong — escalate this in the task report
as a concern rather than guessing at a workaround.

In `escalation_notifier.py`, add a `DealerStore | None` param to
`EscalationNotifier.__init__` (alongside the existing `dealer_email_map`
dict param — keep both; store-first, dict fallback, same pattern), and in
`_send_dealer_forward`, check the store first:

```python
    async def _send_dealer_forward(self, dealer_slug: str, *, conv_id: str, title: str, body: str) -> None:
        email = None
        if self._dealer_store is not None:
            record = await self._dealer_store.get(dealer_slug.lower())
            if record is not None:
                email = record.email
        if not email:
            email = self._dealer_email_map.get(dealer_slug.lower())
        if not email:
            _log.info("escalation_dealer_unmapped", dealer=dealer_slug)
            return
        # ... rest unchanged
```

Note this changes `_send_dealer_forward` to `async def` if it wasn't
already (check the current signature — it very likely already is, since
it's called with `await` from `notify_email_channel_escalation`).

- [ ] **Step 5: Run tests to verify they pass, then the full backend suite**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`
Expected: PASS, no regressions (this is a signature-breaking change to
`lookup()` — the full suite run is the regression check that every caller
was updated).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/pic_registry.py \
        backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py \
        backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py \
        backend/apps/backend/src/chatbot/features/chat/test_pic_registry.py \
        backend/apps/backend/src/chatbot/features/chat/test_escalation_notifier.py
git commit -m "feat(escalation): PicRegistry/dealer lookup store-first, env-var fallback"
```

---

### Task 3: Admin router + RBAC permission + main.py wiring

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/pic_admin_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_pic_admin_router.py`
- Modify: `backend/apps/backend/src/chatbot/features/authz/seed.py`
- Modify: `backend/apps/backend/src/chatbot/main.py`

**Interfaces:**
- Consumes: `PicStore`, `DealerStore` (Task 1); `require_permission` from `chatbot.features.authz.deps`.
- Produces: `build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings) -> APIRouter`.

- [ ] **Step 1: Read the reference router completely**

Read `backend/apps/backend/src/chatbot/features/chat/sla_policy_router.py`
in full (already read during planning — re-read to confirm nothing
changed). This is the exact shape to mirror: `require_permission("...",
repo=authz_repo, validator=validator, settings=settings)`, `prefix="/admin/..."`,
`dependencies=[Depends(the_permission_dep)]` per route.

- [ ] **Step 2: Write the failing tests**

Mirror `test_sla_policy_router.py`'s structure (read it for the exact
FastAPI `TestClient` + mocked `authz_repo`/`validator` fixture pattern).
Tests: unauthenticated request 403/401s (match whatever `require_permission`
actually returns — verify from `authz/deps.py`); authenticated with the
`escalation.manage` permission can list/upsert/delete PIC and dealer
entries; upsert with missing required fields 422s (pydantic validation).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/chat/test_pic_admin_router.py -v`

- [ ] **Step 4: Implement the router**

```python
"""Escalation Routing admin API -- CRUD for PIC (department -> contact) and
dealer (slug -> email) mappings, backing the Escalation Routing admin page.
Gated behind the `escalation.manage` permission via Phase 1's
`require_permission`, matching sla_policy_router.py's pattern exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.pic_store import DealerStore, PicStore
    from chatbot.platform.config import Settings


class PicUpsertBody(BaseModel):
    pic_name: str = Field(min_length=1)
    pic_email: str = Field(min_length=1)
    pic_whatsapp: str = ""
    cc_emails: list[str] = Field(default_factory=list)


class DealerUpsertBody(BaseModel):
    email: str = Field(min_length=1)


def build_pic_admin_router(
    pic_store: PicStore,
    dealer_store: DealerStore,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/escalation", tags=["escalation-admin"])
    manage_escalation = require_permission(
        "escalation.manage", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/pics", dependencies=[Depends(manage_escalation)])
    async def list_pics() -> dict:
        records = await pic_store.list_all()
        return {"pics": [r.__dict__ for r in records]}

    @router.put("/pics/{department}", dependencies=[Depends(manage_escalation)])
    async def upsert_pic(department: str, body: PicUpsertBody) -> dict:
        await pic_store.set(
            department,
            pic_name=body.pic_name,
            pic_email=body.pic_email,
            pic_whatsapp=body.pic_whatsapp,
            cc_emails=body.cc_emails,
        )
        return {"department": department, "status": "ok"}

    @router.delete("/pics/{department}", dependencies=[Depends(manage_escalation)])
    async def delete_pic(department: str) -> dict:
        await pic_store.delete(department)
        return {"department": department, "status": "ok"}

    @router.get("/dealers", dependencies=[Depends(manage_escalation)])
    async def list_dealers() -> dict:
        records = await dealer_store.list_all()
        return {"dealers": [r.__dict__ for r in records]}

    @router.put("/dealers/{dealer}", dependencies=[Depends(manage_escalation)])
    async def upsert_dealer(dealer: str, body: DealerUpsertBody) -> dict:
        await dealer_store.set(dealer, email=body.email)
        return {"dealer": dealer, "status": "ok"}

    @router.delete("/dealers/{dealer}", dependencies=[Depends(manage_escalation)])
    async def delete_dealer(dealer: str) -> dict:
        await dealer_store.delete(dealer)
        return {"dealer": dealer, "status": "ok"}

    return router
```

- [ ] **Step 5: Register the permission**

In `authz/seed.py`, add to `PERMISSION_REGISTRY`:
```python
    "escalation.manage": "Manage PIC/dealer escalation routing",
```

- [ ] **Step 6: Wire into `main.py`**

Inside the `if settings.rbac_enabled and settings.rbac_database_url:` block
(after the `sla_policy_router` mount, ~line 542), add:

```python
        from chatbot.features.chat.pic_admin_router import build_pic_admin_router
        from chatbot.features.chat.pic_store import DealerStore, PicStore

        pic_store = PicStore(settings)
        dealer_store = DealerStore(settings)
        app.include_router(
            build_pic_admin_router(pic_store, dealer_store, authz_repo, authz_validator, settings)
        )
```

Then thread `pic_store` into the existing `build_pic_registry(settings)`
call (line ~293) and `dealer_store` into the `EscalationNotifier(...)`
construction — **both of those constructions happen BEFORE this RBAC
block in the current file layout**, so `pic_store`/`dealer_store` must be
built unconditionally earlier (RBAC gates only the ADMIN UI/router, not
whether the store itself exists — the store must always exist so
`PicRegistry`/`EscalationNotifier` can read from it even when RBAC is off).
Move the `PicStore(settings)`/`DealerStore(settings)` construction up to
right before `pic_registry = build_pic_registry(settings)`, pass
`pic_store` into that call, pass `dealer_store` into the
`EscalationNotifier(...)` construction, and REUSE those same two
instances inside the RBAC block above (don't construct new ones there).

- [ ] **Step 7: Run tests, then the full backend suite**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`

- [ ] **Step 8: Document env vars if any new ones were needed**

This task shouldn't need new env vars (reuses `firestore_project_id`/
`firestore_database_id` already in `Settings`) — if the implementation
needed one, document it in `backend/apps/backend/.env.example` per the
global constraint.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/pic_admin_router.py \
        backend/apps/backend/src/chatbot/features/chat/test_pic_admin_router.py \
        backend/apps/backend/src/chatbot/features/authz/seed.py \
        backend/apps/backend/src/chatbot/main.py
git commit -m "feat(escalation): add /admin/escalation PIC+dealer CRUD router"
```

---

### Task 4: Chatwoot fork patch — Escalation Routing admin page

**Files:**
- Author in the clone: `app/javascript/dashboard/views/ProtonEscalationRoutingPage.vue` (new), `app/javascript/dashboard/api/protonAdmin.js` (extend), `app/javascript/dashboard/components-next/sidebar/Sidebar.vue` (nav entry), `app/javascript/dashboard/routes/dashboard/dashboard.routes.js` (route)
- Create: `deploy/chatwoot-fork/patches/0039-escalation-routing-admin.patch`

**Interfaces:** none (self-contained frontend feature).

**Background:** read `deploy/chatwoot-fork/patches/0025-sla-policies-admin.patch`
and `0027-roles-permissions-admin.patch` in FULL before writing anything —
these are the two closest references for "new standalone RBAC-gated admin
page" (route registration, sidebar icon, `protonHasPermission()` gating,
`adminRequest()` usage). Match their structure exactly; don't invent a
different pattern.

- [ ] **Step 1: Confirm the clone's starting state**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git status
git log --oneline -3
```
Expect a clean tree. If patches 0037/0038 (from earlier this session) were
authored in this same clone and left uncommitted, that's fine — this task
only touches NEW files, so it won't conflict; just don't `git checkout --`
anything that isn't yours.

- [ ] **Step 2: Author the page**

Create `ProtonEscalationRoutingPage.vue`: two sections (PIC table, Dealer
table), each with a simple add/edit/delete form, calling
`listPics/upsertPic/deletePic/listDealers/upsertDealer/deleteDealer` (new
functions to add to `protonAdmin.js`, following the exact shape of the
existing SLA/roles functions in that file — `adminRequest('/admin/escalation/pics')`
etc.). Gate rendering behind `protonHasPermission('escalation.manage')`,
same as the SLA Policies page gates on `sla.manage`.

- [ ] **Step 3: Register the route and sidebar icon**

In `dashboard.routes.js`, add a route (path
`accounts/:accountId/proton/escalation-routing`, name
`proton_escalation_routing`, `meta: { permissions: ['administrator'] }`
matching the sibling admin routes' meta shape). In `Sidebar.vue`, add a
standalone icon entry (same tier as SLA Policies/Audit Log/Roles &
Permissions — read how those three register themselves in the sidebar
computed/menu list and add a fourth following the identical shape,
including the `protonHasPermission` gate).

- [ ] **Step 4: Local build check**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
docker build --target builder . 2>&1 | tail -40
```
Expected: builder stage completes without a Vite/SFC compile error.

- [ ] **Step 5: Export the patch**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git diff -- app/javascript/dashboard/api/protonAdmin.js \
             app/javascript/dashboard/components-next/sidebar/Sidebar.vue \
             app/javascript/dashboard/routes/dashboard/dashboard.routes.js \
  > /tmp/escalation-routing-existing-files.patch
git diff --no-index /dev/null app/javascript/dashboard/views/ProtonEscalationRoutingPage.vue \
  > /tmp/escalation-routing-new-file.patch || true
cat /tmp/escalation-routing-existing-files.patch /tmp/escalation-routing-new-file.patch \
  > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0039-escalation-routing-admin.patch
```
(The `--no-index` diff against `/dev/null` is the standard way to export a
brand-new untracked file as a patch; `git diff --no-index` exits 1 when
there's a difference, which is expected here — the `|| true` prevents that
from failing the step.)

- [ ] **Step 6: Verify against the full stack**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork
docker build --build-arg UPSTREAM_VERSION=v4.15.1 --build-arg PROTON_BUILD_SHA=pending-verify -t proton-chatwoot:v4.15.1-custom .
```
Expected: all patches (through 0039) apply and the image builds with 0
errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0039-escalation-routing-admin.patch
git commit -m "feat(chatwoot-fork): Escalation Routing admin page (PIC + dealer config)"
```

---

## Track 2: Drop Zammad entirely

**MUST run after Track 1 completes** (both touch the same PIC-resolution
code in `adapters/chatwoot.py`).

### Task 5: Remove Zammad from `agent/`

**Files:**
- Delete: `agent/app/clients/zammad.py`, `agent/app/routers/zammad.py`, `agent/app/services/responder.py`
- Modify: `agent/app/services/sync.py`, `agent/app/config.py`, `agent/app/clients/deps.py`, `agent/app/main.py`
- Delete: any `agent/tests/test_zammad_*.py`; trim Zammad-path tests from `test_sync_escalation.py`

- [ ] **Step 1: Inventory every Zammad reference**

```bash
cd agent
grep -rln "zammad\|Zammad\|ZAMMAD" app/ tests/ | grep -v __pycache__
```
This is the authoritative list of files to touch. Read each one before
editing — do not delete a file without confirming what non-Zammad code (if
any) shares it.

- [ ] **Step 2: Remove Zammad client/router/service files**

Delete `agent/app/clients/zammad.py`, `agent/app/routers/zammad.py`,
`agent/app/services/responder.py` (the Zammad draft-reply flow — confirm
via CLAUDE.md's own description "the Zammad draft-reply flow, called from
on_ticket_event" before deleting, to make sure nothing else imports from
this file for a non-Zammad reason).

- [ ] **Step 3: Strip Zammad from `sync.py`**

Remove `escalate_conversation`, `_ensure_zammad_customer`, and the
Zammad-ticket-creation branch inside `maybe_escalate` (everything gated by
`if not get_settings().zammad_ticketing_enabled: ... return` and below it
up to the ticket-creation call — since Zammad no longer exists, delete the
whole conditional AND the code it was guarding, not just the guard). KEEP
`_maybe_notify_email_escalation` and its call inside `maybe_escalate`
completely intact — that's this session's Chatwoot-only email-escalation
path, unrelated to Zammad. `maybe_escalate` after this change should just
be: check for the `escalate` label, call `_maybe_notify_email_escalation`,
done — no more Zammad branch at all. `maybe_stamp_dealer_escalation` is
also untouched (it's about the `dealer_` label for reporting, unrelated to
Zammad).

- [ ] **Step 4: Strip Zammad from `config.py` and `deps.py`**

Remove every `zammad_*` field from `Settings` in `config.py`, including
`zammad_ticketing_enabled` (grep confirmed no other flag reuses this name).
Remove `get_zammad_client` from `deps.py`.

- [ ] **Step 5: Strip Zammad from `main.py`**

Remove the Zammad router mount and any Zammad-specific wiring the Step 1
inventory turned up.

- [ ] **Step 6: Remove/trim tests**

Delete any `test_zammad_*.py` files. In `test_sync_escalation.py`, remove
every test asserting Zammad-ticket-creation behavior (they test code that
no longer exists) — KEEP every test added this session for
`_maybe_notify_email_escalation`/the email-channel escalation path, those
are unrelated to Zammad and must still pass.

- [ ] **Step 7: Run the full agent suite**

Run: `cd agent && source .venv/bin/activate && pytest`
Expected: PASS. Then confirm zero Zammad references remain:
```bash
grep -ril "zammad" app/ tests/ | grep -v __pycache__
```
Expected: no output.

- [ ] **Step 8: Update `deploy/tenants/example.env`**

Remove any `ZAMMAD_*` lines that were agent-visible config.

- [ ] **Step 9: Commit**

```bash
git add -u agent/ deploy/tenants/example.env
git status  # confirm only agent/ and the one deploy file changed
git commit -m "refactor(agent): remove Zammad entirely (code + config)"
```

---

### Task 6: Remove Zammad from `backend/apps/backend/`

**Files:**
- Delete: `backend/apps/backend/src/chatbot/features/chat/adapters/zammad.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`, `backend/apps/backend/src/chatbot/main.py`, `backend/apps/backend/src/chatbot/platform/config.py`
- Delete: Zammad-specific test files

- [ ] **Step 1: Inventory every Zammad reference**

```bash
cd backend/apps/backend
grep -rln "zammad\|Zammad\|ZAMMAD" src/ | grep -v __pycache__
```

- [ ] **Step 2: Strip Zammad from `adapters/chatwoot.py`**

Remove the `zammad: ZammadClient | None = None` constructor param and
`self._zammad`. Remove `_direct_zammad_active()` entirely. Simplify
`_complaint_labels()`: it currently returns `[]` when
`_direct_zammad_active()` is true, else applies the complaint label —
since Zammad no longer exists, this always applies the label now
(delete the `if self._direct_zammad_active(): return []` guard, keep the
rest of the method body unchanged). In `_fire_escalation`, delete the
entire "Create the back-office Zammad ticket ONLY when direct ticketing is
on" block (the `if self._direct_zammad_active() and self._zammad is not
None:` branch and everything inside it) — this also means
`zammad_ticket_number` is always `None` now; simplify the trailing
`self._escalation_notifier.notify(...)` call to drop that now-always-None
kwarg if it's cleaner to do so, but only if `EscalationNotifier.notify`'s
signature still makes `zammad_ticket_number` optional (check — it likely
already defaults to `None`, so passing nothing is equivalent to passing
`None` explicitly; either is fine, prefer removing the now-dead kwarg for
clarity).

- [ ] **Step 3: Strip Zammad from `main.py` and `config.py`**

Remove Zammad client construction/wiring in `main.py`. Remove all
`zammad_*` fields from `Settings` in `config.py`, including
`zammad_direct_ticketing`.

- [ ] **Step 4: Delete the adapter and its tests**

Delete `adapters/zammad.py` and any co-located `test_zammad*.py`. Trim any
Zammad-specific assertions out of `test_chatwoot_wiring.py` or similar
integration tests the Step 1 inventory surfaces (keep everything else in
those files intact).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`
Expected: PASS. Then confirm zero Zammad references remain:
```bash
grep -ril "zammad" src/ | grep -v __pycache__
```

- [ ] **Step 6: Update `.env.example`**

Remove any `ZAMMAD_*` lines from `backend/apps/backend/.env.example`.

- [ ] **Step 7: Commit**

```bash
git add -u backend/apps/backend/
git status
git commit -m "refactor(backend): remove Zammad entirely (code + config)"
```

---

### Task 7: Remove Zammad from deploy infra + live tenants

**Files:**
- Modify: `deploy/docker-compose.tenant.yml`, `deploy/scripts/add-tenant.sh`
- Live infra: `default` and `wahchan` tenants on the VM

- [ ] **Step 1: Remove the 5 zammad-* services from the compose file**

In `deploy/docker-compose.tenant.yml`, delete the `zammad-init`,
`zammad-railsserver`, `zammad-scheduler`, `zammad-websocket`,
`zammad-nginx` service definitions in full (read the whole file first to
find their exact boundaries — service blocks in compose YAML are delimited
by top-level keys under `services:`).

- [ ] **Step 2: Remove Zammad provisioning from `add-tenant.sh`**

Remove the `CREATE ROLE zammad_${TENANT}` / `CREATE DATABASE
zammad_${TENANT}` lines and any Zammad env var templating in the tenant
`.env` generation section.

- [ ] **Step 3: Run local verification**

```bash
docker compose -f deploy/docker-compose.tenant.yml config > /dev/null
```
Expected: valid YAML, no parse errors (confirms the removed blocks didn't
leave the file malformed).

- [ ] **Step 4: Commit the deploy-file changes**

```bash
git add deploy/docker-compose.tenant.yml deploy/scripts/add-tenant.sh
git commit -m "chore(deploy): remove zammad-* service definitions and provisioning"
```

- [ ] **Step 5: Stop and remove live Zammad containers on `default` and `wahchan`**

This step touches live infrastructure — run it directly (the user has
explicitly authorized this for tonight's run), but log exactly what you
do:

```bash
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
sudo docker compose -p default rm -sf zammad-init zammad-railsserver zammad-scheduler zammad-websocket zammad-nginx
sudo docker compose -p wahchan rm -sf zammad-init zammad-railsserver zammad-scheduler zammad-websocket zammad-nginx
sudo docker ps --format "{{.Names}}" | grep -i zammad || echo "no zammad containers remain"
'
```

**Preserve, not purge:** do NOT run any `docker volume rm` or `DROP
DATABASE` command against `zammad_default`/`zammad_wahchan` or their
storage volumes — leave that data in place, same as how proton's Zammad
removal was handled 2026-07-26. This is deliberate and matches the spec.

- [ ] **Step 6: Note in the final report**

Record in your task report exactly which containers were removed and
confirm (via the `docker ps | grep zammad` check above) that no zammad-*
containers remain running on any of the 3 tenants.

---

## Track 3: Bulk CSV upload for FAQ Q&A pairs

### Task 8: Backend `POST /kb/faq/bulk`

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/faq_admin_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_faq_admin_router.py`

**Interfaces:**
- Consumes: existing `LiveFaqPort.create()`.
- Produces: `POST /kb/faq/bulk` — multipart file upload, returns `{"created": int, "errors": [{"row": int, "reason": str}]}`.

- [ ] **Step 1: Read the current router and test file completely**

Both already read during planning — `faq_admin_router.py` is short (120
lines). Re-read `test_faq_admin_router.py` for the exact `TestClient` +
mocked-store fixture pattern used for the existing `create_faq` test, to
match it for the new bulk endpoint's tests.

- [ ] **Step 2: Write the failing tests**

Tests: a valid 2-row CSV (with a header row `question,answer,keywords,tags`)
creates 2 entries and returns `{"created": 2, "errors": []}`; a row missing
`question` or `answer` is skipped and reported in `errors` while the
other valid rows still succeed; an oversized file (over the reused
`KB_MAX_UPLOAD_BYTES` cap — check the exact constant name/value in
`kb_knowledge_router.py`, reuse it, don't hardcode a new number) returns
413; missing/wrong `x-api-key` 401s, matching the existing FAQ endpoints'
auth test pattern exactly.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/chat/test_faq_admin_router.py -k bulk -v`

- [ ] **Step 4: Implement**

Add to `faq_admin_router.py` (needs `UploadFile`/`File` from `fastapi`,
`csv` and `io` from stdlib):

```python
import csv
import io

from fastapi import File, UploadFile

# ... inside build_faq_admin_router, alongside the other routes:

    @router.post("/kb/faq/bulk")
    async def bulk_create_faq(
        file: UploadFile = File(...),
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        raw = await file.read()
        if len(raw) > settings.kb_max_upload_bytes:
            raise HTTPException(status_code=413, detail="File too large")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="File must be UTF-8 encoded CSV") from exc

        reader = csv.DictReader(io.StringIO(text))
        created = 0
        errors: list[dict[str, Any]] = []
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()
            if not question or not answer:
                errors.append({"row": i, "reason": "question and answer are required"})
                continue
            keywords = [k.strip() for k in (row.get("keywords") or "").split(";") if k.strip()]
            tags = [t.strip() for t in (row.get("tags") or "").split(";") if t.strip()]
            try:
                await _require_store().create(
                    LiveFaqEntry(
                        id="", question=question, answer=answer,
                        keywords=keywords, tags=tags, active=True,
                    )
                )
                created += 1
            except Exception as exc:
                errors.append({"row": i, "reason": str(exc)})
        return {"created": created, "errors": errors}
```

Check the exact attribute name for the reused upload-size setting
(`settings.kb_max_upload_bytes` is a guess based on the env var name
`KB_MAX_UPLOAD_BYTES` — verify the actual `Settings` field name in
`config.py` before using it).

- [ ] **Step 5: Run tests to verify they pass, then the full backend suite**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/faq_admin_router.py \
        backend/apps/backend/src/chatbot/features/chat/test_faq_admin_router.py
git commit -m "feat(kb): add POST /kb/faq/bulk CSV upload endpoint"
```

---

### Task 9: Chatwoot fork patch — Bulk upload button on FAQs page

**Files:**
- Author in the clone: `app/javascript/dashboard/components/proton/KnowledgeFaqs.vue` (existing file, extend), `app/javascript/dashboard/api/protonKnowledge.js` (extend)
- Create: `deploy/chatwoot-fork/patches/0040-faq-bulk-csv-upload.patch`

- [ ] **Step 1: Read the existing FAQs page completely**

Read `KnowledgeFaqs.vue` in full via the patches that created/modified it
(`0010-knowledge-faqs-native.patch` and any later FAQ-related patches) —
reconstruct its current state the same way prior tasks this session have
(the clone may or may not already reflect it; check `git log`/`git status`
in the clone first). If the clone's copy is stale relative to the repo's
patch history for this file, reconstruct it by applying the relevant prior
patches to the clone before making new edits — do not guess at its current
structure.

- [ ] **Step 2: Add the bulk-upload button + dialog**

Add an "Bulk upload (CSV)" button next to the existing "+ New entry"
button. On click, open a simple file-picker; on file selection, POST to
`/kb/faq/bulk` via a new `bulkUploadFaqs(file)` function in
`protonKnowledge.js` (multipart upload, matching the existing dedicated
multipart-fetch helper pattern already used for KB document uploads — grep
`protonKnowledge.js` for `uploadKnowledgeFile` and mirror its shape, NOT
the generic JSON `kbRequest` helper). Show the response's `created`/`errors`
counts via `useAlert`, and refresh the FAQ list on success.

- [ ] **Step 3: Local build check**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
docker build --target builder . 2>&1 | tail -40
```

- [ ] **Step 4: Export the patch, verify against the full stack, commit**

Same export/verify/commit shape as Task 4, Steps 5-7 — export via `git
diff` against the correct baseline (the clone's state before this task's
edits), full-stack Docker build must succeed with 0 errors, commit as
`deploy/chatwoot-fork/patches/0040-faq-bulk-csv-upload.patch`.

```bash
git add deploy/chatwoot-fork/patches/0040-faq-bulk-csv-upload.patch
git commit -m "feat(chatwoot-fork): bulk CSV upload button on FAQs page"
```

---

## Track 4: Round-robin ticket cap per agent

### Task 10: `PresenceFetcher.fetch_agent_open_counts()` + config

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/routing/presence.py`, `backend/apps/backend/src/chatbot/platform/config.py`
- Test: `backend/apps/backend/src/chatbot/features/routing/test_presence.py`

- [ ] **Step 1: Locate the existing conversations-listing capability**

Before writing any new HTTP-fetch code, grep for an existing
`list_conversations`-style method:
```bash
grep -rn "list_conversations\|def get_conversations\|/conversations\"" backend/apps/backend/src/chatbot/features/ agent/app/clients/
```
Read whatever this turns up. If a suitable method already exists on a
client `PresenceFetcher` can reuse or pattern-match, use its exact request
shape (path, params, pagination handling) rather than inventing a new one.
If nothing suitable exists, use `PresenceFetcher`'s own `_request` helper
(already used by `fetch_agents`) to call `GET /conversations?status=open`
directly, paginating via whatever `meta.next_page`/`payload` shape the
response actually has (verify by adding a temporary debug print against a
real tenant if uncertain — this repo has a live local Chatwoot instance at
`http://crm.localhost` with admin token `DagL27yDewjPyjwJDXCyh6VV`
(default tenant, account 1) you can curl directly to inspect the real
response shape before writing the parser).

- [ ] **Step 2: Write the failing test**

Add `test_presence.py` tests (if the file doesn't exist yet, check for an
existing `test_routing_presence.py` or similar first): mock the
conversations-list HTTP response with a few conversations having different
`assignee_id`/`meta.assignee.id` values (use whichever field the Step 1
investigation confirmed is real), assert `fetch_agent_open_counts()`
returns the correct per-agent tally; an HTTP failure returns `{}` (fail-open).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/routing/test_presence.py -k open_counts -v`

- [ ] **Step 4: Implement**

Add to `PresenceFetcher`:

```python
    async def fetch_agent_open_counts(self) -> dict[int, int]:
        """agent_id -> count of currently-open conversations assigned to them.

        Empty dict on any failure (fail-open -- the cap check in pick_agent
        becomes a no-op rather than blocking routing when this can't be
        determined).
        """
        counts: dict[int, int] = {}
        page = 1
        try:
            while True:
                res = await self._request(
                    "GET", f"/conversations?status=open&page={page}"
                )
                if not isinstance(res, dict):
                    break
                payload = res.get("data", {}).get("payload") if isinstance(res.get("data"), dict) else res.get("payload")
                if not isinstance(payload, list) or not payload:
                    break
                for conv in payload:
                    assignee = (conv.get("meta") or {}).get("assignee") or {}
                    agent_id = assignee.get("id")
                    if agent_id is not None:
                        counts[int(agent_id)] = counts.get(int(agent_id), 0) + 1
                meta = res.get("data", {}).get("meta") if isinstance(res.get("data"), dict) else res.get("meta")
                total_pages = (meta or {}).get("total_pages") if isinstance(meta, dict) else None
                if total_pages is None or page >= total_pages:
                    break
                page += 1
            return counts
        except Exception as e:
            _log.error("presence_fetch_open_counts_failed", error=str(e))
            return {}
```

**This response-shape parsing is a best guess pending Step 1's real
verification against the live local Chatwoot instance — adjust the exact
`payload`/`meta`/pagination field paths to match what Step 1 actually
found before considering this done.** Add `routing_max_concurrent_per_agent:
int = 0` to `Settings` in `config.py` (0 = unlimited, documented as such in
a comment).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/routing/ -v`

- [ ] **Step 6: Document the env var**

Add `ROUTING_MAX_CONCURRENT_PER_AGENT=0` to `backend/apps/backend/.env.example`
with a one-line comment explaining 0 = unlimited.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/routing/presence.py \
        backend/apps/backend/src/chatbot/features/routing/test_presence.py \
        backend/apps/backend/src/chatbot/platform/config.py \
        backend/apps/backend/.env.example
git commit -m "feat(routing): add PresenceFetcher.fetch_agent_open_counts + cap config"
```

---

### Task 11: Wire the cap into `pick_agent`

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/routing/service.py`
- Test: `backend/apps/backend/src/chatbot/features/routing/test_service.py` (or wherever `pick_agent`'s existing tests live — locate via grep)

**Interfaces:**
- Consumes: `PresenceFetcher.fetch_agent_open_counts()` from Task 10.

- [ ] **Step 1: Read `pick_agent` and its existing tests completely**

Already read `pick_agent` in full during this session's earlier research
(3-tier logic: first-priority-online, any-priority-online,
idle-overflow-online). Locate and read its test file in full to understand
the existing fixture pattern for `PresenceFetcher`/`ChannelPriorityStore`
mocks — this task's new tests must use the exact same mocking convention.

- [ ] **Step 2: Write the failing tests**

Add: an agent who would otherwise win tier 1 is skipped when their open
count is `>= routing_max_concurrent_per_agent` (mock
`fetch_agent_open_counts` to return a high count for that agent); an agent
under the cap still wins; with `routing_max_concurrent_per_agent = 0`
(default), behavior is byte-identical to before — re-run every EXISTING
`pick_agent` test unmodified to confirm this (they should all still pass
without any changes to their assertions).

- [ ] **Step 3: Run tests to verify the new ones fail**

Run the routing test file with `-k cap` or similar to isolate the new
tests; confirm they fail before implementing.

- [ ] **Step 4: Implement**

In `pick_agent`, after fetching `agents = await self._presence.fetch_agents()`,
add:

```python
        open_counts: dict[int, int] = {}
        if self._settings.routing_max_concurrent_per_agent > 0:
            open_counts = await self._presence.fetch_agent_open_counts()

        online = {
            a.id: a
            for a in agents
            if a.availability_status == "online"
            and (
                self._settings.routing_max_concurrent_per_agent <= 0
                or open_counts.get(a.id, 0) < self._settings.routing_max_concurrent_per_agent
            )
        }
```

This single change to the `online` dict comprehension automatically
excludes over-cap agents from all three tiers, since every tier already
filters through `online`. Check whether `RoutingService` already has
`self._settings` available (it very likely doesn't currently, since
`pick_agent`'s existing code doesn't reference settings — check the
constructor and add a `settings: Settings` param if missing, updating
every construction call site, which the earlier `main.py` grep already
located: `RoutingService(presence=..., store=...)` around the Phase 5
wiring block).

- [ ] **Step 5: Run tests to verify they pass, then the full backend suite**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/routing/service.py \
        backend/apps/backend/src/chatbot/main.py
git add -u backend/apps/backend/src/chatbot/features/routing/
git commit -m "feat(routing): exclude agents at/over the concurrent-conversation cap"
```

---

## Track 5: Customer 360 — foundational lookup

### Task 12: Backend `GET /admin/customer360/search`

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/customer360_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_customer360_router.py`
- Modify: `backend/apps/backend/src/chatbot/features/authz/seed.py`, `backend/apps/backend/src/chatbot/main.py`

**Interfaces:**
- Consumes: `RsaRepositoryPort` (already built, from the reporting-metrics-extensions work — locate it via `grep -rn "class RsaRepositoryPort" backend/apps/backend/src/`), an existing Chatwoot contact-search capability (locate before writing a new one), `require_permission`.

- [ ] **Step 1: Locate existing contact-search and RSA repository capabilities**

```bash
grep -rn "def search_contact\|contacts/search\|class RsaRepositoryPort" backend/apps/backend/src/chatbot/features/
```
Read whatever this turns up in full before writing the router — reuse
existing methods on `ChatwootAdapter`/whatever client owns contact search,
and the existing `RsaRepositoryPort.list_all()`-or-equivalent method for
incidents (check its actual method names, don't guess).

- [ ] **Step 2: Write the failing tests**

Tests: searching a phone-number-shaped query returns the matching contact
+ their conversations (mock the contact-search and conversations-list
calls); searching a non-phone-shaped query searches RSA incidents by
`vehicle_no` and conversations by `vehicle_model` custom attribute
(best-effort substring match); no match returns empty lists, not an error
(200 with `{"contact": null, "conversations": [], "rsa_incidents": []}`);
missing/wrong `x-api-key`... actually this route is `require_permission`-gated
like the PIC admin router, not `x-api-key` — match Task 3's auth test
pattern instead.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/chatbot/features/chat/test_customer360_router.py -v`

- [ ] **Step 4: Implement**

```python
"""Customer 360 foundational lookup -- searches by phone number (today's
de facto customer identity) or vehicle number, aggregating what's already
in the CRM (contact, cross-channel conversations, RSA incidents). This is
explicitly NOT a DMS integration -- see the design spec for why phone
number is used as a provisional key pending Proton's final decision.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
    from chatbot.features.rsa.rsa_repository import RsaRepositoryPort
    from chatbot.platform.config import Settings

_PHONE_RE = re.compile(r"^\+?[\d\s-]{6,}$")


def build_customer360_router(
    chatwoot: ChatwootAdapter,
    rsa_repo: RsaRepositoryPort,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/customer360", tags=["customer360"])
    view_360 = require_permission(
        "customer360.view", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/search", dependencies=[Depends(view_360)])
    async def search(q: str = Query(min_length=2)) -> dict[str, Any]:
        contact: dict | None = None
        conversations: list[dict] = []
        rsa_incidents: list[dict] = []

        if _PHONE_RE.match(q.strip()):
            # phone-number search path -- fill in using whatever contact-search
            # method Step 1 located.
            ...
        else:
            # vehicle-number search path -- fill in using RsaRepositoryPort +
            # a best-effort conversations custom-attribute match.
            ...

        return {"contact": contact, "conversations": conversations, "rsa_incidents": rsa_incidents}

    return router
```

**The two `...` branches must be filled in with real code calling the
methods Step 1 located** — this is not a placeholder to ship as-is; it's
flagging that the exact method names depend on Step 1's findings, which
weren't available at plan-writing time. Fill both branches in fully before
writing tests against them (or write the tests first per TDD and let them
drive the real implementation, whichever order — either way, no `...` may
remain when this task reports DONE).

- [ ] **Step 5: Register the permission**

Add to `PERMISSION_REGISTRY` in `authz/seed.py`:
```python
    "customer360.view": "View the Customer 360 lookup",
```

- [ ] **Step 6: Wire into `main.py`**

Inside the same RBAC block as Task 3's router, add the customer360 router
mount, reusing the already-constructed `chatwoot`/`rsa_repo` instances
(locate where `rsa_repo`/the RSA repository instance is built in `main.py`
already — it exists from the reporting-metrics-extensions work; reuse it,
don't construct a second one).

- [ ] **Step 7: Run tests, then the full backend suite**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`

- [ ] **Step 8: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/customer360_router.py \
        backend/apps/backend/src/chatbot/features/chat/test_customer360_router.py \
        backend/apps/backend/src/chatbot/features/authz/seed.py \
        backend/apps/backend/src/chatbot/main.py
git commit -m "feat(customer360): add /admin/customer360/search foundational lookup"
```

---

### Task 13: Chatwoot fork patch — Customer 360 admin page

**Files:**
- Author in the clone: `app/javascript/dashboard/views/ProtonCustomer360Page.vue` (new), `app/javascript/dashboard/api/protonAdmin.js` (extend), sidebar + route registration (same files as Task 4)
- Create: `deploy/chatwoot-fork/patches/0041-customer360-admin.patch`

- [ ] **Step 1-7:** Same shape as Task 4 — a single search box, results
  list (conversations clickable through to the real conversation via
  Chatwoot's existing conversation-link URL pattern, RSA incidents
  listed read-only), RBAC-gated on `customer360.view`, standalone sidebar
  icon, local build check, export, full-stack verify, commit as
  `deploy/chatwoot-fork/patches/0041-customer360-admin.patch`.

```bash
git add deploy/chatwoot-fork/patches/0041-customer360-admin.patch
git commit -m "feat(chatwoot-fork): Customer 360 foundational lookup page"
```

---

## Final tasks: docs, full verification, deploy

### Task 14: Update CLAUDE.md and README.md

**Files:**
- Modify: `CLAUDE.md` (repo root), `README.md` (repo root, if it documents the Zammad service or PIC config)

- [ ] **Step 1:** In `CLAUDE.md`, update the "Direction (2026-07): migrating
  to Chatwoot-only — Zammad is being retired" paragraph to reflect Zammad
  is now FULLY REMOVED (not gated off) — rewrite it as a statement of fact,
  not a migration-in-progress. Remove the `ZAMMAD_TICKETING_ENABLED`
  mention in the webhook-pattern section (no longer exists). Add a short
  mention of the new PIC/Dealer escalation-routing admin page and
  Customer 360 lookup under wherever the Knowledge/admin-pages are
  described, if such a section exists.
- [ ] **Step 2:** Read `README.md` in full; update any section that
  mentions Zammad as a running service, or documents the escalation
  config as raw `PIC_MAP_JSON` env-var editing (point to the new admin UI
  instead, keeping the env-var fallback mentioned as a legacy path).
- [ ] **Step 3:** Commit.
```bash
git add CLAUDE.md README.md
git commit -m "docs: update CLAUDE.md/README.md for Zammad removal + new admin pages"
```

### Task 15: Full local Docker image build + local deploy verification

- [ ] **Step 1:** Full agent suite: `cd agent && source .venv/bin/activate && pytest`
- [ ] **Step 2:** Full backend suite: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key uv run pytest src/ -q`
- [ ] **Step 3:** Full local Chatwoot image build (all patches through
  0041): `cd deploy/chatwoot-fork && docker build --build-arg
  UPSTREAM_VERSION=v4.15.1 --build-arg PROTON_BUILD_SHA=$(git rev-parse
  --short HEAD) -t proton-chatwoot:v4.15.1-custom .`
- [ ] **Step 4:** If ANY of the above fail, this is a stop-the-line
  signal — do not proceed to deploy with a broken build. Fix forward
  (resume as if in a fix-loop round) before continuing.

### Task 16: Push to origin and deploy to VM

- [ ] **Step 1:** Merge the worktree branch to `dev-yuda` locally (same
  flow as every prior merge this session: checkout `dev-yuda`, `git merge
  <worktree-branch>`, re-run both full suites on the merged result before
  pushing).
- [ ] **Step 2:** `git push origin dev-yuda`.
- [ ] **Step 3:** Rebuild agent+backend images and redeploy on the VM for
  every tenant that had Zammad removed and/or needs the new PIC/Customer360
  features — sync current source (`git archive HEAD` for `agent/` and
  `backend/apps/backend/`, per the established deploy runbook used earlier
  this session), rebuild, `up -d --force-recreate backend agent` for
  `default`, `proton`, and `wahchan`.
- [ ] **Step 4:** Cloud Build the Chatwoot image (all patches through
  0041) and redeploy `chatwoot-rails`/`chatwoot-sidekiq` on `default` and
  `proton` (matching the deploy pattern used repeatedly this session) —
  `wahchan` only if it's already running the custom image (check first;
  don't newly onboard a tenant onto the custom image as a side effect of
  this task if it wasn't already).
- [ ] **Step 5:** Verify every redeployed service is healthy (`docker ps`
  status + a login-page curl, same checks used throughout this session)
  before considering the task done.
- [ ] **Step 6:** Set new feature flags to sensible defaults on `proton`
  (the tenant actually used for client demos): `RBAC_ENABLED` must already
  be on for the new admin pages to be reachable at all — verify, don't
  enable it blind if it wasn't already a deliberate choice (check
  `proton.env`; if `RBAC_ENABLED` is already true from the earlier RBAC
  work this session, the new pages just appear once the image is deployed
  — no new flag needed for tracks 1/5).  `ROUTING_MAX_CONCURRENT_PER_AGENT`
  stays at its default `0` (unlimited) — do NOT pick an arbitrary cap
  number for the client's real operation without their input; leaving it
  off is the safe default for an overnight run.

---

## Final report

After Task 16, write a summary (not a file — this is what the human reads
in the morning) covering: what was built and deployed, what was
deliberately skipped and why (reuse the Track 0 triage table), any
concerns or judgment calls made along the way, and the exact smoke-test
checklist for the human to run (Escalation Routing page reachable + can
add a PIC entry; Customer 360 page reachable + a phone-number search
returns something; FAQ bulk-upload button works with a sample CSV;
Zammad containers confirmed gone from `docker ps` on all 3 tenants; no
`zammad` string anywhere in `grep -ri zammad agent/ backend/apps/backend/src/`).
