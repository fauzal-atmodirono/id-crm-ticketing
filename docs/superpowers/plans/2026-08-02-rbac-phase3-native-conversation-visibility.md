# RBAC Phase 3 — Native Conversation-Visibility Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator restrict an agent's Chatwoot conversation visibility (all / unassigned+own / participating-only) from the existing "Roles & Permissions" admin page, by mirroring our RBAC role grants into Chatwoot's own dormant, already-shipped `CustomRole` + `account_users.custom_role_id` mechanism — zero Ruby/Pundit patches.

**Architecture:** Six new `chatwoot.*` permission keys join the existing registry (registered, not auto-granted to default roles). A user's *effective* native permission set is the most-permissive union across ALL their roles (Chatwoot supports only one `custom_role_id` per user, but our RBAC supports multiple roles per user) — so the mirror is **per-user**, not per-role: a new `user_native_role_mirror` table maps `chatwoot_user_id -> chatwoot_custom_role_id`, recomputed and pushed to Chatwoot's REST API synchronously, fail-closed, whenever a role grant/revoke or role assignment could have changed that user's resolved set.

**Tech Stack:** Same as Phases 1-2 — FastAPI, SQLAlchemy 2.0 async, httpx, pytest + respx; Vue 3 Options API fork patch.

## Plan amendment vs. the spec

`docs/superpowers/specs/2026-08-02-rbac-phase3-native-conversation-visibility-design.md`
proposed a `roles.chatwoot_custom_role_id` column (one mirrored `CustomRole`
per **role**). Working through the mechanics during planning surfaced a real
gap: because a user can hold multiple of our roles, their *effective*
resolved native-permission set (most-permissive-wins across all their roles)
is a **combination** that doesn't necessarily match any single role's own
mirror — e.g. Role A grants `conversation_unassigned_manage` only, Role B
grants `contact_manage` only; a user in both roles needs a `CustomRole` with
*both* permissions, which is neither role's own mirror.

**Fix:** mirror per **user**, not per role. A new `user_native_role_mirror`
table (`chatwoot_user_id` primary key) holds the one `CustomRole` id created
for that user, recomputed from their full resolved permission set on every
change. This is simpler than deduplicating/sharing `CustomRole` rows across
users with identical resolved sets — a user whose set becomes empty gets
their mirror row (and the Chatwoot `CustomRole`) deleted, so nothing
accumulates unboundedly. Two users with identical resolved sets get two
separate (duplicate-content) `CustomRole` rows in Chatwoot — a deliberate,
harmless simplification over reference-counted sharing.

This also removes the need for any schema change to the existing `roles`
table (no `ALTER TABLE` / migration-lite concern) — `user_native_role_mirror`
is a brand-new table, created the same way every prior table in this
codebase is: `Base.metadata.create_all`.

## Global Constraints

- **No new backend env vars for feature-enablement.** Native mirroring only
  ever runs when `settings.rbac_enabled and settings.rbac_database_url` (the
  same gate Phases 1-2 use) AND a role actually holds a `chatwoot.*`
  permission — an unconfigured tenant, or one that never grants a native key,
  never calls Chatwoot's `custom_roles`/`agents` endpoints. Byte-identical.
- **Fail-closed on the mirror.** If a Chatwoot-side call fails, the triggering
  `/authz` request returns a 502 and any local DB change from that request is
  compensating-reverted (the inverse repository call) before the error is
  raised — never leave our DB claiming a grant that Chatwoot never received.
  This mirrors Phase 1's "backend admin endpoints fail closed on authz"
  boundary, not the AI-orchestration fail-open contract used elsewhere.
- **Zero Ruby/Pundit changes.** Every new file talks to Chatwoot only via its
  existing REST API (`/custom_roles`, `PATCH /agents/{id}`) — never touches
  `enterprise/` or any other Chatwoot server code, per the parent spec's
  standing constraint.
- **Self-contained Chatwoot client, matching `features/routing/assigner.py`'s
  `RoutingAssigner` convention exactly** (own `httpx.AsyncClient`, dual
  `api_access_token`/`Api-Access-Token` headers, deferred `import httpx` to
  avoid a circular import) — do not import `ChatwootAdapter`.
- **The three `conversation_*` keys are mutually exclusive per role** —
  granting one revokes the other two on that same role, in the same request.
  This exclusivity is scoped to the new grant path only; it must not change
  `AuthzRepository.grant_permission`'s existing behavior (Phases 1-2 rely on
  it being a plain additive grant).

---

### Task 1: Register the six `chatwoot.*` permission keys (registry only, not auto-granted)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/seed.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_seed.py` (new, or extend if it already exists — check first)

**Interfaces:**
- Consumes: nothing new.
- Produces: `NATIVE_PERMISSION_REGISTRY: dict[str, str]` (the six keys + descriptions), `NATIVE_CONVERSATION_KEYS: frozenset[str]` (the three mutually-exclusive keys), `NATIVE_BOOLEAN_KEYS: frozenset[str]` (the three stacking keys), `ALL_NATIVE_KEYS: frozenset[str]`. Task 3's repository, Task 5's router, and Task 6's mirror wiring all import these constants — this is the single source of truth for the key list, do not re-declare it elsewhere.

- [ ] **Step 1: Check for an existing `test_seed.py`**

Run: `ls backend/apps/backend/src/chatbot/features/authz/test_seed.py 2>/dev/null || echo "none"`.
If it exists, read it fully first and extend it (matching its existing fixture style); if not, create it following `test_repository.py`'s sqlite `tmp_path` fixture convention.

- [ ] **Step 2: Write the failing test**

