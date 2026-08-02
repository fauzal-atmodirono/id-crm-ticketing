# RBAC Phase 2 — SLA Policy Store/UI + Audit Viewer + Roles & Permissions UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 2 of `docs/superpowers/specs/2026-07-27-own-sla-audit-rbac-design.md` — flip the three Chatwoot enterprise flags off, add an operator-editable SLA policy store (backend Postgres, per-inbox with tenant-default fallback) that `sla.py` reads, add a cross-ticket audit list/filter endpoint over the existing Firestore audit trail, extend Phase 1's RBAC surface with the CRUD Phase 2's UIs need (role↔permission grant/revoke, role↔user unassign, permission registry, users-for-role), and add three new fork-patch admin pages ("SLA Policies", "Audit Log", "Roles & Permissions") that consume all of the above, RBAC-gated.

**Architecture:** Backend-only new storage (SLA policy tables) reuses Phase 1's `rbac_database_url` Postgres connection via its own `AsyncEngine`/`Base`, following this repo's established "each feature owns its engine" convention (see `kb_db.py`, `authz/db.py`). `sla.py`'s existing per-conversation scan gets one new optional constructor-style parameter (`policy_repo`) that is `None` by default — untouched deployments get byte-identical behavior. The audit list/filter endpoint adds one new method to the existing `AuditLogPort` protocol and implements it on both adapters (`InMemoryAuditLog`, `FirestoreAuditLog`) — no new storage. All three new admin surfaces are FastAPI routers gated by `require_permission` (Phase 1, unchanged) and are mounted in `main.py` inside/alongside the existing RBAC wiring block. The three new fork-patch pages are native Vue (matching the current `AgentPrioritiesEditor.vue`-style pattern — no iframe), added as new numbered patches (`0025`, `0026`, `0027`) applied after `0024`, each forwarding the caller's Chatwoot access token (already available client-side via `useMapGetter('getCurrentUser')`) so the backend's `require_permission` dependency can resolve a real identity instead of falling back to the shared-secret path.