```python
# test_seed.py
import pytest

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import (
    ALL_NATIVE_KEYS,
    NATIVE_PERMISSION_REGISTRY,
    seed_defaults,
)


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/seed_test.db")
    await init_authz_db(engine)
    return AuthzRepository(build_session_maker(engine))


async def test_native_permissions_are_registered(repo):
    await seed_defaults(repo)
    perms = await repo.list_permissions()
    keys = {p.key for p in perms}
    assert ALL_NATIVE_KEYS <= keys


async def test_native_permissions_not_auto_granted_to_administrator(repo):
    await seed_defaults(repo)
    admin_perms = await repo.role_permissions("administrator")
    assert admin_perms.isdisjoint(ALL_NATIVE_KEYS)


async def test_native_permissions_not_auto_granted_to_agent(repo):
    await seed_defaults(repo)
    agent_perms = await repo.role_permissions("agent")
    assert agent_perms.isdisjoint(ALL_NATIVE_KEYS)


def test_native_permission_registry_has_six_keys():
    assert len(NATIVE_PERMISSION_REGISTRY) == 6
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_seed.py -v`
Expected: FAIL (`ALL_NATIVE_KEYS` doesn't exist yet).

- [ ] **Step 4: Implement**

In `seed.py`, add alongside the existing `PERMISSION_REGISTRY`/`_AGENT_PERMISSIONS` (do not touch the existing `seed_defaults` loop that grants `PERMISSION_REGISTRY` to `administrator` — the native keys are registered in a SEPARATE loop that only creates the `Permission` rows, never grants them):

```python
# Native Chatwoot conversation/inbox visibility, mirrored into Chatwoot's own
# CustomRole via features/authz/chatwoot_role_mirror.py (Phase 3). Registered
# so they're visible in the permission registry and grantable from the Roles
# & Permissions page, but deliberately NOT auto-granted to any default role —
# a tenant that never explicitly grants one of these stays byte-identical to
# pre-Phase-3 behavior (no CustomRole ever created, no user's custom_role_id
# ever touched).
NATIVE_CONVERSATION_KEYS: frozenset[str] = frozenset(
    {
        "chatwoot.conversation_manage",
        "chatwoot.conversation_unassigned_manage",
        "chatwoot.conversation_participating_manage",
    }
)
NATIVE_BOOLEAN_KEYS: frozenset[str] = frozenset(
    {
        "chatwoot.contact_manage",
        "chatwoot.report_manage",
        "chatwoot.knowledge_base_manage",
    }
)
ALL_NATIVE_KEYS: frozenset[str] = NATIVE_CONVERSATION_KEYS | NATIVE_BOOLEAN_KEYS

NATIVE_PERMISSION_REGISTRY: dict[str, str] = {
    "chatwoot.conversation_manage": "Chatwoot: see and reply to all conversations",
    "chatwoot.conversation_unassigned_manage": "Chatwoot: see unassigned conversations + own",
    "chatwoot.conversation_participating_manage": "Chatwoot: see only own/participating conversations",
    "chatwoot.contact_manage": "Chatwoot: manage contacts",
    "chatwoot.report_manage": "Chatwoot: manage reports",
    "chatwoot.knowledge_base_manage": "Chatwoot: manage knowledge base portals",
}
```

Then extend `seed_defaults` (after the existing `administrator`/`agent` setup, do not reorder the existing lines):

```python
async def seed_defaults(repo: AuthzRepository) -> None:
    for key, description in PERMISSION_REGISTRY.items():
        await repo.create_permission(key, description)

    await repo.create_role("administrator", "Administrator", "Full access to all permissions")
    for key in PERMISSION_REGISTRY:
        await repo.grant_permission("administrator", key)

    await repo.create_role("agent", "Agent", "Minimal default access")
    for key in _AGENT_PERMISSIONS:
        await repo.grant_permission("agent", key)

    # Register-only — see NATIVE_PERMISSION_REGISTRY's docstring above.
    for key, description in NATIVE_PERMISSION_REGISTRY.items():
        await repo.create_permission(key, description)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_seed.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full authz suite to confirm no regression**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/ -v`
Expected: PASS (existing `seed_defaults` consumers — `test_router.py`, `test_deps.py` — must still pass unchanged since `administrator`/`agent` grants are untouched).

- [ ] **Step 7: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/authz/seed.py \
        backend/apps/backend/src/chatbot/features/authz/test_seed.py
git commit -m "feat(authz): register 6 native chatwoot.* permission keys (register-only, not auto-granted)"
```

---

### Task 2: `user_native_role_mirror` table

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/db.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `UserNativeRoleMirror` ORM model (`chatwoot_user_id: int` PK, `chatwoot_custom_role_id: int`, `updated_at: datetime`). Consumed by Task 3's repository methods.

- [ ] **Step 1: Read the existing `test_db.py` first**

Run: `cat backend/apps/backend/src/chatbot/features/authz/test_db.py` — match its exact fixture/assertion style for the new model's test.

- [ ] **Step 2: Write the failing test**

```python
# extend test_db.py
from chatbot.features.authz.db import UserNativeRoleMirror


async def test_user_native_role_mirror_table_created(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/db_test.db")
    await init_authz_db(engine)
    session_maker = build_session_maker(engine)
    async with session_maker() as session:
        session.add(UserNativeRoleMirror(chatwoot_user_id=42, chatwoot_custom_role_id=7))
        await session.commit()
        row = await session.get(UserNativeRoleMirror, 42)
        assert row.chatwoot_custom_role_id == 7
        assert row.updated_at is not None
```

(Match whatever import names the existing `test_db.py` already uses for `build_engine`/`build_session_maker`/`init_authz_db` — these already exist from Phase 1, do not re-import differently.)

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_db.py -v -k user_native_role_mirror`
Expected: FAIL (`UserNativeRoleMirror` doesn't exist).

- [ ] **Step 4: Implement**

In `db.py`, add after the existing `UserRole` model (do not modify `Role`, `Permission`, `RolePermission`, or `UserRole` — this is purely additive):

```python
class UserNativeRoleMirror(Base):
    """Maps a Chatwoot user to the single native CustomRole we mirror their
    resolved chatwoot.* permission set into (see chatwoot_role_mirror.py).
    One row per user who currently holds ANY native permission across any of
    their roles; the row (and the Chatwoot CustomRole it points to) is
    deleted when their resolved set becomes empty."""

    __tablename__ = "user_native_role_mirror"

    chatwoot_user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chatwoot_custom_role_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_db.py -v`
Expected: PASS, including all pre-existing tests in the file.

- [ ] **Step 6: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/authz/db.py \
        backend/apps/backend/src/chatbot/features/authz/test_db.py
git commit -m "feat(authz): add user_native_role_mirror table for Phase 3's per-user Chatwoot CustomRole mirror"
```

---

### Task 3: Repository — resolve, grant-exclusive, and mirror-row CRUD

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/repository.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_repository.py`

**Interfaces:**
- Consumes: Task 1's `NATIVE_CONVERSATION_KEYS`, Task 2's `UserNativeRoleMirror`.
- Produces: `AuthzRepository.resolve_native_permissions(chatwoot_user_id: int) -> list[str]`, `grant_conversation_permission_exclusive(role_id: str, permission_key: str) -> None`, `get_native_role_mirror(chatwoot_user_id: int) -> int | None`, `set_native_role_mirror(chatwoot_user_id: int, chatwoot_custom_role_id: int) -> None`, `delete_native_role_mirror(chatwoot_user_id: int) -> None`. Consumed by Task 5's router wiring.

- [ ] **Step 1: Write the failing tests**

```python
# extend test_repository.py
from chatbot.features.authz.seed import NATIVE_CONVERSATION_KEYS  # if seed.py exports it; else import from repository's own re-export — see Step 2's implementation note


async def test_resolve_native_permissions_empty_when_no_roles_granted(repo_seeded):
    assert await repo_seeded.resolve_native_permissions(101) == []


async def test_resolve_native_permissions_single_role(repo_seeded):
    await repo_seeded.create_role("leader", "Leader")
    await repo_seeded.grant_permission("leader", "chatwoot.conversation_unassigned_manage")
    await repo_seeded.assign_role(101, "leader")
    assert await repo_seeded.resolve_native_permissions(101) == [
        "chatwoot.conversation_unassigned_manage"
    ]


async def test_resolve_native_permissions_most_permissive_wins_across_roles(repo_seeded):
    await repo_seeded.create_role("role_a", "A")
    await repo_seeded.grant_permission("role_a", "chatwoot.conversation_participating_manage")
    await repo_seeded.create_role("role_b", "B")
    await repo_seeded.grant_permission("role_b", "chatwoot.conversation_manage")
    await repo_seeded.assign_role(101, "role_a")
    await repo_seeded.assign_role(101, "role_b")
    result = await repo_seeded.resolve_native_permissions(101)
    assert result == ["chatwoot.conversation_manage"]


async def test_resolve_native_permissions_combines_conversation_and_boolean_keys(repo_seeded):
    await repo_seeded.create_role("role_a", "A")
    await repo_seeded.grant_permission("role_a", "chatwoot.conversation_unassigned_manage")
    await repo_seeded.create_role("role_b", "B")
    await repo_seeded.grant_permission("role_b", "chatwoot.contact_manage")
    await repo_seeded.assign_role(101, "role_a")
    await repo_seeded.assign_role(101, "role_b")
    result = await repo_seeded.resolve_native_permissions(101)
    assert set(result) == {"chatwoot.conversation_unassigned_manage", "chatwoot.contact_manage"}


async def test_grant_conversation_permission_exclusive_revokes_siblings(repo_seeded):
    await repo_seeded.create_role("leader", "Leader")
    await repo_seeded.grant_conversation_permission_exclusive(
        "leader", "chatwoot.conversation_manage"
    )
    await repo_seeded.grant_conversation_permission_exclusive(
        "leader", "chatwoot.conversation_unassigned_manage"
    )
    perms = await repo_seeded.role_permissions("leader")
    assert perms & NATIVE_CONVERSATION_KEYS == {"chatwoot.conversation_unassigned_manage"}


async def test_grant_conversation_permission_exclusive_leaves_boolean_keys_untouched(repo_seeded):
    await repo_seeded.create_role("leader", "Leader")
    await repo_seeded.grant_permission("leader", "chatwoot.contact_manage")
    await repo_seeded.grant_conversation_permission_exclusive(
        "leader", "chatwoot.conversation_manage"
    )
    perms = await repo_seeded.role_permissions("leader")
    assert "chatwoot.contact_manage" in perms
    assert "chatwoot.conversation_manage" in perms


async def test_native_role_mirror_roundtrip(repo_seeded):
    assert await repo_seeded.get_native_role_mirror(101) is None
    await repo_seeded.set_native_role_mirror(101, 55)
    assert await repo_seeded.get_native_role_mirror(101) == 55
    await repo_seeded.set_native_role_mirror(101, 56)  # overwrite
    assert await repo_seeded.get_native_role_mirror(101) == 56
    await repo_seeded.delete_native_role_mirror(101)
    assert await repo_seeded.get_native_role_mirror(101) is None


async def test_delete_native_role_mirror_absent_is_noop(repo_seeded):
    await repo_seeded.delete_native_role_mirror(999)  # never set — no exception
```

Use whichever `repo_seeded` fixture `test_repository.py` already provides (per Phase 2's Task 7, this fixture already exists — a repo with `seed_defaults` run). If `NATIVE_CONVERSATION_KEYS` isn't importable from `seed.py` in a way that avoids a circular import (`seed.py` importing `repository.py` which would need to import `seed.py`'s constants), move `NATIVE_CONVERSATION_KEYS`/`NATIVE_BOOLEAN_KEYS`/`ALL_NATIVE_KEYS` to `db.py` instead (which `seed.py` already imports from) and re-export from `seed.py` for backward-compat with Task 1 — check for the cleanest placement given the actual import graph once Task 1 is in place, but keep exactly one source of truth for the key lists.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_repository.py -v -k "native or resolve or exclusive"`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `repository.py` (imports: `from chatbot.features.authz.db import UserNativeRoleMirror` alongside the existing model imports; `from chatbot.features.authz.seed import NATIVE_CONVERSATION_KEYS, NATIVE_BOOLEAN_KEYS` — or from wherever Step 1 settled the constants):

```python
    async def resolve_native_permissions(self, chatwoot_user_id: int) -> list[str]:
        """Most-permissive-wins native (chatwoot.*) set for a user, across ALL
        their roles. At most one conversation_* key (highest-ranked present
        wins: manage-all > unassigned > participating-only), plus the union
        of the boolean-style keys. Order in the returned list is stable
        (conversation key first if present, then sorted booleans) so callers
        can diff/compare without re-sorting."""
        all_perms = await self.permissions_for_user(chatwoot_user_id)
        result: list[str] = []
        for key in (
            "chatwoot.conversation_manage",
            "chatwoot.conversation_unassigned_manage",
            "chatwoot.conversation_participating_manage",
        ):
            if key in all_perms:
                result.append(key)
                break
        result.extend(sorted(all_perms & NATIVE_BOOLEAN_KEYS))
        return result

    async def grant_conversation_permission_exclusive(
        self, role_id: str, permission_key: str
    ) -> None:
        """Grant one of the three conversation_* keys on a role, first
        revoking the other two on that SAME role (a role carries at most
        one). Only meaningful for permission_key in NATIVE_CONVERSATION_KEYS
        — callers (Task 5's router) are responsible for routing only those
        three keys through this method; other keys use plain grant_permission
        unchanged."""
        for other in NATIVE_CONVERSATION_KEYS - {permission_key}:
            await self.revoke_permission(role_id, other)
        await self.grant_permission(role_id, permission_key)

    async def get_native_role_mirror(self, chatwoot_user_id: int) -> int | None:
        async with self._sm() as session:
            row = await session.get(UserNativeRoleMirror, chatwoot_user_id)
            return row.chatwoot_custom_role_id if row is not None else None

    async def set_native_role_mirror(
        self, chatwoot_user_id: int, chatwoot_custom_role_id: int
    ) -> None:
        async with self._sm() as session:
            row = await session.get(UserNativeRoleMirror, chatwoot_user_id)
            if row is None:
                session.add(
                    UserNativeRoleMirror(
                        chatwoot_user_id=chatwoot_user_id,
                        chatwoot_custom_role_id=chatwoot_custom_role_id,
                    )
                )
            else:
                row.chatwoot_custom_role_id = chatwoot_custom_role_id
            await session.commit()

    async def delete_native_role_mirror(self, chatwoot_user_id: int) -> None:
        async with self._sm() as session:
            row = await session.get(UserNativeRoleMirror, chatwoot_user_id)
            if row is None:
                return
            await session.delete(row)
            await session.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_repository.py -v`
Expected: PASS, all tests in the file including pre-existing ones.

- [ ] **Step 5: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/authz/repository.py \
        backend/apps/backend/src/chatbot/features/authz/test_repository.py
git commit -m "feat(authz): add resolve_native_permissions, exclusive conversation-key grant, and native-mirror CRUD"
```

---

### Task 4: `ChatwootRoleMirror` — self-contained Chatwoot HTTP client

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/authz/chatwoot_role_mirror.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_chatwoot_role_mirror.py`

**Interfaces:**
- Consumes: `Settings.chatwoot_api_url`/`chatwoot_account_id`/`chatwoot_api_token` (all already exist, used identically by `RoutingAssigner` and `ChatwootAdapter`).
- Produces: `ChatwootRoleMirrorError(Exception)`, `ChatwootRoleMirror(settings)` with `async def ensure_custom_role(chatwoot_role_id: int | None, name: str, description: str, permissions: list[str]) -> int`, `async def delete_custom_role(chatwoot_role_id: int) -> None`, `async def set_agent_custom_role(chatwoot_user_id: int, chatwoot_role_id: int | None) -> None`. Consumed by Task 5's router wiring and Task 6's `main.py` wiring.

- [ ] **Step 1: Write the failing tests**

```python
# test_chatwoot_role_mirror.py
import httpx
import pytest

from chatbot.features.authz.chatwoot_role_mirror import (
    ChatwootRoleMirror,
    ChatwootRoleMirrorError,
)
from chatbot.platform.config import get_settings


@pytest.fixture
def settings():
    return get_settings()


async def test_ensure_custom_role_creates_when_no_existing_id(settings, respx_mock):
    respx_mock.post(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles"
    ).mock(return_value=httpx.Response(200, json={"id": 7}))
    mirror = ChatwootRoleMirror(settings)
    result = await mirror.ensure_custom_role(None, "Leader", "desc", ["conversation_manage"])
    assert result == 7


async def test_ensure_custom_role_updates_when_existing_id(settings, respx_mock):
    respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles/7"
    ).mock(return_value=httpx.Response(200, json={"id": 7}))
    mirror = ChatwootRoleMirror(settings)
    result = await mirror.ensure_custom_role(7, "Leader", "desc", ["contact_manage"])
    assert result == 7


async def test_ensure_custom_role_raises_on_http_error(settings, respx_mock):
    respx_mock.post(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles"
    ).mock(return_value=httpx.Response(500))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.ensure_custom_role(None, "Leader", "desc", [])


async def test_delete_custom_role_raises_on_http_error(settings, respx_mock):
    respx_mock.delete(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles/7"
    ).mock(return_value=httpx.Response(404))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.delete_custom_role(7)


async def test_delete_custom_role_succeeds(settings, respx_mock):
    respx_mock.delete(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles/7"
    ).mock(return_value=httpx.Response(200))
    mirror = ChatwootRoleMirror(settings)
    await mirror.delete_custom_role(7)  # no exception


async def test_set_agent_custom_role_sends_top_level_param(settings, respx_mock):
    route = respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/agents/9"
    ).mock(return_value=httpx.Response(200, json={}))
    mirror = ChatwootRoleMirror(settings)
    await mirror.set_agent_custom_role(9, 7)
    assert route.calls.last.request.content
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {"custom_role_id": 7}


async def test_set_agent_custom_role_clears_with_none(settings, respx_mock):
    route = respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/agents/9"
    ).mock(return_value=httpx.Response(200, json={}))
    mirror = ChatwootRoleMirror(settings)
    await mirror.set_agent_custom_role(9, None)
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {"custom_role_id": None}


async def test_set_agent_custom_role_raises_on_http_error(settings, respx_mock):
    respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/agents/9"
    ).mock(return_value=httpx.Response(422))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.set_agent_custom_role(9, 7)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_chatwoot_role_mirror.py -v`
Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement**

```python
"""Mirrors our RBAC role grants into Chatwoot's own dormant, already-shipped
CustomRole + account_users.custom_role_id mechanism — see
docs/superpowers/specs/2026-08-02-rbac-phase3-native-conversation-visibility-design.md.
Self-contained: owns its own httpx client and constructs dual-auth headers
from settings directly, mirroring features/routing/assigner.py's
RoutingAssigner exactly. UNLIKE RoutingAssigner, this is FAIL-CLOSED — every
method raises ChatwootRoleMirrorError on any HTTP failure instead of
swallowing it, because this governs human access control (Phase 1's
fail-closed boundary), not AI orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class ChatwootRoleMirrorError(Exception):
    pass


class ChatwootRoleMirror:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Deferred import avoids a circular dependency between the authz
        # package and the chat adapter package (matches RoutingAssigner).
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
                res = await client.request(
                    method, url, json=payload, headers=headers, timeout=10.0
                )
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("chatwoot_role_mirror_request_failed", method=method, path=path, error=str(e))
            raise ChatwootRoleMirrorError(f"{method} {path} failed: {e}") from e

    async def ensure_custom_role(
        self,
        chatwoot_role_id: int | None,
        name: str,
        description: str,
        permissions: list[str],
    ) -> int:
        body = {
            "custom_role": {"name": name, "description": description, "permissions": permissions}
        }
        if chatwoot_role_id is None:
            res = await self._request("POST", "/custom_roles", body)
        else:
            res = await self._request("PATCH", f"/custom_roles/{chatwoot_role_id}", body)
        return int(res["id"])

    async def delete_custom_role(self, chatwoot_role_id: int) -> None:
        await self._request("DELETE", f"/custom_roles/{chatwoot_role_id}")

    async def set_agent_custom_role(
        self, chatwoot_user_id: int, chatwoot_role_id: int | None
    ) -> None:
        await self._request(
            "PATCH", f"/agents/{chatwoot_user_id}", {"custom_role_id": chatwoot_role_id}
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_chatwoot_role_mirror.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/authz/chatwoot_role_mirror.py \
        backend/apps/backend/src/chatbot/features/authz/test_chatwoot_role_mirror.py
git commit -m "feat(authz): add ChatwootRoleMirror — fail-closed client for Chatwoot's native CustomRole API"
```

---

### Task 5: Wire the mirror into the `/authz` router, fail-closed

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/router.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_router.py`

**Interfaces:**
- Consumes: Task 3's `resolve_native_permissions`/`grant_conversation_permission_exclusive`/native-mirror CRUD, Task 4's `ChatwootRoleMirror`/`ChatwootRoleMirrorError`, Task 1's `ALL_NATIVE_KEYS`/`NATIVE_CONVERSATION_KEYS`.
- Produces: `build_authz_router(repo, validator, settings, mirror: ChatwootRoleMirror | None = None) -> APIRouter` — the new `mirror` parameter defaults to `None` so any existing caller (there are none yet outside `main.py`, which Task 6 updates) keeps working; when `None`, native-key grants behave exactly like any other permission grant (no mirror sync attempted) — this is what keeps tests for Phases 1-2's endpoints unaffected.

- [ ] **Step 1: Write the failing tests**

Add a new fixture alongside the existing `client` fixture in `test_router.py` (do not modify the existing one — Phase 1/2 tests must keep passing unchanged against it):

```python
# extend test_router.py
from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror, ChatwootRoleMirrorError


class _FakeMirror:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.ensure_calls = []
        self.delete_calls = []
        self.agent_calls = []
        self._next_id = 100

    async def ensure_custom_role(self, chatwoot_role_id, name, description, permissions):
        if self.fail:
            raise ChatwootRoleMirrorError("boom")
        self.ensure_calls.append((chatwoot_role_id, name, description, list(permissions)))
        if chatwoot_role_id is not None:
            return chatwoot_role_id
        self._next_id += 1
        return self._next_id

    async def delete_custom_role(self, chatwoot_role_id):
        if self.fail:
            raise ChatwootRoleMirrorError("boom")
        self.delete_calls.append(chatwoot_role_id)

    async def set_agent_custom_role(self, chatwoot_user_id, chatwoot_role_id):
        if self.fail:
            raise ChatwootRoleMirrorError("boom")
        self.agent_calls.append((chatwoot_user_id, chatwoot_role_id))


@pytest.fixture
async def mirror_client(tmp_path, respx_mock):
    """Same setup as `client`, but with a working _FakeMirror wired in — use
    for Phase 3's native-key tests."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/mirror_router_test.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=1, role_id="administrator")
    await repo.create_role("leader", "Leader", "")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    validator = TokenValidator(settings)
    mirror = _FakeMirror()
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings, mirror=mirror))
    return TestClient(app), repo, mirror


async def test_grant_native_conversation_key_syncs_mirror_and_assigns_agent(mirror_client):
    client, repo, mirror = mirror_client
    await repo.assign_role(chatwoot_user_id=5, role_id="leader")
    res = client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_unassigned_manage"},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    assert res.status_code == 200
    assert mirror.ensure_calls[-1][3] == ["conversation_unassigned_manage"]
    assert mirror.agent_calls[-1][0] == 5
    assert mirror.agent_calls[-1][1] is not None


async def test_grant_second_conversation_key_replaces_first_in_mirror(mirror_client):
    client, repo, mirror = mirror_client
    await repo.assign_role(chatwoot_user_id=5, role_id="leader")
    client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_manage"},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_unassigned_manage"},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    perms = await repo.role_permissions("leader")
    assert "chatwoot.conversation_manage" not in perms
    assert "chatwoot.conversation_unassigned_manage" in perms
    assert mirror.ensure_calls[-1][3] == ["conversation_unassigned_manage"]


async def test_grant_native_key_rolls_back_db_on_mirror_failure(mirror_client):
    client, repo, mirror = mirror_client
    mirror.fail = True
    res = client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_manage"},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    assert res.status_code == 502
    perms = await repo.role_permissions("leader")
    assert "chatwoot.conversation_manage" not in perms


async def test_grant_non_native_key_unaffected_by_missing_mirror(tmp_path, respx_mock):
    """No mirror wired at all (mirror=None, matching Phases 1-2's existing
    `client` fixture) — a plain sla.manage-style grant must behave exactly
    as it did before Phase 3."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/no_mirror_test.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=1, role_id="administrator")
    await repo.create_role("leader", "Leader", "")
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    validator = TokenValidator(settings)
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings))  # mirror defaults to None
    client = TestClient(app)
    res = client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "audit.view"},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    assert res.status_code == 200
    assert "audit.view" in await repo.role_permissions("leader")


async def test_revoke_last_native_key_deletes_mirror_and_clears_agent(mirror_client):
    client, repo, mirror = mirror_client
    await repo.assign_role(chatwoot_user_id=5, role_id="leader")
    client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_manage"},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    mirror.ensure_calls.clear()
    mirror.agent_calls.clear()
    res = client.delete(
        "/authz/roles/leader/permissions/chatwoot.conversation_manage",
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    assert res.status_code == 200
    assert await repo.get_native_role_mirror(5) is None
    assert (5, None) in mirror.agent_calls
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_router.py -v -k "native or mirror"`
Expected: FAIL (`build_authz_router` doesn't accept `mirror=` yet, `_FakeMirror` methods never called).

- [ ] **Step 3: Implement**

In `router.py`, add the imports and a `mirror` parameter, plus a private resync helper and branching in the two affected endpoints. This replaces the `grant_role_permission`/`revoke_role_permission`/`assign_role`/`unassign_role` handler bodies — read the current file (shown fully above in this plan's research) before editing so line numbers match:

```python
from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror, ChatwootRoleMirrorError
from chatbot.features.authz.seed import ALL_NATIVE_KEYS, NATIVE_CONVERSATION_KEYS


def build_authz_router(
    repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
    mirror: ChatwootRoleMirror | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/authz", tags=["authz"])
    # ... existing _caller_user_id, my_permissions, check, list_roles, manage_roles,
    # create_role, assign_role's OLD body replaced below, permission_registry,
    # role_permissions, role_users unchanged — only the four handlers below change.

    async def _resync_role_mirror(role_id: str) -> None:
        """After any grant/revoke of a native key OR any role
        assign/unassign, recompute EVERY affected user's resolved native set
        and push it to Chatwoot. Raises ChatwootRoleMirrorError on failure —
        callers must compensating-revert their own DB change and re-raise as
        a 502."""
        if mirror is None:
            return
        for user_id in await repo.users_for_role(role_id):
            resolved = await repo.resolve_native_permissions(user_id)
            stripped = [p.removeprefix("chatwoot.") for p in resolved]
            existing_mirror_id = await repo.get_native_role_mirror(user_id)
            if not stripped:
                if existing_mirror_id is not None:
                    await mirror.delete_custom_role(existing_mirror_id)
                    await repo.delete_native_role_mirror(user_id)
                    await mirror.set_agent_custom_role(user_id, None)
                continue
            new_id = await mirror.ensure_custom_role(
                existing_mirror_id, f"RBAC user {user_id}", "Mirrored by RBAC Phase 3", stripped
            )
            if new_id != existing_mirror_id:
                await repo.set_native_role_mirror(user_id, new_id)
                await mirror.set_agent_custom_role(user_id, new_id)

    @router.post("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def assign_role(role_id: str, body: AssignRoleBody) -> dict:
        await repo.assign_role(body.chatwoot_user_id, role_id)
        try:
            await _resync_role_mirror(role_id)
        except ChatwootRoleMirrorError as exc:
            await repo.unassign_role(body.chatwoot_user_id, role_id)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        return {"ok": True}

    @router.post("/roles/{role_id}/permissions", dependencies=[Depends(manage_roles)])
    async def grant_role_permission(role_id: str, body: GrantPermissionBody) -> dict:
        key = body.permission_key
        if key not in ALL_NATIVE_KEYS or mirror is None:
            await repo.grant_permission(role_id, key)
            return {"ok": True}
        if key in NATIVE_CONVERSATION_KEYS:
            previous = await repo.role_permissions(role_id) & NATIVE_CONVERSATION_KEYS
            await repo.grant_conversation_permission_exclusive(role_id, key)
        else:
            previous = set()
            await repo.grant_permission(role_id, key)
        try:
            await _resync_role_mirror(role_id)
        except ChatwootRoleMirrorError as exc:
            await repo.revoke_permission(role_id, key)
            for old_key in previous:
                await repo.grant_permission(role_id, old_key)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        return {"ok": True}

    @router.delete(
        "/roles/{role_id}/permissions/{permission_key}", dependencies=[Depends(manage_roles)]
    )
    async def revoke_role_permission(role_id: str, permission_key: str) -> dict:
        was_native = permission_key in ALL_NATIVE_KEYS and mirror is not None
        await repo.revoke_permission(role_id, permission_key)
        if not was_native:
            return {"ok": True}
        try:
            await _resync_role_mirror(role_id)
        except ChatwootRoleMirrorError as exc:
            await repo.grant_permission(role_id, permission_key)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        return {"ok": True}

    @router.delete("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def unassign_role(role_id: str, body: AssignRoleBody) -> dict:
        await repo.unassign_role(body.chatwoot_user_id, role_id)
        try:
            await _resync_role_mirror(role_id)
        except ChatwootRoleMirrorError as exc:
            await repo.assign_role(body.chatwoot_user_id, role_id)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        return {"ok": True}
```

Note `_resync_role_mirror` is defined once and referenced by all four handlers — it must appear in the function body BEFORE the four `@router...` decorators that use it (Python closures need the name bound at call time, not definition time, so either order technically works inside one function scope, but place it right after `manage_roles = require_permission(...)` for readability, matching the file's existing top-to-bottom flow).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/features/authz/test_router.py -v`
Expected: PASS, including every pre-existing Phase 1/2 test in the file (they use the `client` fixture, which calls `build_authz_router` without `mirror=`, so `mirror is None` short-circuits every new branch to old behavior).

- [ ] **Step 5: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/authz/router.py \
        backend/apps/backend/src/chatbot/features/authz/test_router.py
git commit -m "feat(authz): wire ChatwootRoleMirror into grant/revoke/assign/unassign, fail-closed"
```

---

### Task 6: `main.py` wiring

**Files:**
- Modify: `backend/apps/backend/src/chatbot/main.py`
- Test: run the existing wiring smoke test (no new test file — this task is pure wiring, covered by Task 5's router tests + the existing `test_chatwoot_wiring.py`-style smoke test).

**Interfaces:**
- Consumes: Task 4's `ChatwootRoleMirror`, Task 5's updated `build_authz_router` signature.
- Produces: nothing new for later tasks — this is the final wiring point.

- [ ] **Step 1: Modify the RBAC block**

In `main.py`, inside the existing `if settings.rbac_enabled and settings.rbac_database_url:` block (shown fully above in this plan's research, currently ending at `app.state.authz_repo = authz_repo`), add the mirror construction and pass it to `build_authz_router`:

```python
        from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror

        authz_engine = build_authz_engine(settings.rbac_database_url)
        authz_session_maker = build_authz_session_maker(authz_engine)
        authz_repo = AuthzRepository(authz_session_maker)
        authz_validator = TokenValidator(settings)
        authz_mirror = ChatwootRoleMirror(settings)
        app.include_router(build_authz_router(authz_repo, authz_validator, settings, mirror=authz_mirror))
        app.state.authz_engine = authz_engine
        app.state.authz_repo = authz_repo
```

(Only the `build_authz_router(...)` call line changes — add `authz_mirror = ChatwootRoleMirror(settings)` immediately before it and pass `mirror=authz_mirror`. Every other line in this block, and the `elif settings.rbac_enabled:` warning branch below it, stays untouched.)

- [ ] **Step 2: Run the app-wiring smoke test**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/chatbot/ -k "wiring or main" -v`
Expected: PASS — `create_app()` still builds cleanly with RBAC enabled.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy-test-key pytest src/ -q`
Expected: PASS, same count as before Phase 3 plus this plan's new tests, zero regressions.

- [ ] **Step 4: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/main.py
git commit -m "feat(authz): wire ChatwootRoleMirror into the RBAC block in main.py"
```

---

### Task 7: Fork patch `0028` — "Chatwoot access" permission group on the Roles & Permissions page

**Files:**
- Create: `deploy/chatwoot-fork/patches/0028-chatwoot-access-permissions.patch`

**Interfaces:**
- Consumes: Task 1's six `chatwoot.*` permission keys (via the existing `GET /authz/permission-registry` and `GET/POST/DELETE /authz/roles/{role_id}/permissions` endpoints Phase 2 already built — no new backend endpoint needed for the frontend).
- Produces: a UI-only change to `ProtonRolesPermissionsPage.vue` (created by patch `0027`) — a new "Chatwoot access" section at the top of each role's permission editor.

- [ ] **Step 1: Set up the local dev loop**

Follow `deploy/chatwoot-fork/README.md`'s "Local dev loop" (same as every prior fork-patch task):

```bash
VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION)
rm -rf /tmp/proton-chatwoot-dev-p3
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-dev-p3
cd /tmp/proton-chatwoot-dev-p3
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/[0-9]*.patch; do
  git apply --whitespace=fix "$p" || { echo "FAILED: $p"; exit 1; }
done
echo "All patches 0001-0027 applied cleanly"
```

- [ ] **Step 2: Read the current `ProtonRolesPermissionsPage.vue` in full**

Run: `sed -n '1,400p' app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue` (the file patch `0027` created) — find the exact markup for the existing permission-checkbox list (per Task 11's plan in Phase 2, it's a `<ul>` of checkboxes bound to `togglePermission(role.id, perm.key)`-style handlers calling `protonAdmin.js`'s grant/revoke functions) so the new section matches its exact styling classes and interaction pattern.

- [ ] **Step 3: Add the "Chatwoot access" section**

Insert a new `<div>` immediately above the existing permission-checkbox `<ul>`, inside the selected-role detail panel:

```vue
      <div class="flex flex-col gap-2 mb-6 pb-6 border-b border-n-weak">
        <h4 class="text-sm font-medium text-n-slate-12">Chatwoot access</h4>
        <p class="text-xs text-n-slate-11">
          Controls what this role can see/do in Chatwoot itself (not our admin
          pages below).
        </p>
        <fieldset class="flex flex-col gap-1">
          <label
            v-for="opt in conversationVisibilityOptions"
            :key="opt.key"
            class="flex items-center gap-2 text-sm text-n-slate-12"
          >
            <input
              type="radio"
              :name="`conv-visibility-${selectedRole.id}`"
              :value="opt.key"
              :checked="selectedRoleConversationKey === opt.key"
              @change="setConversationVisibility(opt.key)"
            />
            {{ opt.label }}
          </label>
        </fieldset>
        <div class="flex flex-col gap-1 mt-2">
          <label
            v-for="opt in booleanNativeOptions"
            :key="opt.key"
            class="flex items-center gap-2 text-sm text-n-slate-12"
          >
            <input
              type="checkbox"
              :checked="selectedRolePermissions.includes(opt.key)"
              @change="toggleNativePermission(opt.key, $event.target.checked)"
            />
            {{ opt.label }}
          </label>
        </div>
      </div>
```

In the `<script>` block, add (matching whatever composition style — Options API `data`/`computed`/`methods`, per patch 0027's existing convention — the file already uses):

```js
const NATIVE_CONVERSATION_OPTIONS = [
  { key: 'chatwoot.conversation_manage', label: 'Manage all conversations' },
  { key: 'chatwoot.conversation_unassigned_manage', label: 'Unassigned conversations only' },
  { key: 'chatwoot.conversation_participating_manage', label: 'My conversations only' },
];
const NATIVE_BOOLEAN_OPTIONS = [
  { key: 'chatwoot.contact_manage', label: 'Contacts' },
  { key: 'chatwoot.report_manage', label: 'Reports' },
  { key: 'chatwoot.knowledge_base_manage', label: 'Knowledge base' },
];

// ... inside the component:
  data() {
    return {
      // ...existing fields
      conversationVisibilityOptions: NATIVE_CONVERSATION_OPTIONS,
      booleanNativeOptions: NATIVE_BOOLEAN_OPTIONS,
    };
  },
  computed: {
    // ...existing computed
    selectedRoleConversationKey() {
      const conversationKeys = NATIVE_CONVERSATION_OPTIONS.map(o => o.key);
      return (
        this.selectedRolePermissions.find(key => conversationKeys.includes(key)) || null
      );
    },
  },
  methods: {
    // ...existing methods
    async setConversationVisibility(key) {
      await this.grantPermission(key); // reuses the existing grant-permission call already wired to POST /authz/roles/{id}/permissions
    },
    async toggleNativePermission(key, checked) {
      if (checked) {
        await this.grantPermission(key);
      } else {
        await this.revokePermission(key); // reuses the existing revoke-permission call
      }
    },
```

(`grantPermission`/`revokePermission` and `selectedRolePermissions` are the existing methods/computed patch `0027` already defined for the plain permission checkboxes below — Step 2's read confirms their exact names; reuse them verbatim rather than duplicating the API-call logic, since the backend's mutual-exclusivity handling in Task 5 means the radio group's `setConversationVisibility` can just call the SAME grant endpoint the checkboxes use — the server enforces exclusivity, the client doesn't need to.)

- [ ] **Step 4: Manual smoke test**

Confirm: selecting a role shows its current conversation-visibility radio selection and boolean checkboxes pre-checked from `GET /authz/roles/{id}/permissions`; changing the radio grants the new key and the old one visibly unchecks after the list refetches; a 502 from the backend (simulate by stopping Chatwoot) surfaces as an error toast, not a silently-stuck-checked checkbox.

- [ ] **Step 5: Export and verify**

```bash
cd /tmp/proton-chatwoot-dev-p3
git diff HEAD -- app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue \
  > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0028-chatwoot-access-permissions.patch

# Verify 0001-0028 apply cleanly in full sequence on a fresh clone:
rm -rf /tmp/proton-chatwoot-final-verify-p3
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-final-verify-p3
cd /tmp/proton-chatwoot-final-verify-p3
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/[0-9]*.patch; do
  git apply --whitespace=fix "$p" || { echo "FAILED: $p"; exit 1; }
done
echo "All patches 0001-0028 applied cleanly"

# Local builder-stage compile check (arm64 fine, don't push):
cd /Users/yudaadipratama/Archive/id-crm-ticketing
docker build --target builder deploy/chatwoot-fork/
```

- [ ] **Step 6: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0028-chatwoot-access-permissions.patch
git commit -m "feat(chatwoot-fork): add 'Chatwoot access' native-permission group to Roles & Permissions page"
```

---

## Plan Self-Review Notes

- **Spec coverage:** the design doc's data-model section is superseded by this plan's "Plan amendment" (per-user mirror, not per-role) — documented explicitly at the top rather than silently deviating. Sync mechanism → Tasks 4-5. Mutual-exclusivity ("set not add") → Task 3's `grant_conversation_permission_exclusive` + Task 5's router branch. Delete-last-key-clears-mirror → Task 5's `_resync_role_mirror` empty-`stripped` branch. Frontend → Task 7. Fail-closed/rollback → every handler in Task 5 has a compensating revert on `ChatwootRoleMirrorError`. Default-preserving (`mirror=None` byte-identical) → Task 5's `test_grant_non_native_key_unaffected_by_missing_mirror`.
- **Placeholder scan:** no TBD/TODO; every step has real code.
- **Type consistency check:** `ChatwootRoleMirror.ensure_custom_role`'s `permissions: list[str]` takes STRIPPED keys (no `chatwoot.` prefix) throughout — Task 5's `_resync_role_mirror` does the `removeprefix("chatwoot.")` before calling it, consistently in both the router and (implicitly) nowhere else calls `ensure_custom_role` directly. `resolve_native_permissions` returns keys WITH the `chatwoot.` prefix (matching `role_permissions`'s existing convention) — the strip only happens at the Chatwoot-API boundary in Task 5, not inside Task 3's repository. Verified consistent across Tasks 3, 4, 5.
- **Rollback is compensating, not transactional.** Since the Chatwoot HTTP call can't participate in the SQL transaction, "rollback" in every Task 5 handler means "call the inverse repository method after catching the mirror failure" — not a literal `ROLLBACK`. Flagging this explicitly so an implementer doesn't try to wrap the HTTP call inside the DB session's transaction (which SQLAlchemy async sessions don't support across an awaited external call cleanly, and isn't needed here).
- **Known limitation, not a task gap:** two users with identical resolved native-permission sets get two separate (duplicate-content) Chatwoot `CustomRole` rows rather than sharing one — a deliberate simplification (see "Plan amendment" section) to avoid reference-counted row sharing. Not expected to cause any functional issue; only cosmetic duplication in Chatwoot's own `custom_roles` table.