**Tech Stack:** Python (FastAPI, SQLAlchemy 2.0 async, pytest + respx + aiosqlite — matching `features/authz/`'s existing test conventions), Vue 3 Options API (matching `AgentPrioritiesEditor.vue`), Chatwoot fork patch pipeline (`deploy/chatwoot-fork/`).

## Global Constraints

- **No new backend env vars for feature-enablement.** All three new admin surfaces (SLA policy store, audit list endpoint, roles/permissions CRUD extensions) are gated by the *existing* `settings.rbac_enabled and settings.rbac_database_url` — per the spec, "All new pages RBAC-gated (depends on phase 1)." An unconfigured/RBAC-off tenant sees none of this; nothing new to document in `example.env` beyond a short note (Task 11).
- **The SLA policy store reuses `rbac_database_url`'s connection string** (same per-tenant Postgres RBAC already provisions) but builds its **own** `AsyncEngine`/`async_sessionmaker`/`DeclarativeBase` — mirrors how `kb_db.py` and `authz/db.py` each independently own their engine today; do not import or share SQLAlchemy `Base`/engine objects across `features/chat/` and `features/authz/`.
- **Byte-identical fallback is non-negotiable.** `sla.py` with `policy_repo=None` (the default), or with a `policy_repo` whose `resolve()` returns `None`/all-`None`-fields for a given inbox, MUST produce identical output to today's code. Every new test in Task 3 must include at least one case proving this.
- **Backend admin endpoints fail closed.** Every new endpoint added in Tasks 4, 6, 8 uses `require_permission(...)` from `features/authz/deps.py` exactly as `authz/router.py` already does (`dependencies=[Depends(require_permission(...))]` or an equivalent `Depends` on the specific permission) — never a bespoke auth check.
- **No Chatwoot `enterprise/` server code is touched.** Task 1 only edits `chatwoot-config/provision_features.py`'s Python-side flag lists; the fork patches (Tasks 9-11) only touch `app/javascript/` frontend files, matching every existing patch in `deploy/chatwoot-fork/patches/`.
- **Fork patches are additive and reversible**, numbered `0025`, `0026`, `0027`, applied strictly after `0024-agent-priorities.patch`. Each patch task must verify: `git apply --whitespace=fix` succeeds standalone against an `0001`-through-`0024`-applied tree, AND all three new patches together still leave the tree in a state where nothing later (there is nothing later yet, but leave the tree valid for a hypothetical `0028`).
- **Client-side RBAC gating (nav visibility) is UX-only, not the security boundary** — the backend's `require_permission` is what actually enforces access; a hidden-but-reachable-by-URL page must still 403 correctly server-side. Do not treat client-side hiding as sufficient and skip server-side gating on any new endpoint.
- **Audit storage stays Firestore** (or in-memory for dev/tests) — Task 5 adds one new read method to the existing port/adapters; it does not introduce a new store.
- **No SLA breach *engine* logic changes.** Task 3 only changes *where threshold values come from*, never the scan/breach/alert algorithm itself.

---

### Task 1: Flip the three Chatwoot enterprise flags

**Files:**
- Modify: `chatwoot-config/provision_features.py`
- Test: `chatwoot-config/test_provision_features.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this is a pure data change (which flags land in the script's `ENABLE` vs `DISABLE` lists) plus a first-ever test file guarding it.

- [ ] **Step 1: Read the current script and its sibling's test conventions**

Run: `cat chatwoot-config/provision_features.py` and `cat chatwoot-config/test_provision_case_taxonomy.py` (if present) to match this repo's test-fixture style for a Chatwoot-API-talking provisioning script (how it mocks the Rails-runner/API call, how a "dry run" is expressed).

- [ ] **Step 2: Move the three flags**

In `provision_features.py`, the current lists are:

```python
DISABLE = ["captain_integration", "captain_tasks", "custom_tools",
           "captain_document_auto_sync", "contact_chatwoot_support_team"]
ENABLE = ["disable_branding", "sla", "audit_logs", "custom_roles"]
```

Change to:

```python
DISABLE = ["captain_integration", "captain_tasks", "custom_tools",
           "captain_document_auto_sync", "contact_chatwoot_support_team",
           "sla", "audit_logs", "custom_roles"]
ENABLE = ["disable_branding"]
```

Reversible by re-swapping the three strings back if a future tenant wants Chatwoot's native enterprise pages instead of ours.

- [ ] **Step 3: Write a test asserting the flag placement**

```python
# chatwoot-config/test_provision_features.py
from provision_features import DISABLE, ENABLE


def test_enterprise_sla_audit_roles_flags_are_disabled():
    for flag in ("sla", "audit_logs", "custom_roles"):
        assert flag in DISABLE, f"{flag} must be in DISABLE — Phase 2 replaces it with our own surface"
        assert flag not in ENABLE


def test_disable_branding_still_enabled():
    assert "disable_branding" in ENABLE
```

(Match whatever import style `test_provision_case_taxonomy.py` uses if it differs — e.g. relative import or `sys.path` shim — this file's Step 1 read tells you which.)

- [ ] **Step 4: Run the test**

Run: `cd chatwoot-config && python -m pytest test_provision_features.py -v` (or the equivalent runner `test_provision_case_taxonomy.py` uses, per Step 1).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chatwoot-config/provision_features.py chatwoot-config/test_provision_features.py
git commit -m "fix(chatwoot-config): disable sla/audit_logs/custom_roles enterprise flags — Phase 2 replaces them"
```

---

### Task 2: SLA policy store — Postgres models + repository

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/sla_policy_db.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/sla_policy_repository.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_sla_policy_repository.py`

**Interfaces:**
- Consumes: nothing new (self-contained storage layer).
- Produces (for Tasks 3-4): `SlaPolicyValues` dataclass (`response_hours: float | None`, `resolution_hours: float | None`, `ack_minutes_by_channel_json: str | None`, `pic_whatsapp: str | None`, `engine_enabled: bool | None`); `SlaPolicyRepository(session_maker)` with `get_tenant_default() -> SlaPolicyValues | None`, `get_for_inbox(inbox_id: int) -> SlaPolicyValues | None`, `upsert_tenant_default(**fields) -> SlaPolicyValues`, `upsert_for_inbox(inbox_id: int, **fields) -> SlaPolicyValues`, `resolve(inbox_id: int | None) -> SlaPolicyValues | None`; `build_engine(url) -> AsyncEngine`, `build_session_maker(engine) -> async_sessionmaker`, `init_sla_policy_db(engine) -> None`.

- [ ] **Step 1: Write `sla_policy_db.py`**

```python
"""SLA policy store — operator-editable overrides for sla.py's thresholds.

Reuses the RBAC feature's Postgres connection string (rbac_database_url) but
owns its own engine/Base, matching how kb_db.py and authz/db.py each
independently own their engine (see CLAUDE.md's per-feature-engine
convention). A `(inbox_id)` row with inbox_id NULL is the tenant-wide
default; a specific inbox_id row overrides it for that inbox. The single
tenant-default-row invariant is enforced at the repository layer (get-then-
upsert), not by the database — Postgres treats multiple NULLs in a UNIQUE
column as distinct, so a DB constraint alone can't express "at most one
NULL row."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, Text, UniqueConstraint, func
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (UniqueConstraint("inbox_id", name="uq_sla_policies_inbox_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    ack_minutes_by_channel_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pic_whatsapp: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


@dataclass
class SlaPolicyValues:
    response_hours: float | None = None
    resolution_hours: float | None = None
    ack_minutes_by_channel_json: str | None = None
    pic_whatsapp: str | None = None
    engine_enabled: bool | None = None


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def build_engine(url: str) -> AsyncEngine:
    return create_async_engine(_to_async_url(url))


def build_session_maker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_sla_policy_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Step 2: Write `sla_policy_repository.py`**

```python
"""Repository for the SLA policy store — get/upsert tenant-default and
per-inbox rows, plus the inbox-specific -> tenant-default resolution used by
sla.py (env fallback happens one layer up, in sla.py itself, since this
repository has no knowledge of Settings)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from chatbot.features.chat.sla_policy_db import SlaPolicy, SlaPolicyValues

_FIELDS = (
    "response_hours",
    "resolution_hours",
    "ack_minutes_by_channel_json",
    "pic_whatsapp",
    "engine_enabled",
)


def _to_values(row: SlaPolicy) -> SlaPolicyValues:
    return SlaPolicyValues(**{f: getattr(row, f) for f in _FIELDS})


class SlaPolicyRepository:
    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._sm = session_maker

    async def get_tenant_default(self) -> SlaPolicyValues | None:
        async with self._sm() as session:
            row = (
                await session.execute(select(SlaPolicy).where(SlaPolicy.inbox_id.is_(None)))
            ).scalars().first()
            return _to_values(row) if row is not None else None

    async def get_for_inbox(self, inbox_id: int) -> SlaPolicyValues | None:
        async with self._sm() as session:
            row = (
                await session.execute(select(SlaPolicy).where(SlaPolicy.inbox_id == inbox_id))
            ).scalars().first()
            return _to_values(row) if row is not None else None

    async def upsert_tenant_default(self, **fields: object) -> SlaPolicyValues:
        return await self._upsert(None, fields)

    async def upsert_for_inbox(self, inbox_id: int, **fields: object) -> SlaPolicyValues:
        return await self._upsert(inbox_id, fields)

    async def _upsert(self, inbox_id: int | None, fields: dict) -> SlaPolicyValues:
        async with self._sm() as session:
            row = (
                await session.execute(select(SlaPolicy).where(SlaPolicy.inbox_id == inbox_id))
            ).scalars().first()
            if row is None:
                row = SlaPolicy(inbox_id=inbox_id)
                session.add(row)
            for key, value in fields.items():
                if key in _FIELDS:
                    setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return _to_values(row)

    async def resolve(self, inbox_id: int | None) -> SlaPolicyValues | None:
        """inbox-specific row's non-None fields win; unset fields fall back to
        the tenant-default row's value; returns None only when neither row
        exists at all (caller falls back fully to env)."""
        inbox_row = await self.get_for_inbox(inbox_id) if inbox_id is not None else None
        default_row = await self.get_tenant_default()
        if inbox_row is None and default_row is None:
            return None
        merged = {}
        for f in _FIELDS:
            inbox_val = getattr(inbox_row, f) if inbox_row is not None else None
            default_val = getattr(default_row, f) if default_row is not None else None
            merged[f] = inbox_val if inbox_val is not None else default_val
        return SlaPolicyValues(**merged)
```

- [ ] **Step 3: Write the tests**

```python
# test_sla_policy_repository.py
import pytest

from chatbot.features.chat.sla_policy_db import build_engine, build_session_maker, init_sla_policy_db
from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/sla_policy.db")
    await init_sla_policy_db(engine)
    return SlaPolicyRepository(build_session_maker(engine))


async def test_get_tenant_default_absent_returns_none(repo):
    assert await repo.get_tenant_default() is None


async def test_upsert_and_get_tenant_default(repo):
    await repo.upsert_tenant_default(response_hours=4.0, pic_whatsapp="+6281234567890")
    values = await repo.get_tenant_default()
    assert values.response_hours == 4.0
    assert values.pic_whatsapp == "+6281234567890"
    assert values.resolution_hours is None


async def test_upsert_tenant_default_twice_updates_same_row(repo):
    await repo.upsert_tenant_default(response_hours=4.0)
    await repo.upsert_tenant_default(response_hours=8.0)
    values = await repo.get_tenant_default()
    assert values.response_hours == 8.0


async def test_get_for_inbox_absent_returns_none(repo):
    assert await repo.get_for_inbox(42) is None


async def test_upsert_and_get_for_inbox(repo):
    await repo.upsert_for_inbox(42, response_hours=2.0)
    values = await repo.get_for_inbox(42)
    assert values.response_hours == 2.0


async def test_resolve_with_no_rows_returns_none(repo):
    assert await repo.resolve(42) is None
    assert await repo.resolve(None) is None


async def test_resolve_falls_back_to_tenant_default_when_no_inbox_row(repo):
    await repo.upsert_tenant_default(response_hours=4.0, resolution_hours=24.0)
    resolved = await repo.resolve(42)
    assert resolved.response_hours == 4.0
    assert resolved.resolution_hours == 24.0


async def test_resolve_inbox_row_overrides_tenant_default_field_by_field(repo):
    await repo.upsert_tenant_default(response_hours=4.0, resolution_hours=24.0)
    await repo.upsert_for_inbox(42, response_hours=1.0)  # only overrides response_hours
    resolved = await repo.resolve(42)
    assert resolved.response_hours == 1.0
    assert resolved.resolution_hours == 24.0  # inherited from tenant default


async def test_resolve_inbox_only_no_tenant_default(repo):
    await repo.upsert_for_inbox(42, engine_enabled=False)
    resolved = await repo.resolve(42)
    assert resolved.engine_enabled is False
    assert resolved.response_hours is None
```

Add `pytest_asyncio` markers/fixtures matching this repo's existing async-test setup (check `features/authz/test_repository.py` for the exact `pytest.mark.asyncio` / `pytest-asyncio` mode convention already used — `pyproject.toml` sets `asyncio_mode=auto` per `agent/`'s conftest, confirm `backend/`'s equivalent before assuming no marker is needed).

- [ ] **Step 4: Run tests**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_sla_policy_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/sla_policy_db.py src/chatbot/features/chat/sla_policy_repository.py src/chatbot/features/chat/test_sla_policy_repository.py
git commit -m "feat(sla): add operator-editable SLA policy store (Postgres, per-inbox + tenant default)"
```

---

### Task 3: Wire the policy store into `sla.py`'s resolution order

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py` (`scan_conversations` at line ~156, `run_sla_scan_job` at line ~375, `start_sla_scheduler` at line ~437)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_sla.py` (extend)

**Interfaces:**
- Consumes: Task 2's `SlaPolicyRepository`, `SlaPolicyValues`.
- Produces: `scan_conversations(..., policy_repo: SlaPolicyRepository | None = None)`, `run_sla_scan_job(..., policy_repo: SlaPolicyRepository | None = None)`, `start_sla_scheduler(..., policy_repo: SlaPolicyRepository | None = None)` — all default `None`, threaded straight through to the next layer down. Task 4 passes a real repo when RBAC+the SLA store are enabled; `main.py`'s current call site (unmodified until Task 4) keeps working unchanged since the new param is keyword-only with a default.

- [ ] **Step 1: Read `sla.py`'s `scan_conversations` in full first**

Run: `sed -n '156,260p' backend/apps/backend/src/chatbot/features/chat/sla.py` — confirm the exact current lines around `response_threshold`/`resolution_threshold_default` (~174-175), the per-`conv` loop (~182), the per-conversation Chatwoot-label override (`_chatwoot_sla_minutes`, ~196), and the channel-ack lookup (`ack_minutes_by_channel.get(channel)`, ~205) before editing — line numbers may have drifted slightly since this plan was written.

- [ ] **Step 2: Write the failing tests first**

Add to `test_sla.py` (match its existing fixture/mock conventions — e.g. however it currently builds a fake `fetch_conversations` and `Settings`):

```python
async def test_scan_conversations_byte_identical_when_policy_repo_is_none(...):
    # Run scan_conversations with policy_repo=None (today's default) and
    # again with the parameter simply omitted; assert identical results —
    # guards against the new parameter accidentally changing default behavior.
    ...

async def test_scan_conversations_uses_policy_store_response_hours_when_set(...):
    # policy_repo.resolve(inbox_id) returns SlaPolicyValues(response_hours=1.0);
    # a conversation whose age exceeds 1h (but not settings.sla_response_hours)
    # must be flagged as breached — proves the store value is actually used
    # ahead of the env default.
    ...

async def test_scan_conversations_falls_back_to_env_when_policy_repo_resolve_returns_none(...):
    # policy_repo.resolve(inbox_id) returns None (no rows at all for this
    # inbox/tenant) — behavior must match the policy_repo=None case exactly.
    ...
```

Use a lightweight fake for `policy_repo` (an object with an async `resolve(inbox_id)` method returning a canned `SlaPolicyValues` or `None`) — do not require a real database in this test file, matching `test_sla.py`'s existing style of injecting fakes via constructor/parameter rather than hitting Postgres.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_sla.py -v -k policy_repo`
Expected: FAIL (`scan_conversations` doesn't accept `policy_repo` yet).

- [ ] **Step 4: Implement**

Add the import and thread the parameter through all three functions:

```python
from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository  # add to imports
```

`scan_conversations` signature gains `policy_repo: SlaPolicyRepository | None = None` (keyword-only, alongside the existing `alert`/`level2_alert`). Inside the per-`conv` loop, right after `inbox_id = conv.get("inbox_id")` is available (or wherever the loop currently reads `conv`), resolve a policy before computing thresholds:

```python
        resolved_policy = None
        if policy_repo is not None:
            resolved_policy = await policy_repo.resolve(conv.get("inbox_id"))

        response_hours = (
            resolved_policy.response_hours
            if resolved_policy is not None and resolved_policy.response_hours is not None
            else settings.sla_response_hours
        )
        resolution_hours = (
            resolved_policy.resolution_hours
            if resolved_policy is not None and resolved_policy.resolution_hours is not None
            else settings.sla_resolution_hours
        )
```

Use `response_hours`/`resolution_hours` wherever the function currently reads `settings.sla_response_hours`/`settings.sla_resolution_hours` directly inside the loop (do not change any usage of those settings *outside* the loop, e.g. any module-level defaults). Similarly, for the per-channel ACK lookup (~205), when `resolved_policy is not None and resolved_policy.ack_minutes_by_channel_json`, parse it the same way the existing `settings.sla_ack_minutes_by_channel_json` is parsed (reuse the existing parsing helper/inline `json.loads` — do not duplicate parsing logic; extract a tiny local helper if the existing code inlines it more than once as a result of this change) and prefer it over the settings value; otherwise keep today's exact lookup. For `pic_whatsapp` and `engine_enabled`, apply the same "resolved value wins if not None, else today's settings-based value" substitution at their existing usage sites — do not move *where* engine_enabled is checked (that's `sla_engine_enabled`, read once outside the loop in `start_sla_scheduler`, not per-conversation — see Step 4b below for the correct place to apply an inbox-level engine on/off override, if the existing code structure makes a per-conversation override meaningful; if `sla_engine_enabled` is only ever checked once globally before scanning starts, per-inbox `engine_enabled` overrides are out of scope for this task — note this explicitly as a known limitation in the commit message rather than forcing an awkward per-conversation skip that the rest of the function doesn't support).

`run_sla_scan_job` gains `policy_repo: SlaPolicyRepository | None = None`, passed straight through:

```python
def run_sla_scan_job(
    settings: Settings,
    audit: AuditLogPort,
    *,
    twilio_adapter: TwilioChannelAdapter | None = None,
    policy_repo: SlaPolicyRepository | None = None,
) -> list[AuditEntry]:
    alert = _build_pic_alert(settings, twilio_adapter)
    level2_alert = _build_level2_alert(settings, twilio_adapter)
    try:
        return asyncio.run(
            scan_conversations(
                settings, audit, alert=alert, level2_alert=level2_alert, policy_repo=policy_repo
            )
        )
    except Exception as e:
        _log.error("sla_scan_job_failed", error=str(e))
        return []
```

`start_sla_scheduler` gains `policy_repo: SlaPolicyRepository | None = None`, passed into the `job` lambda's `run_sla_scan_job` call:

```python
def start_sla_scheduler(
    settings: Settings,
    audit: AuditLogPort,
    *,
    twilio_adapter: TwilioChannelAdapter | None = None,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
    policy_repo: SlaPolicyRepository | None = None,
) -> Any | None:
    if not settings.sla_engine_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (
        lambda: run_sla_scan_job(
            settings, audit, twilio_adapter=twilio_adapter, policy_repo=policy_repo
        )
    )
    ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_sla.py -v`
Expected: PASS, including all pre-existing tests (byte-identical guarantee holds).

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/sla.py src/chatbot/features/chat/test_sla.py
git commit -m "feat(sla): resolve response/resolution/ack thresholds from the policy store when provided"
```

---

### Task 4: SLA policy admin endpoint + router + `main.py` wiring

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/sla_policy_router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py` (RBAC wiring block, ~line 483-519, and the `start_sla_scheduler(...)` call, ~line 547)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_sla_policy_router.py`

**Interfaces:**
- Consumes: Task 2's `SlaPolicyRepository`/`SlaPolicyValues`, Phase 1's `require_permission`, `TokenValidator`, `Settings`.
- Produces: `build_sla_policy_router(repo: SlaPolicyRepository, validator: TokenValidator, settings: Settings) -> APIRouter` mounted at endpoints below.

- [ ] **Step 1: Write the router**

```python
"""SLA policy admin API — read/write the operator-editable SLA policy store.

Gated behind the `sla.manage` permission via Phase 1's `require_permission`,
matching authz/router.py's pattern exactly (constant-time shared-secret
fallback when RBAC is off, fail-closed permission check when RBAC is on).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository
    from chatbot.platform.config import Settings


class SlaPolicyBody(BaseModel):
    response_hours: float | None = None
    resolution_hours: float | None = None
    ack_minutes_by_channel_json: str | None = None
    pic_whatsapp: str | None = None
    engine_enabled: bool | None = None


def _to_dict(values) -> dict:
    return {
        "response_hours": values.response_hours,
        "resolution_hours": values.resolution_hours,
        "ack_minutes_by_channel_json": values.ack_minutes_by_channel_json,
        "pic_whatsapp": values.pic_whatsapp,
        "engine_enabled": values.engine_enabled,
    }


def build_sla_policy_router(
    repo: SlaPolicyRepository, validator: TokenValidator, settings: Settings
) -> APIRouter:
    router = APIRouter(prefix="/admin/sla-policy", tags=["sla-policy"])
    manage_sla = require_permission(
        "sla.manage", repo=None, validator=validator, settings=settings
    )
    # NOTE: repo=None above is WRONG for authz's own require_permission — that
    # dependency needs the *AuthzRepository* (permission lookups), not the SLA
    # policy repository. Pass the real authz_repo from main.py's closure
    # instead — see Step 3's wiring; this docstring flags the easy mix-up.

    @router.get("/default", dependencies=[Depends(manage_sla)])
    async def get_default() -> dict:
        values = await repo.get_tenant_default()
        return _to_dict(values) if values is not None else _to_dict(_empty())

    @router.put("/default", dependencies=[Depends(manage_sla)])
    async def put_default(body: SlaPolicyBody) -> dict:
        values = await repo.upsert_tenant_default(**body.model_dump())
        return _to_dict(values)

    @router.get("/inbox/{inbox_id}", dependencies=[Depends(manage_sla)])
    async def get_inbox(inbox_id: int) -> dict:
        values = await repo.get_for_inbox(inbox_id)
        return _to_dict(values) if values is not None else _to_dict(_empty())

    @router.put("/inbox/{inbox_id}", dependencies=[Depends(manage_sla)])
    async def put_inbox(inbox_id: int, body: SlaPolicyBody) -> dict:
        values = await repo.upsert_for_inbox(inbox_id, **body.model_dump())
        return _to_dict(values)

    return router


def _empty():
    from chatbot.features.chat.sla_policy_db import SlaPolicyValues

    return SlaPolicyValues()
```

Fix the flagged mix-up before committing: `require_permission("sla.manage", repo=..., validator=..., settings=...)` must receive the **`AuthzRepository`** instance (it looks up `permissions_for_user`), not `SlaPolicyRepository`. Change `build_sla_policy_router`'s signature to also accept `authz_repo: AuthzRepository` distinctly from the SLA `repo: SlaPolicyRepository`:

```python
def build_sla_policy_router(
    repo: SlaPolicyRepository,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/sla-policy", tags=["sla-policy"])
    manage_sla = require_permission(
        "sla.manage", repo=authz_repo, validator=validator, settings=settings
    )
    ...
```

(Delete the erroneous inline `repo=None` version and its docstring comment above — that was scaffolding to make the mistake visible, not code to ship. The final file has only the corrected version.)

- [ ] **Step 2: Write the tests**

Follow `features/authz/test_deps.py`'s exact harness (sqlite `tmp_path` engine, `TestClient`, respx-stubbed `/api/v1/profile`) — mount `build_sla_policy_router(sla_repo, authz_repo, validator, settings)` on a scratch `FastAPI()` and assert:

```python
async def test_get_default_requires_sla_manage_permission(...):
    # user with no roles -> 403 when RBAC enabled
    ...

async def test_get_default_returns_empty_policy_when_unset(...):
    # authenticated + permitted -> 200, all-None body
    ...

async def test_put_then_get_default_roundtrips(...):
    # PUT {response_hours: 4.0} then GET /default -> response_hours == 4.0
    ...

async def test_put_then_get_inbox_roundtrips(...):
    ...

async def test_rbac_disabled_falls_back_to_shared_secret(...):
    # settings.rbac_enabled=False -> x-api-key path works, matching authz/test_deps.py's equivalent case
    ...
```

- [ ] **Step 3: Wire into `main.py`**

Inside the existing `if settings.rbac_enabled and settings.rbac_database_url:` block (the RBAC section, ~line 483-519), add the SLA policy store's own engine/tables and mount the new router, reusing the already-in-scope `authz_repo`/`authz_validator`/`settings`:

```python
    # --- RBAC (roles/permissions; default-off) ---
    authz_repo = None
    sla_policy_repo = None
    if settings.rbac_enabled and settings.rbac_database_url:
        from chatbot.features.authz.db import build_engine as build_authz_engine
        from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
        from chatbot.features.authz.identity import TokenValidator
        from chatbot.features.authz.repository import AuthzRepository
        from chatbot.features.authz.router import build_authz_router
        from chatbot.features.chat.sla_policy_db import build_engine as build_sla_policy_engine
        from chatbot.features.chat.sla_policy_db import (
            build_session_maker as build_sla_policy_session_maker,
        )
        from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository
        from chatbot.features.chat.sla_policy_router import build_sla_policy_router

        authz_engine = build_authz_engine(settings.rbac_database_url)
        authz_session_maker = build_authz_session_maker(authz_engine)
        authz_repo = AuthzRepository(authz_session_maker)
        authz_validator = TokenValidator(settings)
        app.include_router(build_authz_router(authz_repo, authz_validator, settings))
        app.state.authz_engine = authz_engine
        app.state.authz_repo = authz_repo

        sla_policy_engine = build_sla_policy_engine(settings.rbac_database_url)
        sla_policy_repo = SlaPolicyRepository(build_sla_policy_session_maker(sla_policy_engine))
        app.include_router(
            build_sla_policy_router(sla_policy_repo, authz_repo, authz_validator, settings)
        )
        app.state.sla_policy_engine = sla_policy_engine
    elif settings.rbac_enabled:
        ...  # unchanged existing warning branch
```

Extend the existing `_init_authz_db` startup handler to also create the SLA policy tables (same function, one more call — do not add a second `@app.on_event("startup")` handler for this):

```python
    @app.on_event("startup")
    async def _init_authz_db() -> None:
        engine = getattr(app.state, "authz_engine", None)
        repo = getattr(app.state, "authz_repo", None)
        if engine is not None and repo is not None:
            from chatbot.features.authz.db import init_authz_db
            from chatbot.features.authz.seed import seed_defaults

            await init_authz_db(engine)
            await seed_defaults(repo)
            if settings.rbac_bootstrap_admin_user_id is not None:
                await repo.assign_role(settings.rbac_bootstrap_admin_user_id, "administrator")

        sla_policy_engine = getattr(app.state, "sla_policy_engine", None)
        if sla_policy_engine is not None:
            from chatbot.features.chat.sla_policy_db import init_sla_policy_db

            await init_sla_policy_db(sla_policy_engine)
```

Finally, thread `sla_policy_repo` into the existing `start_sla_scheduler(...)` call (further down in the same function, ~line 547) — this line runs *after* the RBAC block, so `sla_policy_repo` (declared `None` above, possibly reassigned inside the `if`) is already in scope:

```python
    sla_scheduler = start_sla_scheduler(
        settings, audit_log, twilio_adapter=twilio_adapter, policy_repo=sla_policy_repo
    )
```

- [ ] **Step 4: Run tests**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_sla_policy_router.py -v`
Expected: PASS.

Run the app-wiring smoke test too (whatever test file covers `main.py`'s `create_app()` — check `test_chatwoot_wiring.py`/`test_routing_mount.py` for the convention and confirm `create_app()` still builds cleanly with RBAC enabled in a test settings fixture):

Run: `cd backend/apps/backend && pytest src/chatbot/ -k "wiring or main" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/sla_policy_router.py src/chatbot/features/chat/test_sla_policy_router.py src/chatbot/main.py
git commit -m "feat(sla): add sla.manage-gated admin endpoint for the SLA policy store"
```

---

### Task 5: Audit port — `list_filtered` + adapter implementations

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/ports.py` (`AuditLogPort` protocol, ~line 271-288)
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/audit_log.py` (`InMemoryAuditLog`, `FirestoreAuditLog`)
- Test: `backend/apps/backend/src/chatbot/features/chat/adapters/test_audit_log.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `AuditLogPort.list_filtered(self, *, actor: str | None = None, from_ts: str | None = None, to_ts: str | None = None, limit: int = 200) -> list[AuditEntry]`, implemented on both adapters.

- [ ] **Step 1: Read the existing port and both adapters first**

Run: `sed -n '260,300p' backend/apps/backend/src/chatbot/features/chat/ports.py` and read `adapters/audit_log.py` in full — confirm `AuditEntry`'s exact fields (`ticket_id`, `session_id`, `actor`, `from_state`, `to_state`, `at`, `remark`) and `at`'s exact format (ISO-8601 string, confirmed lexicographically sortable) before writing the filter logic.

- [ ] **Step 2: Add the protocol method**

```python
class AuditLogPort(Protocol):
    async def append(self, entry: AuditEntry) -> None: ...
    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]: ...
    async def list_filtered(
        self,
        *,
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]: ...
```

- [ ] **Step 3: Write the failing tests**

```python
# extend test_audit_log.py — parametrize across both InMemoryAuditLog and
# FirestoreAuditLog if the file already has a shared-fixture pattern for
# running the same test against both adapters; otherwise duplicate per-class
# matching the file's existing convention.

async def test_list_filtered_no_filters_returns_all_entries_newest_first(store):
    await store.append(AuditEntry(ticket_id="1", session_id="s1", actor="alice", from_state="open", to_state="pending", at="2026-08-01T10:00:00Z", remark=""))
    await store.append(AuditEntry(ticket_id="2", session_id="s2", actor="bob", from_state="open", to_state="pending", at="2026-08-01T11:00:00Z", remark=""))
    results = await store.list_filtered()
    assert [r.actor for r in results] == ["bob", "alice"]


async def test_list_filtered_by_actor(store):
    await store.append(AuditEntry(ticket_id="1", session_id="s1", actor="alice", from_state="open", to_state="pending", at="2026-08-01T10:00:00Z", remark=""))
    await store.append(AuditEntry(ticket_id="2", session_id="s2", actor="bob", from_state="open", to_state="pending", at="2026-08-01T11:00:00Z", remark=""))
    results = await store.list_filtered(actor="alice")
    assert [r.ticket_id for r in results] == ["1"]


async def test_list_filtered_by_date_range(store):
    await store.append(AuditEntry(ticket_id="1", session_id="s1", actor="alice", from_state="open", to_state="pending", at="2026-08-01T10:00:00Z", remark=""))
    await store.append(AuditEntry(ticket_id="2", session_id="s2", actor="bob", from_state="open", to_state="pending", at="2026-08-02T11:00:00Z", remark=""))
    results = await store.list_filtered(from_ts="2026-08-02T00:00:00Z")
    assert [r.ticket_id for r in results] == ["2"]


async def test_list_filtered_respects_limit(store):
    for i in range(5):
        await store.append(AuditEntry(ticket_id=str(i), session_id=f"s{i}", actor="alice", from_state="open", to_state="pending", at=f"2026-08-01T1{i}:00:00Z", remark=""))
    results = await store.list_filtered(limit=2)
    assert len(results) == 2
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/adapters/test_audit_log.py -v -k list_filtered`
Expected: FAIL (`list_filtered` not implemented).

- [ ] **Step 5: Implement on `InMemoryAuditLog`**

```python
    async def list_filtered(
        self,
        *,
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        all_entries = [e for entries in self._by_ticket.values() for e in entries]
        if actor is not None:
            all_entries = [e for e in all_entries if e.actor == actor]
        if from_ts is not None:
            all_entries = [e for e in all_entries if e.at >= from_ts]
        if to_ts is not None:
            all_entries = [e for e in all_entries if e.at <= to_ts]
        all_entries.sort(key=lambda e: e.at, reverse=True)
        return all_entries[:limit]
```

(Match `InMemoryAuditLog`'s actual internal attribute name from Step 1's read — `_by_ticket` is illustrative if the real field is named differently.)

- [ ] **Step 6: Implement on `FirestoreAuditLog`**

```python
    async def list_filtered(
        self,
        *,
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        query = self._collection  # match the real attribute name from Step 1's read
        if actor is not None:
            query = query.where("actor", "==", actor)
        if from_ts is not None:
            query = query.where("at", ">=", from_ts)
        if to_ts is not None:
            query = query.where("at", "<=", to_ts)
        docs = list(query.stream())
        entries = [self._to_entry(d) for d in docs]  # reuse whatever doc->AuditEntry
                                                       # conversion list_for_ticket already uses
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries[:limit]
```

If `FirestoreAuditLog`'s doc-to-`AuditEntry` conversion is inlined in `list_for_ticket` rather than factored into a helper, extract it into a small private method (`_to_entry`) shared by both `list_for_ticket` and `list_filtered` — do not duplicate the field-mapping logic.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/adapters/test_audit_log.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/ports.py src/chatbot/features/chat/adapters/audit_log.py src/chatbot/features/chat/adapters/test_audit_log.py
git commit -m "feat(audit): add list_filtered to AuditLogPort for a cross-ticket admin view"
```

---

### Task 6: Audit list/filter endpoint + router + `main.py` wiring

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/audit_router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py` (mount near the RBAC block; reuses the existing `audit_log` local variable built at ~line 367)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_audit_router.py`

**Interfaces:**
- Consumes: Task 5's `AuditLogPort.list_filtered`, Phase 1's `require_permission`.
- Produces: `build_audit_router(audit: AuditLogPort, authz_repo: AuthzRepository, validator: TokenValidator, settings: Settings) -> APIRouter`, endpoint `GET /admin/audit?actor=&from_ts=&to_ts=&limit=`, gated `audit.view`.

- [ ] **Step 1: Write the router**

```python
"""Cross-ticket audit list/filter API — the existing GET /cases/{id}/audit
route (ChatRouter) is per-case only; this adds a global admin view. Gated
behind the `audit.view` permission via Phase 1's require_permission."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.ports import AuditLogPort
    from chatbot.platform.config import Settings


def build_audit_router(
    audit: AuditLogPort,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["audit"])
    view_audit = require_permission(
        "audit.view", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/audit", dependencies=[Depends(view_audit)])
    async def list_audit(
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> dict:
        rows = await audit.list_filtered(actor=actor, from_ts=from_ts, to_ts=to_ts, limit=limit)
        return {"audit": [asdict(r) for r in rows]}

    return router
```

- [ ] **Step 2: Write the tests**

Same `test_deps.py`-style harness as Task 4. Cases: unpermitted → 403; permitted + no filters → 200 with all entries; permitted + `actor=` filter → filtered subset; RBAC-disabled → shared-secret fallback works.

- [ ] **Step 3: Wire into `main.py`**

`audit_log` is already built at ~line 367 (`audit_log = build_audit_log(settings)`), before the RBAC block (~483). Inside the RBAC block, after the existing `authz_repo`/`authz_validator` are constructed, mount the new router:

```python
        app.include_router(build_authz_router(authz_repo, authz_validator, settings))
        app.state.authz_engine = authz_engine
        app.state.authz_repo = authz_repo

        from chatbot.features.chat.audit_router import build_audit_router

        app.include_router(build_audit_router(audit_log, authz_repo, authz_validator, settings))

        sla_policy_engine = build_sla_policy_engine(settings.rbac_database_url)
        ...  # Task 4's block continues
```

- [ ] **Step 4: Run tests**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_audit_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/audit_router.py src/chatbot/features/chat/test_audit_router.py src/chatbot/main.py
git commit -m "feat(audit): add audit.view-gated cross-ticket audit list/filter endpoint"
```

---

### Task 7: Roles & Permissions — repository extensions

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/repository.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_repository.py` (extend)

**Interfaces:**
- Consumes: existing `Role`, `Permission`, `RolePermission`, `UserRole` models (unchanged schema — no migration needed).
- Produces (for Task 8): `list_permissions() -> list[Permission]`, `role_permissions(role_id: str) -> set[str]`, `revoke_permission(role_id: str, permission_key: str) -> None`, `users_for_role(role_id: str) -> list[int]`, `unassign_role(chatwoot_user_id: int, role_id: str) -> None`.

- [ ] **Step 1: Add the methods**

Append to `AuthzRepository`, matching the file's existing style exactly (plain `async with self._sm() as session:` blocks, no ORM relationship traversal):

```python
    async def list_permissions(self) -> list[Permission]:
        async with self._sm() as session:
            return list((await session.execute(select(Permission))).scalars().all())

    async def role_permissions(self, role_id: str) -> set[str]:
        async with self._sm() as session:
            rows = await session.execute(
                select(RolePermission.permission_key).where(RolePermission.role_id == role_id)
            )
            return {r[0] for r in rows.all()}

    async def revoke_permission(self, role_id: str, permission_key: str) -> None:
        async with self._sm() as session:
            existing = await session.get(RolePermission, (role_id, permission_key))
            if existing is None:
                return
            await session.delete(existing)
            await session.commit()

    async def users_for_role(self, role_id: str) -> list[int]:
        async with self._sm() as session:
            rows = await session.execute(
                select(UserRole.chatwoot_user_id).where(UserRole.role_id == role_id)
            )
            return [r[0] for r in rows.all()]

    async def unassign_role(self, chatwoot_user_id: int, role_id: str) -> None:
        async with self._sm() as session:
            existing = await session.get(UserRole, (chatwoot_user_id, role_id))
            if existing is None:
                return
            await session.delete(existing)
            await session.commit()
```

- [ ] **Step 2: Write the tests**

```python
# extend test_repository.py

async def test_list_permissions_includes_seeded_registry(repo_seeded):
    perms = await repo_seeded.list_permissions()
    keys = {p.key for p in perms}
    assert "sla.manage" in keys
    assert "audit.view" in keys


async def test_role_permissions_returns_granted_set(repo_seeded):
    perms = await repo_seeded.role_permissions("administrator")
    assert "roles.manage" in perms


async def test_revoke_permission_removes_grant(repo_seeded):
    await repo_seeded.revoke_permission("administrator", "audit.view")
    perms = await repo_seeded.role_permissions("administrator")
    assert "audit.view" not in perms


async def test_revoke_permission_absent_grant_is_noop(repo_seeded):
    await repo_seeded.revoke_permission("agent", "roles.manage")  # never granted
    # no exception


async def test_users_for_role_empty_by_default(repo_seeded):
    assert await repo_seeded.users_for_role("administrator") == []


async def test_assign_then_users_for_role(repo_seeded):
    await repo_seeded.assign_role(101, "administrator")
    assert await repo_seeded.users_for_role("administrator") == [101]


async def test_unassign_role_removes_assignment(repo_seeded):
    await repo_seeded.assign_role(101, "administrator")
    await repo_seeded.unassign_role(101, "administrator")
    assert await repo_seeded.users_for_role("administrator") == []


async def test_unassign_role_absent_assignment_is_noop(repo_seeded):
    await repo_seeded.unassign_role(999, "administrator")  # never assigned
    # no exception
```

(`repo_seeded` — reuse whatever fixture `test_repository.py` or `test_deps.py` already uses to build a repository with `seed_defaults` already run; if none exists at this granularity, build one inline per-test the same way the file's existing tests do.)

- [ ] **Step 3: Run tests**

Run: `cd backend/apps/backend && pytest src/chatbot/features/authz/test_repository.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/authz/repository.py src/chatbot/features/authz/test_repository.py
git commit -m "feat(authz): add repository methods for permission grant/revoke and role-user unassignment"
```

---

### Task 8: Roles & Permissions — router extensions

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/router.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_router.py` (extend)

**Interfaces:**
- Consumes: Task 7's repository methods.
- Produces: new endpoints on the existing `/authz` router (all `roles.manage`-gated, matching the existing `POST /authz/roles` pattern):
  - `GET /authz/permission-registry` → `{"permissions": [{"key": ..., "description": ...}]}`
  - `GET /authz/roles/{role_id}/permissions` → `{"permissions": [...]}`
  - `POST /authz/roles/{role_id}/permissions` body `{"permission_key": str}` → `{"ok": true}`
  - `DELETE /authz/roles/{role_id}/permissions/{permission_key}` → `{"ok": true}`
  - `GET /authz/roles/{role_id}/users` → `{"chatwoot_user_ids": [...]}`
  - `DELETE /authz/roles/{role_id}/assign` body `{"chatwoot_user_id": int}` → `{"ok": true}`

- [ ] **Step 1: Add the endpoints**

Append inside `build_authz_router`, after the existing `manage_roles = require_permission(...)` line and the existing `POST /roles/{role_id}/assign` endpoint:

```python
    @router.get("/permission-registry", dependencies=[Depends(manage_roles)])
    async def permission_registry() -> dict:
        perms = await repo.list_permissions()
        return {"permissions": [{"key": p.key, "description": p.description} for p in perms]}

    @router.get("/roles/{role_id}/permissions", dependencies=[Depends(manage_roles)])
    async def role_permissions(role_id: str) -> dict:
        perms = await repo.role_permissions(role_id)
        return {"permissions": sorted(perms)}

    class GrantPermissionBody(BaseModel):
        permission_key: str

    @router.post("/roles/{role_id}/permissions", dependencies=[Depends(manage_roles)])
    async def grant_role_permission(role_id: str, body: GrantPermissionBody) -> dict:
        await repo.grant_permission(role_id, body.permission_key)
        return {"ok": True}

    @router.delete(
        "/roles/{role_id}/permissions/{permission_key}", dependencies=[Depends(manage_roles)]
    )
    async def revoke_role_permission(role_id: str, permission_key: str) -> dict:
        await repo.revoke_permission(role_id, permission_key)
        return {"ok": True}

    @router.get("/roles/{role_id}/users", dependencies=[Depends(manage_roles)])
    async def role_users(role_id: str) -> dict:
        user_ids = await repo.users_for_role(role_id)
        return {"chatwoot_user_ids": user_ids}

    @router.delete("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def unassign_role(role_id: str, body: AssignRoleBody) -> dict:
        await repo.unassign_role(body.chatwoot_user_id, role_id)
        return {"ok": True}
```

(`GrantPermissionBody` can be moved up next to the file's existing `CreateRoleBody`/`AssignRoleBody` module-level `BaseModel`s instead of nested inside the function — match whichever placement the file's existing two models use.)

- [ ] **Step 2: Write the tests**

Extend `test_router.py` with cases for each new endpoint: permitted CRUD roundtrip, unpermitted → 403, and one case confirming `DELETE .../assign` actually removes access (`repo.permissions_for_user` no longer includes the role's permissions after unassignment, if such a helper test already exists for the analogous `assign` case — mirror it).

- [ ] **Step 3: Run tests**

Run: `cd backend/apps/backend && pytest src/chatbot/features/authz/test_router.py -v`
Expected: PASS.

- [ ] **Step 4: Run the full authz + sla + audit suite together**

Run: `cd backend/apps/backend && pytest src/chatbot/features/authz/ src/chatbot/features/chat/test_sla*.py src/chatbot/features/chat/test_audit_router.py src/chatbot/features/chat/adapters/test_audit_log.py -v`
Expected: PASS — confirms Tasks 2-8 integrate cleanly.

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/authz/router.py src/chatbot/features/authz/test_router.py
git commit -m "feat(authz): add permission-registry, role-permission grant/revoke, and role-user CRUD endpoints"
```

---

### Task 9: Fork patch `0025` — shared authz-aware request helper + "SLA Policies" admin page

**Files:**
- Create: `deploy/chatwoot-fork/patches/0025-sla-policies-admin.patch`

**Interfaces:**
- Consumes: Task 4's `/admin/sla-policy/*` endpoints, the existing `/authz/permissions` endpoint (Phase 1), the existing `listInboxes()` from `protonKnowledge.js` (Task 17's `/kb/inboxes`, already shipped — reused here purely for its `{inbox_id, name}` fields, no new backend dependency).
- Produces: `dashboard/api/protonAdmin.js` (new — `adminRequest` helper + SLA-policy calls), `dashboard/composables/useProtonPermissions.js` (new — fetches `/authz/permissions` once, exposes `hasPermission(key)`), `dashboard/views/ProtonSlaPoliciesPage.vue` (new), a new route + sidebar entry gated on `sla.manage`.

- [ ] **Step 1: Set up the local dev loop**

Follow `deploy/chatwoot-fork/README.md`'s "Local dev loop" section exactly:

```bash
VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION)
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-dev
cd /tmp/proton-chatwoot-dev
for p in /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/[0-9]*.patch; do
  git apply --whitespace=fix "$p"
done
```

Verify all 24 existing patches applied cleanly (no errors printed) before writing any new code — a failure here means the base image drifted and must be investigated before adding `0025`, not worked around.

- [ ] **Step 2: Write `dashboard/api/protonAdmin.js`**

```js
// protonAdmin.js — OUR file. Shared request helper + API calls for the
// three RBAC-gated admin pages (SLA Policies, Audit Log, Roles &
// Permissions). Unlike protonKnowledge.js's kbRequest (which only forwards
// the shared backendKey), adminRequest ALSO forwards the caller's Chatwoot
// access token, since these endpoints are gated by Phase 1's per-user
// require_permission dependency, not the shared secret alone.
import { useProtonConfig } from 'dashboard/composables/useProtonConfig';
import { useMapGetter } from 'dashboard/composables/store';
import { computed } from 'vue';

export async function adminRequest(path, { method = 'GET', body } = {}) {
  const { backendUrl, backendKey } = useProtonConfig();
  if (!backendUrl) throw new Error('PROTON_BACKEND_URL not configured');

  const currentUser = useMapGetter('getCurrentUser');
  const accessToken = currentUser.value?.access_token ?? '';

  const response = await fetch(`${backendUrl}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(backendKey ? { 'x-api-key': backendKey } : {}),
      ...(accessToken ? { 'x-chatwoot-access-token': accessToken } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText);
    const err = new Error(`${response.status}: ${text.slice(0, 200)}`);
    err.status = response.status;
    throw err;
  }
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

// ── SLA policy ─────────────────────────────────────────────────────────────

export function getSlaDefault() {
  return adminRequest('/admin/sla-policy/default');
}

export function putSlaDefault(body) {
  return adminRequest('/admin/sla-policy/default', { method: 'PUT', body });
}

export function getSlaForInbox(inboxId) {
  return adminRequest(`/admin/sla-policy/inbox/${encodeURIComponent(inboxId)}`);
}

export function putSlaForInbox(inboxId, body) {
  return adminRequest(`/admin/sla-policy/inbox/${encodeURIComponent(inboxId)}`, {
    method: 'PUT',
    body,
  });
}

// ── My permissions (for nav/page gating) ────────────────────────────────────

export async function myPermissions() {
  const data = await adminRequest('/authz/permissions');
  return Array.isArray(data.permissions) ? data.permissions : [];
}
```

- [ ] **Step 3: Write `dashboard/composables/useProtonPermissions.js`**

```js
// useProtonPermissions.js — OUR file. Fetches the current user's RBAC
// permission set once (module-level cache, shared across every component
// that imports this) and exposes a reactive hasPermission(key) check for
// nav/page-visibility gating. This is UX-only — the backend's
// require_permission dependency is the actual security boundary; a hidden
// nav item does not replace server-side enforcement.
import { ref, computed } from 'vue';
import { myPermissions } from 'dashboard/api/protonAdmin';

const permissions = ref(null); // null = not yet loaded
const loading = ref(false);
let loadPromise = null;

async function ensureLoaded() {
  if (permissions.value !== null || loadPromise) return loadPromise;
  loading.value = true;
  loadPromise = myPermissions()
    .then(perms => {
      permissions.value = perms;
    })
    .catch(() => {
      permissions.value = []; // fail closed for nav visibility on error
    })
    .finally(() => {
      loading.value = false;
    });
  return loadPromise;
}

export function useProtonPermissions() {
  ensureLoaded();
  return {
    loading,
    hasPermission: key => (permissions.value || []).includes(key),
  };
}
```

- [ ] **Step 4: Write `dashboard/views/ProtonSlaPoliciesPage.vue`**

```vue
<!-- ProtonSlaPoliciesPage.vue — OUR file. Top-level admin page: edit the
     tenant-default SLA policy or a specific inbox's override. An inbox
     picker (plus "Tenant default") selects which row is being edited;
     unset fields on an inbox row inherit from the tenant default rather
     than requiring every field to be re-entered per inbox (shown via
     placeholder text, not auto-filled values, so "unset" stays distinguishable
     from "explicitly set to the same value"). -->
<script>
import { useAlert } from 'dashboard/composables';
import { listInboxes } from 'dashboard/api/protonKnowledge';
import {
  getSlaDefault,
  putSlaDefault,
  getSlaForInbox,
  putSlaForInbox,
} from 'dashboard/api/protonAdmin';

const EMPTY_FORM = {
  response_hours: null,
  resolution_hours: null,
  ack_minutes_by_channel_json: null,
  pic_whatsapp: null,
  engine_enabled: null,
};

export default {
  name: 'ProtonSlaPoliciesPage',
  data() {
    return {
      inboxes: [],
      selectedInboxId: '', // '' = tenant default
      form: { ...EMPTY_FORM },
      loading: true,
      saving: false,
      errored: false,
    };
  },
  async mounted() {
    await this.loadInboxes();
    await this.loadPolicy();
  },
  methods: {
    async loadInboxes() {
      try {
        const rows = await listInboxes();
        this.inboxes = rows.map(r => ({ inbox_id: r.inbox_id, name: r.name || String(r.inbox_id) }));
      } catch (err) {
        useAlert('Failed to load inbox list: ' + (err.message || String(err)));
      }
    },
    async loadPolicy() {
      this.loading = true;
      this.errored = false;
      try {
        const data = this.selectedInboxId
          ? await getSlaForInbox(this.selectedInboxId)
          : await getSlaDefault();
        this.form = { ...EMPTY_FORM, ...data };
      } catch (err) {
        this.errored = true;
        useAlert('Failed to load SLA policy: ' + (err.message || String(err)));
      } finally {
        this.loading = false;
      }
    },
    async onSelectInbox() {
      await this.loadPolicy();
    },
    async save() {
      this.saving = true;
      try {
        if (this.selectedInboxId) {
          await putSlaForInbox(this.selectedInboxId, this.form);
        } else {
          await putSlaDefault(this.form);
        }
        useAlert('SLA policy saved.');
      } catch (err) {
        useAlert('Failed to save SLA policy: ' + (err.message || String(err)));
      } finally {
        this.saving = false;
      }
    },
  },
};
</script>

<template>
  <div class="p-6">
    <h1 class="mb-1 text-lg font-medium text-n-slate-12">SLA Policies</h1>
    <p class="mb-6 text-sm text-n-slate-11">
      Edit the tenant-wide default, or override it for a specific inbox. Unset fields inherit from
      the tenant default.
    </p>

    <div class="mb-4">
      <label class="block mb-1 text-sm font-medium text-n-slate-12">Scope</label>
      <select
        v-model="selectedInboxId"
        class="w-64 px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        @change="onSelectInbox"
      >
        <option value="">Tenant default</option>
        <option v-for="ib in inboxes" :key="ib.inbox_id" :value="ib.inbox_id">{{ ib.name }}</option>
      </select>
    </div>

    <div v-if="loading" class="py-10 text-sm text-center text-n-slate-11">Loading…</div>
    <div v-else-if="errored" class="py-10 text-sm text-center text-n-slate-11">
      Could not load this policy.
    </div>
    <form v-else class="grid max-w-lg grid-cols-1 gap-4" @submit.prevent="save">
      <div>
        <label class="block mb-1 text-sm text-n-slate-11">Response window (hours)</label>
        <input
          v-model.number="form.response_hours"
          type="number"
          step="0.5"
          class="w-full px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        />
      </div>
      <div>
        <label class="block mb-1 text-sm text-n-slate-11">Resolution window (hours)</label>
        <input
          v-model.number="form.resolution_hours"
          type="number"
          step="0.5"
          class="w-full px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        />
      </div>
      <div>
        <label class="block mb-1 text-sm text-n-slate-11">Per-channel ACK minutes (JSON)</label>
        <input
          v-model="form.ack_minutes_by_channel_json"
          type="text"
          placeholder='{"whatsapp": 15}'
          class="w-full px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        />
      </div>
      <div>
        <label class="block mb-1 text-sm text-n-slate-11">PIC WhatsApp number</label>
        <input
          v-model="form.pic_whatsapp"
          type="text"
          class="w-full px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        />
      </div>
      <div class="flex items-center gap-2">
        <input v-model="form.engine_enabled" type="checkbox" />
        <label class="text-sm text-n-slate-11">Engine enabled override</label>
      </div>
      <button
        type="submit"
        :disabled="saving"
        class="px-4 py-2 text-sm font-medium rounded-lg w-fit bg-n-brand text-white hover:opacity-90 disabled:opacity-50"
      >
        Save
      </button>
    </form>
  </div>
</template>
```

- [ ] **Step 5: Register the route and nav entry**

In `dashboard.routes.js`, add (near the other `proton*` routes):

```js
{
  path: 'proton/sla-policies',
  name: 'proton_sla_policies',
  component: () => import('../../views/ProtonSlaPoliciesPage.vue'),
  meta: { permissions: ['administrator'] },
},
```

In `Sidebar.vue`'s `menuItems` computed, add a permission-gated entry (import `useProtonPermissions` at the top of the `<script setup>` block):

```js
import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';
const { hasPermission } = useProtonPermissions();
```

```js
    ...(hasPermission('sla.manage')
      ? [
          {
            name: 'SlaPolicies',
            icon: 'i-lucide-timer',
            label: 'SLA Policies',
            to: accountScopedRoute('proton_sla_policies'),
          },
        ]
      : []),
```

- [ ] **Step 6: Manual smoke test**

Follow the README's "Start local infrastructure" + "Start Chatwoot in dev mode" steps. With `PROTON_BACKEND_URL`/`PROTON_BACKEND_KEY` pointed at a locally running `backend/` (RBAC enabled, a test admin user assigned via `RBAC_BOOTSTRAP_ADMIN_USER_ID`), confirm: the "SLA Policies" nav item appears for the bootstrapped admin, the page loads the tenant-default policy (initially empty), editing and saving round-trips correctly, and switching the inbox picker loads that inbox's override.

- [ ] **Step 7: Re-export the patch and verify**

```bash
cd /tmp/proton-chatwoot-dev
git diff HEAD -- app/javascript/dashboard/api/protonAdmin.js \
                 app/javascript/dashboard/composables/useProtonPermissions.js \
                 app/javascript/dashboard/views/ProtonSlaPoliciesPage.vue \
                 app/javascript/dashboard/routes/dashboard/dashboard.routes.js \
                 app/javascript/dashboard/components-next/sidebar/Sidebar.vue \
  > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0025-sla-policies-admin.patch
```

Verify it applies standalone on a fresh `0001`-`0024`-applied clone:

```bash
rm -rf /tmp/proton-chatwoot-verify
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-verify
cd /tmp/proton-chatwoot-verify
for p in /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/000{1..9}-*.patch \
         /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/00{10..25}-*.patch; do
  git apply --whitespace=fix "$p" || { echo "FAILED: $p"; exit 1; }
done
echo "All patches 0001-0025 applied cleanly"
```

- [ ] **Step 8: Commit**

```bash
git add deploy/chatwoot-fork/patches/0025-sla-policies-admin.patch
git commit -m "feat(chatwoot-fork): add sla.manage-gated 'SLA Policies' admin page"
```

---

### Task 10: Fork patch `0026` — "Audit Log" viewer page

**Files:**
- Create: `deploy/chatwoot-fork/patches/0026-audit-log-admin.patch`

**Interfaces:**
- Consumes: Task 6's `/admin/audit` endpoint, `adminRequest`/`useProtonPermissions` from Task 9's patch (already applied at this point in the chain — `0026` is written against a tree with `0025` present).
- Produces: `dashboard/views/ProtonAuditLogPage.vue` (new), an `adminRequest`-based `listAudit(filters)` call added to `protonAdmin.js`, a new route + sidebar entry gated on `audit.view`.

- [ ] **Step 1: Clone and apply `0001`-`0025`**

```bash
VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION)
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-dev-2
cd /tmp/proton-chatwoot-dev-2
for p in /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/000{1..9}-*.patch \
         /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/00{10..25}-*.patch; do
  git apply --whitespace=fix "$p"
done
```

- [ ] **Step 2: Add `listAudit` to `protonAdmin.js`**

```js
// ── Audit log ─────────────────────────────────────────────────────────────

// GET /admin/audit?actor=&from_ts=&to_ts=&limit= -> { audit: [{ ticket_id,
//   session_id, actor, from_state, to_state, at, remark }] }
export function listAudit({ actor, fromTs, toTs, limit } = {}) {
  const params = new URLSearchParams();
  if (actor) params.set('actor', actor);
  if (fromTs) params.set('from_ts', fromTs);
  if (toTs) params.set('to_ts', toTs);
  if (limit) params.set('limit', String(limit));
  const qs = params.toString();
  return adminRequest(`/admin/audit${qs ? `?${qs}` : ''}`);
}
```

- [ ] **Step 3: Write `dashboard/views/ProtonAuditLogPage.vue`**

```vue
<!-- ProtonAuditLogPage.vue — OUR file. Filterable, read-only viewer over
     the backend's cross-ticket audit trail (Firestore case_audit_log).
     Filters: actor, date range. Storage is unchanged — this only adds a
     list/filter view on top of the existing per-case audit data. -->
<script>
import { useAlert } from 'dashboard/composables';
import { listAudit } from 'dashboard/api/protonAdmin';

export default {
  name: 'ProtonAuditLogPage',
  data() {
    return {
      rows: [],
      actorFilter: '',
      fromFilter: '',
      toFilter: '',
      loading: true,
      errored: false,
    };
  },
  async mounted() {
    await this.load();
  },
  methods: {
    async load() {
      this.loading = true;
      this.errored = false;
      try {
        const data = await listAudit({
          actor: this.actorFilter || undefined,
          fromTs: this.fromFilter ? new Date(this.fromFilter).toISOString() : undefined,
          toTs: this.toFilter ? new Date(this.toFilter).toISOString() : undefined,
        });
        this.rows = Array.isArray(data.audit) ? data.audit : [];
      } catch (err) {
        this.errored = true;
        useAlert('Failed to load audit log: ' + (err.message || String(err)));
      } finally {
        this.loading = false;
      }
    },
  },
};
</script>

<template>
  <div class="p-6">
    <h1 class="mb-1 text-lg font-medium text-n-slate-12">Audit Log</h1>
    <p class="mb-6 text-sm text-n-slate-11">Cross-ticket case state changes, newest first.</p>

    <div class="flex flex-wrap items-end gap-3 mb-4">
      <div>
        <label class="block mb-1 text-xs text-n-slate-11">Actor</label>
        <input
          v-model="actorFilter"
          type="text"
          class="w-40 px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        />
      </div>
      <div>
        <label class="block mb-1 text-xs text-n-slate-11">From</label>
        <input
          v-model="fromFilter"
          type="date"
          class="px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        />
      </div>
      <div>
        <label class="block mb-1 text-xs text-n-slate-11">To</label>
        <input
          v-model="toFilter"
          type="date"
          class="px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
        />
      </div>
      <button
        type="button"
        class="px-3 py-2 text-sm font-medium rounded-lg bg-n-brand text-white hover:opacity-90"
        @click="load"
      >
        Filter
      </button>
    </div>

    <div v-if="loading" class="py-10 text-sm text-center text-n-slate-11">Loading…</div>
    <div v-else-if="errored" class="py-10 text-sm text-center text-n-slate-11">
      Could not load the audit log.
    </div>
    <div v-else-if="!rows.length" class="py-10 text-sm text-center text-n-slate-11">
      No entries match this filter.
    </div>
    <table v-else class="min-w-full table-auto outline outline-1 -outline-offset-1 outline-n-weak rounded-xl">
      <thead>
        <tr class="border-b border-n-weak">
          <th class="py-3 px-4 text-start text-heading-3 text-n-slate-12">At</th>
          <th class="py-3 px-4 text-start text-heading-3 text-n-slate-12">Ticket</th>
          <th class="py-3 px-4 text-start text-heading-3 text-n-slate-12">Actor</th>
          <th class="py-3 px-4 text-start text-heading-3 text-n-slate-12">Transition</th>
          <th class="py-3 px-4 text-start text-heading-3 text-n-slate-12">Remark</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-n-weak">
        <tr v-for="(row, i) in rows" :key="i">
          <td class="py-3 px-4 text-sm text-n-slate-11 whitespace-nowrap">{{ row.at }}</td>
          <td class="py-3 px-4 text-sm text-n-slate-12 whitespace-nowrap">{{ row.ticket_id }}</td>
          <td class="py-3 px-4 text-sm text-n-slate-12 whitespace-nowrap">{{ row.actor }}</td>
          <td class="py-3 px-4 text-sm text-n-slate-11 whitespace-nowrap">
            {{ row.from_state }} → {{ row.to_state }}
          </td>
          <td class="py-3 px-4 text-sm text-n-slate-11">{{ row.remark }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
```

- [ ] **Step 4: Register route + nav entry**

`dashboard.routes.js`:

```js
{
  path: 'proton/audit-log',
  name: 'proton_audit_log',
  component: () => import('../../views/ProtonAuditLogPage.vue'),
  meta: { permissions: ['administrator'] },
},
```

`Sidebar.vue` (`useProtonPermissions` import already added by `0025` — reuse it):

```js
    ...(hasPermission('audit.view')
      ? [
          {
            name: 'AuditLog',
            icon: 'i-lucide-scroll-text',
            label: 'Audit Log',
            to: accountScopedRoute('proton_audit_log'),
          },
        ]
      : []),
```

- [ ] **Step 5: Manual smoke test**

Same dev-loop setup as Task 9. Confirm the nav item appears only for a user with `audit.view`, the table loads and renders entries, and the actor/date filters actually narrow the result set (verify against a backend seeded with a couple of `case_audit_log`/in-memory entries spanning different actors/dates).

- [ ] **Step 6: Re-export and verify**

```bash
cd /tmp/proton-chatwoot-dev-2
git diff HEAD -- app/javascript/dashboard/api/protonAdmin.js \
                 app/javascript/dashboard/views/ProtonAuditLogPage.vue \
                 app/javascript/dashboard/routes/dashboard/dashboard.routes.js \
                 app/javascript/dashboard/components-next/sidebar/Sidebar.vue \
  > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0026-audit-log-admin.patch
```

Verify `0001`-`0026` apply cleanly in sequence on a fresh clone (same pattern as Task 9 Step 7, extended one patch further).

- [ ] **Step 7: Commit**

```bash
git add deploy/chatwoot-fork/patches/0026-audit-log-admin.patch
git commit -m "feat(chatwoot-fork): add audit.view-gated 'Audit Log' viewer page"
```

---

### Task 11: Fork patch `0027` — "Roles & Permissions" admin page + documentation

**Files:**
- Create: `deploy/chatwoot-fork/patches/0027-roles-permissions-admin.patch`
- Modify: `deploy/tenants/example.env`

**Interfaces:**
- Consumes: Task 8's `/authz/roles`, `/authz/permission-registry`, `/authz/roles/{id}/permissions` (GET/POST/DELETE), `/authz/roles/{id}/users`, `/authz/roles/{id}/assign` (POST/DELETE), `adminRequest`/`useProtonPermissions` from Task 9's patch.
- Produces: `dashboard/views/ProtonRolesPermissionsPage.vue` (new), role/permission/assignment API calls added to `protonAdmin.js`, a new route + sidebar entry gated on `roles.manage`.

- [ ] **Step 1: Clone and apply `0001`-`0026`**

Same pattern as Task 10 Step 1, extended one patch further.

- [ ] **Step 2: Add role/permission/assignment calls to `protonAdmin.js`**

```js
// ── Roles & permissions ──────────────────────────────────────────────────

export async function listRoles() {
  const data = await adminRequest('/authz/roles');
  return Array.isArray(data.roles) ? data.roles : [];
}

export function createRole({ id, name, description = '' }) {
  return adminRequest('/authz/roles', { method: 'POST', body: { id, name, description } });
}

export async function permissionRegistry() {
  const data = await adminRequest('/authz/permission-registry');
  return Array.isArray(data.permissions) ? data.permissions : [];
}

export async function rolePermissions(roleId) {
  const data = await adminRequest(`/authz/roles/${encodeURIComponent(roleId)}/permissions`);
  return Array.isArray(data.permissions) ? data.permissions : [];
}

export function grantRolePermission(roleId, permissionKey) {
  return adminRequest(`/authz/roles/${encodeURIComponent(roleId)}/permissions`, {
    method: 'POST',
    body: { permission_key: permissionKey },
  });
}

export function revokeRolePermission(roleId, permissionKey) {
  return adminRequest(
    `/authz/roles/${encodeURIComponent(roleId)}/permissions/${encodeURIComponent(permissionKey)}`,
    { method: 'DELETE' }
  );
}

export async function roleUsers(roleId) {
  const data = await adminRequest(`/authz/roles/${encodeURIComponent(roleId)}/users`);
  return Array.isArray(data.chatwoot_user_ids) ? data.chatwoot_user_ids : [];
}

export function assignRole(roleId, chatwootUserId) {
  return adminRequest(`/authz/roles/${encodeURIComponent(roleId)}/assign`, {
    method: 'POST',
    body: { chatwoot_user_id: chatwootUserId },
  });
}

export function unassignRole(roleId, chatwootUserId) {
  return adminRequest(`/authz/roles/${encodeURIComponent(roleId)}/assign`, {
    method: 'DELETE',
    body: { chatwoot_user_id: chatwootUserId },
  });
}
```

- [ ] **Step 3: Write `dashboard/views/ProtonRolesPermissionsPage.vue`**

```vue
<!-- ProtonRolesPermissionsPage.vue — OUR file. Roles & Permissions admin:
     pick a role, toggle which permissions it grants (from the fixed
     backend-defined registry), and manage which Chatwoot user ids are
     assigned to it. Replaces Chatwoot's enterprise-licensed custom_roles
     page. -->
<script>
import { useAlert } from 'dashboard/composables';
import {
  listRoles,
  createRole,
  permissionRegistry,
  rolePermissions,
  grantRolePermission,
  revokeRolePermission,
  roleUsers,
  assignRole,
  unassignRole,
} from 'dashboard/api/protonAdmin';

export default {
  name: 'ProtonRolesPermissionsPage',
  data() {
    return {
      roles: [],
      registry: [],
      selectedRoleId: '',
      selectedRolePermissions: [],
      selectedRoleUsers: [],
      newRoleId: '',
      newRoleName: '',
      newUserId: '',
      loading: true,
      errored: false,
    };
  },
  async mounted() {
    await this.loadRolesAndRegistry();
  },
  methods: {
    async loadRolesAndRegistry() {
      this.loading = true;
      this.errored = false;
      try {
        const [roles, registry] = await Promise.all([listRoles(), permissionRegistry()]);
        this.roles = roles;
        this.registry = registry;
        if (roles.length && !this.selectedRoleId) {
          this.selectedRoleId = roles[0].id;
          await this.loadRoleDetail();
        }
      } catch (err) {
        this.errored = true;
        useAlert('Failed to load roles: ' + (err.message || String(err)));
      } finally {
        this.loading = false;
      }
    },
    async onSelectRole() {
      await this.loadRoleDetail();
    },
    async loadRoleDetail() {
      if (!this.selectedRoleId) return;
      try {
        const [perms, users] = await Promise.all([
          rolePermissions(this.selectedRoleId),
          roleUsers(this.selectedRoleId),
        ]);
        this.selectedRolePermissions = perms;
        this.selectedRoleUsers = users;
      } catch (err) {
        useAlert('Failed to load role detail: ' + (err.message || String(err)));
      }
    },
    async togglePermission(key) {
      const has = this.selectedRolePermissions.includes(key);
      try {
        if (has) {
          await revokeRolePermission(this.selectedRoleId, key);
        } else {
          await grantRolePermission(this.selectedRoleId, key);
        }
        await this.loadRoleDetail();
      } catch (err) {
        useAlert('Failed to update permission: ' + (err.message || String(err)));
      }
    },
    async addUser() {
      const userId = Number(this.newUserId);
      if (!userId) {
        useAlert('Enter a valid Chatwoot user id.');
        return;
      }
      try {
        await assignRole(this.selectedRoleId, userId);
        this.newUserId = '';
        await this.loadRoleDetail();
      } catch (err) {
        useAlert('Failed to assign user: ' + (err.message || String(err)));
      }
    },
    async removeUser(userId) {
      try {
        await unassignRole(this.selectedRoleId, userId);
        await this.loadRoleDetail();
      } catch (err) {
        useAlert('Failed to unassign user: ' + (err.message || String(err)));
      }
    },
    async addRole() {
      if (!this.newRoleId || !this.newRoleName) {
        useAlert('Role id and name are required.');
        return;
      }
      try {
        await createRole({ id: this.newRoleId, name: this.newRoleName });
        this.newRoleId = '';
        this.newRoleName = '';
        await this.loadRolesAndRegistry();
        useAlert('Role created.');
      } catch (err) {
        useAlert('Failed to create role: ' + (err.message || String(err)));
      }
    },
  },
};
</script>

<template>
  <div class="p-6">
    <h1 class="mb-1 text-lg font-medium text-n-slate-12">Roles & Permissions</h1>
    <p class="mb-6 text-sm text-n-slate-11">
      Manage roles, the permissions each role grants, and which users hold each role.
    </p>

    <div v-if="loading" class="py-10 text-sm text-center text-n-slate-11">Loading…</div>
    <div v-else-if="errored" class="py-10 text-sm text-center text-n-slate-11">
      Could not load roles.
    </div>
    <div v-else class="grid grid-cols-1 gap-8 lg:grid-cols-2">
      <div>
        <label class="block mb-1 text-sm font-medium text-n-slate-12">Role</label>
        <select
          v-model="selectedRoleId"
          class="w-full px-3 py-2 mb-4 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
          @change="onSelectRole"
        >
          <option v-for="r in roles" :key="r.id" :value="r.id">{{ r.name }}</option>
        </select>

        <div class="flex items-end gap-2 mb-6">
          <div>
            <label class="block mb-1 text-xs text-n-slate-11">New role id</label>
            <input
              v-model="newRoleId"
              type="text"
              class="w-32 px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
            />
          </div>
          <div>
            <label class="block mb-1 text-xs text-n-slate-11">Name</label>
            <input
              v-model="newRoleName"
              type="text"
              class="w-40 px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
            />
          </div>
          <button
            type="button"
            class="px-3 py-2 text-sm font-medium rounded-lg bg-n-brand text-white hover:opacity-90"
            @click="addRole"
          >
            Create role
          </button>
        </div>

        <h2 class="mb-2 text-sm font-medium text-n-slate-12">Permissions</h2>
        <div class="flex flex-col gap-2">
          <label v-for="p in registry" :key="p.key" class="flex items-center gap-2 text-sm text-n-slate-11">
            <input
              type="checkbox"
              :checked="selectedRolePermissions.includes(p.key)"
              @change="togglePermission(p.key)"
            />
            {{ p.key }} — {{ p.description }}
          </label>
        </div>
      </div>

      <div>
        <h2 class="mb-2 text-sm font-medium text-n-slate-12">Assigned users (Chatwoot user id)</h2>
        <div class="flex items-end gap-2 mb-4">
          <input
            v-model="newUserId"
            type="text"
            placeholder="e.g. 101"
            class="w-40 px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12"
          />
          <button
            type="button"
            class="px-3 py-2 text-sm font-medium rounded-lg bg-n-brand text-white hover:opacity-90"
            @click="addUser"
          >
            Assign
          </button>
        </div>
        <ul class="flex flex-col gap-2">
          <li
            v-for="userId in selectedRoleUsers"
            :key="userId"
            class="flex items-center justify-between px-3 py-2 text-sm border rounded-lg border-n-weak text-n-slate-12"
          >
            {{ userId }}
            <button type="button" class="text-xs text-n-ruby-9" @click="removeUser(userId)">
              Remove
            </button>
          </li>
          <li v-if="!selectedRoleUsers.length" class="text-sm text-n-slate-11">No users assigned.</li>
        </ul>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 4: Register route + nav entry**

`dashboard.routes.js`:

```js
{
  path: 'proton/roles-permissions',
  name: 'proton_roles_permissions',
  component: () => import('../../views/ProtonRolesPermissionsPage.vue'),
  meta: { permissions: ['administrator'] },
},
```

`Sidebar.vue`:

```js
    ...(hasPermission('roles.manage')
      ? [
          {
            name: 'RolesPermissions',
            icon: 'i-lucide-shield-check',
            label: 'Roles & Permissions',
            to: accountScopedRoute('proton_roles_permissions'),
          },
        ]
      : []),
```

- [ ] **Step 5: Manual smoke test**

Confirm: nav item visible only with `roles.manage`; selecting a role loads its permissions/users; toggling a permission checkbox persists (reload the page, confirm state survived); assigning/unassigning a user id round-trips; creating a new role appears in the dropdown immediately.

- [ ] **Step 6: Re-export and verify**

```bash
cd /tmp/proton-chatwoot-dev-3   # or continue in the same clone used for Steps 1-5
git diff HEAD -- app/javascript/dashboard/api/protonAdmin.js \
                 app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue \
                 app/javascript/dashboard/routes/dashboard/dashboard.routes.js \
                 app/javascript/dashboard/components-next/sidebar/Sidebar.vue \
  > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0027-roles-permissions-admin.patch
```

Verify `0001`-`0027` apply cleanly in full sequence on one final fresh clone — this is the definitive check that Tasks 9-11 compose correctly together, not just pairwise:

```bash
rm -rf /tmp/proton-chatwoot-final-verify
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-final-verify
cd /tmp/proton-chatwoot-final-verify
for p in /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/[0-9]*.patch; do
  git apply --whitespace=fix "$p" || { echo "FAILED: $p"; exit 1; }
done
echo "All patches 0001-0027 applied cleanly"
```

- [ ] **Step 7: Document in `example.env`**

Add a short note near the existing `RBAC_ENABLED`/`RBAC_DATABASE_URL` documentation (if that section exists yet from Phase 1's Task 6 — check first):

```
# The "SLA Policies", "Audit Log", and "Roles & Permissions" admin pages
# (Chatwoot fork patches 0025-0027) all depend on RBAC_ENABLED=true and
# RBAC_DATABASE_URL being set — they reuse the same Postgres connection and
# the same require_permission enforcement Phase 1 added. Without both set,
# these pages are neither mounted server-side nor shown in the nav.
```

- [ ] **Step 8: Commit**

```bash
git add deploy/chatwoot-fork/patches/0027-roles-permissions-admin.patch deploy/tenants/example.env
git commit -m "feat(chatwoot-fork): add roles.manage-gated 'Roles & Permissions' admin page; document Phase 2 UI dependency on RBAC_ENABLED"
```

---

## Plan Self-Review Notes

- **Spec coverage:** Feature 1 (SLA wiring+UI) → Tasks 2-4, 9. Feature 2 (Audit viewer) → Tasks 5-6, 10. Feature 3's Phase-2-relevant slice (role-admin UI, permission CRUD backing it — the `/authz` API itself and enforcement dependency were Phase 1) → Tasks 7-8, 11. The flag-flip (shared root) → Task 1.
- **Explicitly out of scope, matching the spec's own phasing:** Chatwoot Pundit-policy enforcement on conversations/inboxes (Phase 3 — "heaviest, most upstream-coupled, phased last"). This plan's Task 8/11 CRUD lets an operator define custom roles and assign them, but Chatwoot's own conversation/inbox access is still governed by Chatwoot's native `administrator`/`agent` roles until Phase 3 lands the Pundit patch — worth flagging to whoever picks up Phase 3 next.
- **`sla.py`'s per-inbox `engine_enabled` override** is threaded into the policy resolution (Task 3) but Task 3's Step 4 flags that the *global* `sla_engine_enabled` gate in `start_sla_scheduler` only runs once at scheduler-start, not per-conversation — so a policy-store `engine_enabled=False` override for one inbox cannot currently skip that inbox mid-scan without restructuring `scan_conversations`'s loop to check it per-conversation and `continue`. Task 3's implementer should apply the per-conversation skip if the existing loop structure supports it cleanly (a simple `if resolved_policy and resolved_policy.engine_enabled is False: continue` at the top of the per-conv iteration is likely straightforward); if not, this is an acceptable documented limitation for Phase 2 to leave for a follow-up, not a reason to block the rest of the task.
- **Type consistency check:** `SlaPolicyValues` field names (`response_hours`, `resolution_hours`, `ack_minutes_by_channel_json`, `pic_whatsapp`, `engine_enabled`) are used identically across Tasks 2, 3, 4, and 9's `SlaPolicyBody`/form fields — verified consistent. `AuditEntry` field names (`ticket_id`, `session_id`, `actor`, `from_state`, `to_state`, `at`, `remark`) are used identically across Tasks 5, 6, and 10's table columns.
