# Custom Feature Switchboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the platform superadmin a per-tenant switchboard that decides which custom surfaces a tenant has, defaulting to all-off so a newly provisioned tenant opens as a blank CRM.

**Architecture:** A static registry of custom features plus a Firestore-backed per-tenant store (absent key = off) read through one endpoint. Superadmin identity comes from Chatwoot's own `SuperAdmin` STI type, plus a hardcoded `user_id == 1` floor. The SPA gates nav and routes on `hasFeature(f) && hasPermission(p)` — two orthogonal gates, feature owned by the vendor, permission owned by the tenant's admin.

**Tech Stack:** FastAPI, pydantic-settings, google-cloud-firestore, pytest (`asyncio_mode=auto`), Vue 3 composition API in the Chatwoot fork (applied as `git apply` patches at image build time).

**Spec:** `docs/superpowers/specs/2026-08-22-platform-feature-switchboard-design.md`

## Global Constraints

- **Module location is `chatbot/features/tenant_config/`, not `features/platform/`.** The spec says `features/platform/`; that would sit confusingly beside the unrelated `chatbot/platform/` infra package. Same code, clearer home.
- **Absent key means off.** No seeding, no default-on list, no first-boot marker. "Starts empty" must be a property of the data model.
- **`type == "SuperAdmin"` is an equality test, never truthiness.** A regular Chatwoot user's `type` is `nil`, not `"User"`.
- **Never add `features.manage` to `PERMISSION_REGISTRY`.** `seed_defaults` grants `administrator` every key there, which would hand the customer's own admin the switchboard.
- **Fail closed.** Feature reads that error resolve to `[]`, never to a last-known or default-on list.
- **New code uses generic names** — `custom_features`, `CustomFeatureStore`, `/admin/custom-features`, `useCustomFeatures`. Do not add to the `Proton*` surface. Do not rename anything that already exists.
- **Tests live beside their source** as `test_<module>.py`, matching this codebase.
- **Run tests from `backend/apps/backend`** with `uv run pytest`. A whole-suite run needs `GOOGLE_API_KEY=test-dummy` set or 5 modules fail at collection.
- **Fork patches: this sandbox cannot reach github.com to clone upstream.** New-file patches are pure additions and can be written directly. Patches that modify an upstream file must have their context lines transcribed from an existing patch that already touches that file — never guessed.

---

### Task 1: Resolve superadmin identity from the Chatwoot profile

`TokenValidator` already calls `/api/v1/profile` and throws away everything except `id`. The profile also carries `type`, which is `"SuperAdmin"` for a Chatwoot super admin. Extend the validator to return both, without breaking its existing single-value callers.

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/identity.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_identity.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `TokenValidator.resolve_identity(access_token: str, client: str, uid: str) -> tuple[int, bool] | None` — `(user_id, is_super_admin)`, or `None` on any failure.
  - `TokenValidator.resolve_user_id(...) -> int | None` — unchanged signature and behaviour, now delegating to `resolve_identity`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/apps/backend/src/chatbot/features/authz/test_identity.py`:

```python
@pytest.mark.asyncio
async def test_resolve_identity_reports_super_admin_type(respx_mock) -> None:
    respx_mock.get("https://cw.test/api/v1/profile").respond(
        json={"id": 7, "type": "SuperAdmin"}
    )
    validator = TokenValidator(_settings())
    assert await validator.resolve_identity("tok", "cli", "uid@x") == (7, True)


@pytest.mark.asyncio
async def test_resolve_identity_treats_null_type_as_not_super_admin(respx_mock) -> None:
    """A regular Chatwoot user's `type` is null, NOT the string "User". A
    truthiness check would pass here and hand the switchboard to everyone."""
    respx_mock.get("https://cw.test/api/v1/profile").respond(
        json={"id": 9, "type": None}
    )
    validator = TokenValidator(_settings())
    assert await validator.resolve_identity("tok", "cli", "uid@x") == (9, False)


@pytest.mark.asyncio
async def test_resolve_identity_caches_both_halves(respx_mock) -> None:
    route = respx_mock.get("https://cw.test/api/v1/profile").respond(
        json={"id": 1, "type": "SuperAdmin"}
    )
    validator = TokenValidator(_settings())
    await validator.resolve_identity("tok", "cli", "uid@x")
    await validator.resolve_identity("tok", "cli", "uid@x")
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_resolve_user_id_still_returns_a_bare_int(respx_mock) -> None:
    """Existing callers pass an int straight into repo.permissions_for_user."""
    respx_mock.get("https://cw.test/api/v1/profile").respond(
        json={"id": 4, "type": None}
    )
    validator = TokenValidator(_settings())
    assert await validator.resolve_user_id("tok", "cli", "uid@x") == 4


@pytest.mark.asyncio
async def test_resolve_identity_returns_none_on_http_failure(respx_mock) -> None:
    respx_mock.get("https://cw.test/api/v1/profile").respond(status_code=401)
    validator = TokenValidator(_settings())
    assert await validator.resolve_identity("tok", "cli", "uid@x") is None
```

If `_settings()` and the `respx_mock` fixture are not already defined in this file, read the top of `test_identity.py` and reuse whatever helper it already uses to build a `Settings` with `chatwoot_api_url="https://cw.test"`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/authz/test_identity.py -v
```

Expected: FAIL — `AttributeError: 'TokenValidator' object has no attribute 'resolve_identity'`.

- [ ] **Step 3: Implement**

In `identity.py`, change the cache value type and split the method. Replace the `_cache` declaration and `resolve_user_id` with:

```python
        # (access_token, client, uid) -> ((user_id, is_super_admin), expires_at)
        self._cache: dict[tuple[str, str, str], tuple[tuple[int, bool], float]] = {}

    async def resolve_identity(
        self, access_token: str, client: str, uid: str
    ) -> tuple[int, bool] | None:
        """Resolve a session to `(user_id, is_super_admin)`.

        `type` is Chatwoot's STI discriminator on `users.type`: `SuperAdmin`
        for a platform super admin, and **null** for everyone else — not the
        string "User". The comparison is therefore an equality test against
        "SuperAdmin" rather than a truthiness check, which would grant every
        ordinary agent superadmin status.
        """
        cache_key = (access_token, client, uid)
        cached = self._cache.get(cache_key)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        url = f"{self._settings.chatwoot_api_url.rstrip('/')}/api/v1/profile"
        try:
            async with httpx.AsyncClient() as http_client:
                res = await http_client.get(
                    url,
                    headers={"access-token": access_token, "client": client, "uid": uid},
                    timeout=5.0,
                )
                res.raise_for_status()
                payload = res.json()
                identity = (int(payload["id"]), payload.get("type") == "SuperAdmin")
        except Exception as exc:
            _log.warning("authz_token_validation_failed", error=str(exc))
            return None

        self._cache[cache_key] = (identity, time.monotonic() + self._ttl)
        return identity

    async def resolve_user_id(self, access_token: str, client: str, uid: str) -> int | None:
        identity = await self.resolve_identity(access_token, client, uid)
        return None if identity is None else identity[0]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/authz/test_identity.py src/chatbot/features/authz/test_deps.py -v
```

Expected: PASS. `test_deps.py` is included because it exercises `resolve_user_id` through `require_permission` — it must stay green.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/authz/identity.py \
        backend/apps/backend/src/chatbot/features/authz/test_identity.py
git commit -m "feat(authz): resolve the Chatwoot SuperAdmin type alongside the user id"
```

---

### Task 2: Gate on platform superadmin, and let a superadmin hold every permission

Two changes in `deps.py`. A new dependency for the switchboard's write path, and a bypass so the platform owner is not locked out of a tenant's Roles & Permissions page on a tenant where they were never assigned a role.

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/deps.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_deps.py`

**Interfaces:**
- Consumes: `TokenValidator.resolve_identity` from Task 1.
- Produces:
  - `is_platform_superadmin(user_id: int, is_super_admin_type: bool) -> bool`
  - `require_platform_superadmin(*, validator: TokenValidator | None, settings: Settings)` → a FastAPI dependency callable returning the caller's `int` user id.

- [ ] **Step 1: Write the failing tests**

Append to `test_deps.py`:

```python
from chatbot.features.authz.deps import (
    is_platform_superadmin,
    require_platform_superadmin,
)


def test_user_one_is_always_a_platform_superadmin() -> None:
    """The floor. Id 1 set the platform up on every tenant, and hardcoding it
    means no administrative accident can lock the owner out."""
    assert is_platform_superadmin(1, False) is True


def test_chatwoot_super_admin_type_is_a_platform_superadmin() -> None:
    assert is_platform_superadmin(7, True) is True


def test_ordinary_user_is_not_a_platform_superadmin() -> None:
    assert is_platform_superadmin(7, False) is False


@pytest.mark.asyncio
async def test_require_platform_superadmin_401s_without_a_session() -> None:
    check = require_platform_superadmin(validator=None, settings=_settings())
    with pytest.raises(HTTPException) as exc:
        await check(None, None, None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_platform_superadmin_403s_for_an_ordinary_user() -> None:
    check = require_platform_superadmin(
        validator=_FakeValidator((7, False)), settings=_settings()
    )
    with pytest.raises(HTTPException) as exc:
        await check("tok", "cli", "uid@x")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_platform_superadmin_allows_user_one_without_the_type() -> None:
    check = require_platform_superadmin(
        validator=_FakeValidator((1, False)), settings=_settings()
    )
    assert await check("tok", "cli", "uid@x") == 1


@pytest.mark.asyncio
async def test_require_platform_superadmin_ignores_the_shared_secret() -> None:
    """A shared secret identifies a service, not a person. The switchboard
    records who changed a tenant's product, so there must be a person."""
    check = require_platform_superadmin(validator=None, settings=_settings())
    with pytest.raises(HTTPException) as exc:
        await check(None, None, None, x_api_key="the-shared-secret")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_super_admin_holds_every_rbac_permission_without_a_role() -> None:
    """Otherwise the platform owner cannot open Roles & Permissions on a
    tenant where nobody ever assigned them a role — which is most of them."""
    settings = _settings(rbac_enabled=True)
    repo = _FakeRepo(perms=set())  # no role, no permissions
    check = require_permission(
        "roles.manage",
        repo=repo,
        validator=_FakeValidator((7, True)),
        settings=settings,
    )
    await check(None, "tok", "cli", "uid@x")  # must not raise
```

Read the existing `test_deps.py` first and reuse its `_settings()`, `_FakeRepo` and validator-double helpers. If its validator double only implements `resolve_user_id`, add a `_FakeValidator` that implements `resolve_identity` returning the tuple it was constructed with, and `resolve_user_id` returning `identity[0]`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/authz/test_deps.py -v
```

Expected: FAIL — `ImportError: cannot import name 'is_platform_superadmin'`.

- [ ] **Step 3: Implement**

Add to `deps.py`:

```python
# The platform owner. Id 1 is the account that set the instance up, verified
# to be the vendor on every tenant here. It is a hardcoded floor rather than
# a stored grant precisely so that no sequence of administrative accidents --
# a revoked role, a stripped SuperAdmin type, a botched migration -- can lock
# the owner out of the switchboard.
_PLATFORM_OWNER_USER_ID = 1


def is_platform_superadmin(user_id: int, is_super_admin_type: bool) -> bool:
    """Chatwoot's own SuperAdmin type, plus the id-1 floor.

    Granting superadmin to somebody else is Chatwoot's `/super_admin` console,
    which already does it and is already how this platform's other superadmins
    were made. Deliberately NOT a second grant list of our own: it would be
    free to disagree with `users.type`, producing someone revoked in our UI
    who is still a Chatwoot superadmin.
    """
    return user_id == _PLATFORM_OWNER_USER_ID or is_super_admin_type


def require_platform_superadmin(
    *,
    validator: TokenValidator | None = None,
    settings: Settings,
):
    """Gate for the custom-feature switchboard.

    Never honours the shared-secret path, and does NOT consult
    `settings.rbac_enabled`: this is a platform-level authority that exists
    whether or not a tenant has opted into RBAC. Feature management is
    deliberately not an RBAC permission -- `seed_defaults` grants
    "administrator" every key in PERMISSION_REGISTRY, so a `features.manage`
    key would hand each customer's own admin the power to switch on surfaces
    they did not buy.
    """

    async def _check(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),  # noqa: ARG001 -- shared secret deliberately ignored
    ) -> int:
        if (
            not x_chatwoot_access_token
            or not x_chatwoot_client
            or not x_chatwoot_uid
            or validator is None
        ):
            raise HTTPException(status_code=401, detail="Chatwoot session required")

        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id, is_super_admin_type = identity
        if not is_platform_superadmin(user_id, is_super_admin_type):
            raise HTTPException(status_code=403, detail="Platform superadmin required")
        return user_id

    return _check
```

Then, inside `require_permission`'s `_check`, replace the resolve-and-check block with an identity-aware version:

```python
        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id, is_super_admin_type = identity
        # A platform superadmin holds every RBAC permission. Without this the
        # platform owner is locked out of a tenant's own Roles & Permissions
        # page on any tenant where they were never assigned a role.
        if is_platform_superadmin(user_id, is_super_admin_type):
            return

        perms = await repo.permissions_for_user(user_id)
        if permission not in perms:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
```

Apply the same superadmin bypass inside `require_permission_with_identity`, immediately before its `perms = await repo.permissions_for_user(user_id)` line, returning `user_id` instead of `None`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/authz/ -v
```

Expected: PASS, including the pre-existing `test_deps.py` and `test_scope_enforcement.py` cases.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/authz/deps.py \
        backend/apps/backend/src/chatbot/features/authz/test_deps.py
git commit -m "feat(authz): platform-superadmin gate, outside RBAC by design"
```

---

### Task 3: The registry and the store

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/tenant_config/__init__.py` (empty)
- Create: `backend/apps/backend/src/chatbot/features/tenant_config/custom_features.py`
- Test: `backend/apps/backend/src/chatbot/features/tenant_config/test_custom_features.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `CustomFeature` dataclass: `key: str`, `label: str`, `group: str`, `permission: str | None`, `kind: str`.
  - `CUSTOM_FEATURE_REGISTRY: dict[str, CustomFeature]` — 24 `kind="surface"` entries.
  - `BEHAVIOR_FLAGS: dict[str, str]` — behaviour key → `Settings` attribute name.
  - `CustomFeatureStore(settings)` with `async get_all() -> dict[str, bool]` and `async set(key: str, enabled: bool) -> None`.
  - `enabled_features(stored: dict[str, bool]) -> list[str]` — sorted keys that are both registered and true.

- [ ] **Step 1: Write the failing tests**

Create `test_custom_features.py`:

```python
from __future__ import annotations

import pytest

from chatbot.features.tenant_config.custom_features import (
    BEHAVIOR_FLAGS,
    CUSTOM_FEATURE_REGISTRY,
    enabled_features,
)
from chatbot.features.authz.seed import PERMISSION_REGISTRY


def test_an_unwritten_store_yields_no_features() -> None:
    """The whole point: a tenant nobody has configured opens as a blank CRM.
    "Starts empty" is a property of the data model, not a value someone has
    to remember to set."""
    assert enabled_features({}) == []


def test_only_registered_and_true_keys_are_enabled() -> None:
    stored = {"knowledge": True, "cases": False, "not_a_real_feature": True}
    assert enabled_features(stored) == ["knowledge"]


def test_registry_covers_every_expected_surface() -> None:
    assert len(CUSTOM_FEATURE_REGISTRY) == 24
    assert all(f.kind == "surface" for f in CUSTOM_FEATURE_REGISTRY.values())
    for key in ("knowledge", "cases", "workforce", "customer360", "roles_permissions"):
        assert key in CUSTOM_FEATURE_REGISTRY


def test_every_paired_permission_actually_exists() -> None:
    """A typo here is a page that no role can ever reach, and nothing else in
    the system would report it."""
    for feature in CUSTOM_FEATURE_REGISTRY.values():
        if feature.permission is not None:
            assert feature.permission in PERMISSION_REGISTRY, feature.key


def test_behavior_flags_name_real_settings_fields() -> None:
    from chatbot.platform.config import Settings

    for key, attr in BEHAVIOR_FLAGS.items():
        assert hasattr(Settings(), attr), f"{key} -> {attr}"


def test_surface_and_behavior_keys_do_not_collide() -> None:
    assert not (set(CUSTOM_FEATURE_REGISTRY) & set(BEHAVIOR_FLAGS))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_custom_features.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'chatbot.features.tenant_config'`.

- [ ] **Step 3: Implement**

Create the empty `__init__.py`, then `custom_features.py`:

```python
"""The custom-feature switchboard: which surfaces a tenant's CRM has.

Two gates exist in this product and they answer different questions. A
FEATURE asks "is this capability part of this tenant's product at all?" and
is owned by the platform superadmin. A PERMISSION asks "which of the enabled
capabilities may this person use?" and is owned by the tenant's own
administrator. A surface renders only when both agree, so a tenant with every
feature off opens blank no matter how permissive its roles are.

An absent key is OFF. There is no seeding, no default-on list and no
first-boot marker: "a new tenant opens empty" is a property of the data model
rather than a value somebody has to remember to set.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# One document, not one per key: this is read on every SPA page load, so a
# single get() beats N reads. The term dictionary (see the term-dictionary
# spec) lands in this same document for the same reason.
_COLLECTION = "platform_config"
_DOCUMENT = "custom_features"


@dataclass(frozen=True)
class CustomFeature:
    key: str
    label: str
    group: str
    permission: str | None
    kind: str = "surface"


def _f(key: str, label: str, group: str, permission: str | None) -> CustomFeature:
    return CustomFeature(key=key, label=label, group=group, permission=permission)


# The closed set of toggleable surfaces. Static rather than store-driven: a
# feature that can be enabled by typing its name is one that can be enabled by
# MIStyping something else.
CUSTOM_FEATURE_REGISTRY: dict[str, CustomFeature] = {
    f.key: f
    for f in (
        _f("ai_assist", "AI reply suggestions", "AI", None),
        _f("copilot", "Ask Copilot panel", "AI", None),
        _f("faq_suggestion_popup", "FAQ suggestion strip", "AI", None),
        _f("translate", "Message translation", "AI", "translation.use"),
        _f("knowledge", "Knowledge Base console", "Knowledge", "knowledge.edit"),
        _f("reports_departments", "Departments report", "Reports", None),
        _f("reports_case_lifecycle", "Case lifecycle report", "Reports", None),
        _f("reports_anomaly", "Anomaly report", "Reports", None),
        _f("reports_weekly", "Weekly report", "Reports", None),
        _f("cases", "Cases list", "Cases", "cases.view"),
        _f("taxonomy", "Case taxonomy admin", "Cases", "taxonomy.manage"),
        _f("rsa_incidents", "Field incident log", "Cases", "sla.manage"),
        _f("workforce", "Workforce dashboard", "Operations", "workforce.view"),
        _f("agent_softphone", "Agent softphone", "Operations", "voice.answer"),
        _f("sla_policies", "SLA policies", "Operations", "sla.manage"),
        _f("escalation_routing", "Escalation routing", "Operations", "escalation.manage"),
        _f("inbound_alerts", "Inbound alerts", "Operations", "alerts.manage"),
        _f("alert_preferences", "Alert preferences", "Operations", "alerts.set_own_preferences"),
        _f("agent_status", "Availability status selector", "Operations", "presence.set_own_status"),
        _f("agent_priorities", "Agent channel priorities", "Operations", "workforce.manage"),
        _f("customer360", "Customer 360", "Data", "customer360.view"),
        _f("integrations", "Business system integration", "Data", "integration.manage"),
        _f("audit_log", "Audit log", "Admin", "audit.view"),
        _f("roles_permissions", "Roles & permissions", "Admin", "roles.manage"),
    )
}

# Backend runtime behaviours with no UI of their own. Phase 1 does NOT make
# these toggleable -- they are read from `Settings` at boot, so moving them
# into the store means runtime-mutable settings with their own caching and
# invalidation story. They are listed here so the switchboard can show them
# read-only: a page that silently omits half a tenant's configuration is worse
# than one that shows it and says who owns it.
BEHAVIOR_FLAGS: dict[str, str] = {
    "behavior_lifecycle": "lifecycle_enabled",
    "behavior_routing": "routing_enabled",
    "behavior_presence_tracking": "presence_tracking_enabled",
    "behavior_sla_engine": "sla_engine_enabled",
    "behavior_escalation_email": "escalation_email_enabled",
    "behavior_email_escalation": "email_escalation_enabled",
    "behavior_phone_handoff": "phone_handoff_enabled",
    "behavior_phone_recording": "phone_recording_enabled",
    "behavior_kb_grounded_replies": "kb_grounded_replies",
    "behavior_knowledge_pg": "knowledge_pg_enabled",
    "behavior_chat_agent": "chat_agent_enabled",
    "behavior_rbac": "rbac_enabled",
}


def enabled_features(stored: dict[str, bool]) -> list[str]:
    """Registered keys that the store says are on. Unknown keys are ignored
    rather than raising -- a key left behind by a retired feature must not be
    able to 500 every page load in the tenant that still has it stored."""
    return sorted(k for k, v in stored.items() if v and k in CUSTOM_FEATURE_REGISTRY)


class CustomFeatureStore:
    """Firestore-backed, one document per tenant. Mirrors PicStore/DealerStore:
    lazy client, `asyncio.to_thread` around the blocking SDK, fail-closed."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached_client: firestore.Client | None = None

    def _client(self) -> firestore.Client:
        if self._cached_client is None:
            self._cached_client = firestore.Client(project=self._settings.gcp_project_id)
        return self._cached_client

    def _doc_ref(self) -> firestore.DocumentReference:
        return self._client().collection(_COLLECTION).document(_DOCUMENT)

    async def get_all(self) -> dict[str, bool]:
        """Fail CLOSED. An unreachable store yields {} -- every feature off --
        because the alternative is briefly showing a tenant surfaces it does
        not have."""
        try:
            snap = await asyncio.to_thread(self._doc_ref().get)
        except Exception as e:
            _log.error("custom_feature_store_get_failed", error=str(e))
            return {}
        if not snap.exists:
            return {}
        raw = (snap.to_dict() or {}).get("features") or {}
        return {str(k): bool(v) for k, v in raw.items()}

    async def set(self, key: str, enabled: bool) -> None:
        """Merge-write a single key. A bare set() would drop every other
        feature, which on this document means blanking the tenant's CRM."""
        await asyncio.to_thread(
            self._doc_ref().set, {"features": {key: enabled}}, merge=True
        )
```

If `Settings` has no `gcp_project_id` field, use whatever attribute `PicStore._client()` in `features/chat/pic_store.py` uses — copy it verbatim rather than inventing a name.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_custom_features.py -v
```

Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/tenant_config/
git commit -m "feat(tenant-config): custom-feature registry and per-tenant store"
```

---

### Task 4: The switchboard endpoints

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/tenant_config/custom_features_router.py`
- Test: `backend/apps/backend/src/chatbot/features/tenant_config/test_custom_features_router.py`

**Interfaces:**
- Consumes: `CustomFeatureStore`, `CUSTOM_FEATURE_REGISTRY`, `BEHAVIOR_FLAGS`, `enabled_features` (Task 3); `require_platform_superadmin`, `is_platform_superadmin` (Task 2); `TokenValidator.resolve_identity` (Task 1).
- Produces: `build_custom_features_router(store, validator, settings) -> APIRouter` mounted at `/admin/custom-features`.

- [ ] **Step 1: Write the failing tests**

Create `test_custom_features_router.py`:

```python
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.tenant_config.custom_features_router import (
    build_custom_features_router,
)
from chatbot.platform.config import Settings


class _FakeStore:
    def __init__(self, initial: dict[str, bool] | None = None) -> None:
        self.data = dict(initial or {})

    async def get_all(self) -> dict[str, bool]:
        return dict(self.data)

    async def set(self, key: str, enabled: bool) -> None:
        self.data[key] = enabled


class _FakeValidator:
    def __init__(self, identity):
        self._identity = identity

    async def resolve_identity(self, *_args):
        return self._identity


def _client(store, identity) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_custom_features_router(store, _FakeValidator(identity), Settings())
    )
    return TestClient(app)


_SESSION = {
    "x-chatwoot-access-token": "tok",
    "x-chatwoot-client": "cli",
    "x-chatwoot-uid": "uid@x",
}


def test_unconfigured_tenant_reports_no_features() -> None:
    res = _client(_FakeStore(), (9, False)).get("/admin/custom-features", headers=_SESSION)
    assert res.status_code == 200
    assert res.json()["features"] == []


def test_read_reports_enabled_keys_and_superadmin_flag() -> None:
    store = _FakeStore({"knowledge": True, "cases": False})
    res = _client(store, (1, False)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert body["features"] == ["knowledge"]
    assert body["is_superadmin"] is True


def test_read_hides_the_registry_from_a_non_superadmin() -> None:
    """A tenant admin must not be able to enumerate which surfaces exist but
    are switched off — that is a product roadmap, and an upsell surface we
    deliberately do not put inside the customer's console."""
    res = _client(_FakeStore(), (9, False)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert body["is_superadmin"] is False
    assert body["registry"] == []
    assert body["behavior"] == {}


def test_read_exposes_the_registry_to_a_superadmin() -> None:
    res = _client(_FakeStore(), (7, True)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert len(body["registry"]) == 24
    assert body["registry"][0]["key"]
    assert body["registry"][0]["label"]
    assert "behavior_lifecycle" in body["behavior"]


def test_write_is_refused_for_a_non_superadmin() -> None:
    res = _client(_FakeStore(), (9, False)).post(
        "/admin/custom-features", json={"key": "knowledge", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 403


def test_write_toggles_a_registered_key() -> None:
    store = _FakeStore()
    res = _client(store, (1, False)).post(
        "/admin/custom-features", json={"key": "knowledge", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 200
    assert store.data == {"knowledge": True}


def test_write_rejects_an_unregistered_key_with_400() -> None:
    res = _client(_FakeStore(), (1, False)).post(
        "/admin/custom-features", json={"key": "nope", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 400


def test_write_rejects_a_behavior_key_with_409() -> None:
    """A real key that is simply not writable yet — distinct from one that
    does not exist, so the operator can tell "not yet" from "typo"."""
    res = _client(_FakeStore(), (1, False)).post(
        "/admin/custom-features",
        json={"key": "behavior_lifecycle", "enabled": True},
        headers=_SESSION,
    )
    assert res.status_code == 409


def test_read_401s_without_a_session() -> None:
    res = _client(_FakeStore(), (1, False)).get("/admin/custom-features")
    assert res.status_code == 401


def test_a_write_that_did_not_persist_reports_503_not_200() -> None:
    """A 200 on a dropped write tells the superadmin the tenant's product
    changed when it did not. They would go looking for the bug in the SPA."""

    class _BrokenStore(_FakeStore):
        async def set(self, key: str, enabled: bool) -> None:
            raise RuntimeError("firestore unavailable")

    res = _client(_BrokenStore(), (1, False)).post(
        "/admin/custom-features", json={"key": "knowledge", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 503
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_custom_features_router.py -v
```

Expected: FAIL — `ModuleNotFoundError: ...custom_features_router`.

- [ ] **Step 3: Implement**

```python
"""The custom-feature switchboard's HTTP surface.

Reads are open to any signed-in Chatwoot session -- there is nothing
sensitive in "what is switched on in the CRM I am already looking at" -- but
the REGISTRY is superadmin-only. A tenant admin who could enumerate the
switched-off surfaces would be reading a product roadmap.

Writes are gated on `require_platform_superadmin`, deliberately outside RBAC.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.authz.deps import (
    is_platform_superadmin,
    require_platform_superadmin,
)
from chatbot.features.tenant_config.custom_features import (
    BEHAVIOR_FLAGS,
    CUSTOM_FEATURE_REGISTRY,
    enabled_features,
)

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.tenant_config.custom_features import CustomFeatureStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class ToggleBody(BaseModel):
    key: str
    enabled: bool


def build_custom_features_router(
    store: CustomFeatureStore,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/custom-features", tags=["custom-features"])
    superadmin_only = require_platform_superadmin(validator=validator, settings=settings)

    async def _identity(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> tuple[int, bool]:
        if not x_chatwoot_access_token or not x_chatwoot_client or not x_chatwoot_uid:
            raise HTTPException(status_code=401, detail="Chatwoot session required")
        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return identity

    @router.get("")
    async def read(identity: tuple[int, bool] = Depends(_identity)) -> dict:
        user_id, is_super_admin_type = identity
        superadmin = is_platform_superadmin(user_id, is_super_admin_type)
        stored = await store.get_all()

        registry: list[dict] = []
        behavior: dict[str, bool] = {}
        if superadmin:
            registry = [
                {
                    "key": f.key,
                    "label": f.label,
                    "group": f.group,
                    "enabled": bool(stored.get(f.key, False)),
                }
                for f in CUSTOM_FEATURE_REGISTRY.values()
            ]
            # Read-only, env-owned. Shown so the page tells the whole truth
            # about the tenant rather than implying these do not exist.
            behavior = {
                key: bool(getattr(settings, attr, False))
                for key, attr in BEHAVIOR_FLAGS.items()
            }

        return {
            "features": enabled_features(stored),
            "is_superadmin": superadmin,
            "registry": registry,
            "behavior": behavior,
        }

    @router.post("")
    async def toggle(
        body: ToggleBody,
        _user_id: int = Depends(superadmin_only),
    ) -> dict:
        if body.key in BEHAVIOR_FLAGS:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{body.key} is env-controlled and not yet switchable here"
                ),
            )
        if body.key not in CUSTOM_FEATURE_REGISTRY:
            raise HTTPException(status_code=400, detail=f"Unknown feature: {body.key}")
        try:
            await store.set(body.key, body.enabled)
        except Exception as e:
            # 503, not a bare 500 and never a 200: reporting success for a
            # write that did not land tells the superadmin this tenant's
            # product changed when it did not.
            _log.error("custom_feature_write_failed", key=body.key, error=str(e))
            raise HTTPException(status_code=503, detail="Could not save") from e
        return {"key": body.key, "enabled": body.enabled, "status": "ok"}

    return router
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/ -v
```

Expected: PASS, 15 tests across both files.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/tenant_config/
git commit -m "feat(tenant-config): custom-feature switchboard endpoints"
```

---

### Task 5: Wire the router into the app

**Files:**
- Modify: `backend/apps/backend/src/chatbot/main.py`
- Test: `backend/apps/backend/src/chatbot/features/tenant_config/test_custom_features_wiring.py`

**Interfaces:**
- Consumes: `build_custom_features_router` (Task 4), `CustomFeatureStore` (Task 3).
- Produces: `GET/POST /admin/custom-features` on the real app.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from chatbot.main import create_app


def test_custom_features_routes_are_mounted() -> None:
    """Reads must be mounted unconditionally: the SPA calls this on every page
    load, and a 404 there fails closed into a blank CRM on a tenant that has
    features switched on."""
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/admin/custom-features" in paths
```

If `create_app` is not the factory name, read `main.py` and use whatever the existing `test_wiring.py` in `features/metrics/` calls.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-dummy uv run pytest \
  src/chatbot/features/tenant_config/test_custom_features_wiring.py -v
```

Expected: FAIL — the path is absent.

- [ ] **Step 3: Implement**

In `main.py`, near the existing `pic_store = PicStore(settings)` construction (around line 440), add:

```python
    custom_feature_store = CustomFeatureStore(settings)
```

with the import at the top of the same block:

```python
    from chatbot.features.tenant_config.custom_features import CustomFeatureStore
```

Then mount the router. It needs a `TokenValidator`, and the existing one is built only inside the `rbac_enabled` branch — the switchboard must not depend on RBAC being on, so construct one unconditionally:

```python
    # Mounted unconditionally, unlike the RBAC admin routers: the switchboard
    # is a platform-level authority that exists whether or not a tenant has
    # opted into RBAC, and the SPA reads it on every page load.
    from chatbot.features.authz.identity import TokenValidator as _TokenValidator
    from chatbot.features.tenant_config.custom_features_router import (
        build_custom_features_router,
    )

    app.include_router(
        build_custom_features_router(
            custom_feature_store,
            _TokenValidator(settings),
            settings,
        )
    )
```

Place this immediately after the `_wire_metrics_features(app, settings)` call so it is outside every feature-flag branch.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-dummy uv run pytest \
  src/chatbot/features/tenant_config/ src/chatbot/features/authz/ -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole backend suite**

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-dummy uv run pytest src/chatbot -q
```

Expected: PASS. Baseline before this plan was 3186 passed, 2 skipped — the count should now be higher, with no failures.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/main.py \
        backend/apps/backend/src/chatbot/features/tenant_config/
git commit -m "feat(tenant-config): mount the switchboard unconditionally"
```

---

### Task 6: SPA client and composable

The first fork patch. Both files are new, so no upstream context lines are needed.

**Files:**
- Create: `deploy/chatwoot-fork/patches/0072-custom-features-composable.patch`

**Interfaces:**
- Consumes: `GET/POST /admin/custom-features` (Task 4).
- Produces:
  - `dashboard/api/protonAdmin.js`: `fetchCustomFeatures()`, `setCustomFeature(key, enabled)`.
  - `dashboard/composables/useCustomFeatures.js`: `{ loading, loadFailed, hasFeature, isSuperadmin, registry, behavior, refresh }`.

- [ ] **Step 1: Write the patch**

`protonAdmin.js` is an existing file, so its hunk needs real context. Take the context lines verbatim from the end of `0025-sla-policies-admin.patch`'s `protonAdmin.js` hunk — the `myPermissions` function is the last thing in that file, so append after it.

Create `deploy/chatwoot-fork/patches/0072-custom-features-composable.patch`:

```diff
diff --git a/app/javascript/dashboard/api/protonAdmin.js b/app/javascript/dashboard/api/protonAdmin.js
--- a/app/javascript/dashboard/api/protonAdmin.js
+++ b/app/javascript/dashboard/api/protonAdmin.js
@@ -66,3 +66,17 @@ export async function myPermissions() {
   const data = await adminRequest('/authz/permissions');
   return Array.isArray(data.permissions) ? data.permissions : [];
 }
+
+// ── Custom features (the platform switchboard) ─────────────────────────────
+
+export async function fetchCustomFeatures() {
+  return adminRequest('/admin/custom-features');
+}
+
+export async function setCustomFeature(key, enabled) {
+  return adminRequest('/admin/custom-features', {
+    method: 'POST',
+    body: { key, enabled },
+  });
+}
diff --git a/app/javascript/dashboard/composables/useCustomFeatures.js b/app/javascript/dashboard/composables/useCustomFeatures.js
new file mode 100644
--- /dev/null
+++ b/app/javascript/dashboard/composables/useCustomFeatures.js
@@ -0,0 +1,58 @@
+// useCustomFeatures.js — OUR file. The read side of the platform feature
+// switchboard. Deliberately shaped exactly like useProtonPermissions.js:
+// module-level cache shared by every importer, one in-flight promise, and a
+// fail-CLOSED error path.
+//
+// Fail-closed is the whole point and it has a visible cost: a backend blip
+// renders the CRM empty rather than briefly rendering surfaces the tenant
+// does not have. That is the right trade for a licensing gate, but it means
+// an outage looks like a missing product — so `loadFailed` is exposed
+// separately, letting the nav say "couldn't load" instead of silently
+// showing nothing.
+import { ref } from 'vue';
+import { fetchCustomFeatures } from 'dashboard/api/protonAdmin';
+
+const features = ref(null); // null = not yet loaded
+const isSuperadmin = ref(false);
+const registry = ref([]);
+const behavior = ref({});
+const loading = ref(false);
+const loadFailed = ref(false);
+let loadPromise = null;
+
+function ensureLoaded() {
+  if (features.value !== null || loadPromise) return loadPromise;
+  loading.value = true;
+  loadPromise = fetchCustomFeatures()
+    .then(data => {
+      features.value = Array.isArray(data.features) ? data.features : [];
+      isSuperadmin.value = Boolean(data.is_superadmin);
+      registry.value = Array.isArray(data.registry) ? data.registry : [];
+      behavior.value = data.behavior || {};
+      loadFailed.value = false;
+    })
+    .catch(() => {
+      features.value = []; // fail closed — no feature renders
+      isSuperadmin.value = false;
+      registry.value = [];
+      behavior.value = {};
+      loadFailed.value = true;
+    })
+    .finally(() => {
+      loading.value = false;
+    });
+  return loadPromise;
+}
+
+// Force a re-read after a toggle, so the nav reflects the change without a
+// page reload.
+async function refresh() {
+  features.value = null;
+  loadPromise = null;
+  return ensureLoaded();
+}
+
+export function useCustomFeatures() {
+  ensureLoaded();
+  return {
+    loading,
+    loadFailed,
+    isSuperadmin,
+    registry,
+    behavior,
+    refresh,
+    hasFeature: key => (features.value || []).includes(key),
+  };
+}
```

- [ ] **Step 2: Verify the patch applies**

There is no upstream checkout in this sandbox, so verify structurally instead — confirm the new-file hunk's line count matches its body, and that the `protonAdmin.js` context lines are character-identical to the tail of `0025-sla-policies-admin.patch`:

```bash
cd deploy/chatwoot-fork/patches
rtk proxy grep -c '^+' 0072-custom-features-composable.patch
rtk proxy grep -A3 'export async function myPermissions' 0025-sla-policies-admin.patch
```

Expected: the `myPermissions` body in `0025` matches the three context lines in `0072`'s first hunk exactly.

- [ ] **Step 3: Commit**

```bash
git add deploy/chatwoot-fork/patches/0072-custom-features-composable.patch
git commit -m "feat(chatwoot-fork): custom-features client and fail-closed composable"
```

---

### Task 7: The switchboard admin page

**Files:**
- Create: `deploy/chatwoot-fork/patches/0073-custom-features-page.patch`

**Interfaces:**
- Consumes: `useCustomFeatures` (Task 6), `setCustomFeature` (Task 6).
- Produces: route `custom_features` at `admin/custom-features`, rendered by `CustomFeaturesPage.vue`.

- [ ] **Step 1: Write the patch**

The route hunk modifies `dashboard.routes.js`. Take its context lines verbatim from `0053-workforce-dashboard.patch`, which adds a route to the same file and shows the exact surrounding lines.

```diff
diff --git a/app/javascript/dashboard/views/CustomFeaturesPage.vue b/app/javascript/dashboard/views/CustomFeaturesPage.vue
new file mode 100644
--- /dev/null
+++ b/app/javascript/dashboard/views/CustomFeaturesPage.vue
@@ -0,0 +1,96 @@
+<!-- CustomFeaturesPage.vue — OUR file. The platform switchboard: which
+     custom surfaces this tenant's CRM has.
+
+     Superadmin-only, and invisible rather than disabled for everyone else.
+     A tenant admin who could see the switched-off surfaces would be reading
+     a product roadmap; the backend already withholds the registry from a
+     non-superadmin, so this component simply has nothing to render for them.
+
+     Behaviour flags are shown read-only. They are env-owned and not yet
+     switchable, and a page that silently omitted them would imply this
+     tenant has no configuration beyond these toggles. -->
+<script setup>
+import { ref } from 'vue';
+import { useCustomFeatures } from 'dashboard/composables/useCustomFeatures';
+import { setCustomFeature } from 'dashboard/api/protonAdmin';
+
+const { loading, loadFailed, isSuperadmin, registry, behavior, refresh } =
+  useCustomFeatures();
+
+const saving = ref('');
+const error = ref('');
+
+function groups(rows) {
+  const out = [];
+  rows.forEach(row => {
+    const found = out.find(g => g.name === row.group);
+    if (found) found.rows.push(row);
+    else out.push({ name: row.group, rows: [row] });
+  });
+  return out;
+}
+
+async function toggle(row) {
+  saving.value = row.key;
+  error.value = '';
+  try {
+    await setCustomFeature(row.key, !row.enabled);
+    await refresh();
+  } catch (e) {
+    error.value = e.message || 'Could not save';
+  } finally {
+    saving.value = '';
+  }
+}
+</script>
+
+<template>
+  <div class="p-6 overflow-auto">
+    <h1 class="text-lg font-semibold text-n-slate-12 mb-1">Custom features</h1>
+    <p class="text-sm text-n-slate-11 mb-6">
+      Which surfaces this tenant's CRM has. Every feature is off until switched
+      on here.
+    </p>
+
+    <woot-loading-state v-if="loading" message="Loading features" />
+
+    <div v-else-if="loadFailed" class="text-sm text-n-amber-11">
+      Could not load the feature list. Nothing has been changed.
+    </div>
+
+    <div v-else-if="!isSuperadmin" class="text-sm text-n-slate-11">
+      This page is available to platform superadmins only.
+    </div>
+
+    <template v-else>
+      <div v-if="error" class="text-sm text-n-ruby-11 mb-4">{{ error }}</div>
+
+      <div v-for="group in groups(registry)" :key="group.name" class="mb-6">
+        <h2 class="text-sm font-semibold text-n-slate-12 mb-2">
+          {{ group.name }}
+        </h2>
+        <div
+          v-for="row in group.rows"
+          :key="row.key"
+          class="flex items-center justify-between py-2 border-b border-n-container"
+        >
+          <span class="text-sm text-n-slate-12">{{ row.label }}</span>
+          <button
+            class="text-sm px-3 py-1 rounded-lg"
+            :class="row.enabled ? 'bg-n-teal-3 text-n-teal-11' : 'bg-n-slate-3 text-n-slate-11'"
+            :disabled="saving === row.key"
+            @click="toggle(row)"
+          >
+            {{ row.enabled ? 'On' : 'Off' }}
+          </button>
+        </div>
+      </div>
+
+      <h2 class="text-sm font-semibold text-n-slate-12 mb-2 mt-8">
+        Env-controlled behaviour
+      </h2>
+      <p class="text-xs text-n-slate-11 mb-2">
+        Read-only. These are set in the tenant's environment file.
+      </p>
+      <div
+        v-for="(value, key) in behavior"
+        :key="key"
+        class="flex items-center justify-between py-1.5 text-sm"
+      >
+        <span class="text-n-slate-11">{{ key }}</span>
+        <span class="text-n-slate-12">{{ value ? 'on' : 'off' }}</span>
+      </div>
+    </template>
+  </div>
+</template>
diff --git a/app/javascript/dashboard/routes/dashboard/dashboard.routes.js b/app/javascript/dashboard/routes/dashboard/dashboard.routes.js
--- a/app/javascript/dashboard/routes/dashboard/dashboard.routes.js
+++ b/app/javascript/dashboard/routes/dashboard/dashboard.routes.js
@@ -84,6 +84,12 @@
           path: 'proton/workforce',
           name: 'proton_workforce',
           component: () => import('../../views/ProtonWorkforceDashboardPage.vue'),
+        },
+        {
+          path: 'admin/custom-features',
+          name: 'custom_features',
+          component: () => import('../../views/CustomFeaturesPage.vue'),
+          meta: { permissions: ['administrator'] },
         },
```

Before finalising, open `0053-workforce-dashboard.patch` and copy its `dashboard.routes.js` hunk header and context lines exactly — the `@@` arithmetic and the surrounding lines must be that file's, not invented. The `meta.permissions` guard is Chatwoot's own route guard and is a coarse pre-filter only; the real gate is the backend's 403 plus the `isSuperadmin` branch in the template.

- [ ] **Step 2: Add the nav entry**

Append a third hunk to the same patch adding the sidebar link. Copy the hunk header and context lines verbatim from `0053-workforce-dashboard.patch`, which adds a nav entry to the same file in the same place — only the added lines below are new:

```diff
 import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';
+import { useCustomFeatures } from 'dashboard/composables/useCustomFeatures';
```

```diff
 const { hasPermission: protonHasPermission } = useProtonPermissions();
+const { isSuperadmin } = useCustomFeatures();
```

```diff
+    ...(isSuperadmin.value
+      ? [
+          {
+            name: 'Custom features',
+            label: 'Custom features',
+            icon: 'settings',
+            to: accountScopedRoute('custom_features'),
+          },
+        ]
+      : []),
```

`isSuperadmin` is a `ref`, so the nav re-evaluates once the composable's fetch resolves — the entry appears a moment after load rather than never. Match whatever icon name `0053` uses if `settings` is not a valid icon in this Chatwoot version.

- [ ] **Step 3: Verify structurally**

```bash
cd deploy/chatwoot-fork/patches
rtk proxy grep -n '@@' 0073-custom-features-page.patch
rtk proxy grep -n '@@' 0053-workforce-dashboard.patch
```

Expected: the `dashboard.routes.js` and sidebar context lines in `0073` are character-identical to their counterparts in `0053`.

- [ ] **Step 4: Commit**

```bash
git add deploy/chatwoot-fork/patches/0073-custom-features-page.patch
git commit -m "feat(chatwoot-fork): superadmin custom-features switchboard page"
```

---

### Task 8: Gate the existing surfaces on features

The last patch. Every nav entry and route that today checks only a permission gains a feature check beside it.

**Files:**
- Create: `deploy/chatwoot-fork/patches/0074-feature-gate-surfaces.patch`

**Interfaces:**
- Consumes: `useCustomFeatures().hasFeature` (Task 6), the 24 registry keys (Task 3).
- Produces: nav and routes rendering only when `hasFeature(key) && hasPermission(perm)`.

- [ ] **Step 1: Map every surface to its key before editing**

```bash
cd deploy/chatwoot-fork/patches
rtk proxy grep -rhoE "accountScopedRoute\('proton_[a-z0-9_]+'" *.patch | sort -u
rtk proxy grep -rn "protonHasPermission\('[a-z0-9_.]+'\)" *.patch | head -40
```

Write the mapping down before touching anything. It must match `CUSTOM_FEATURE_REGISTRY` exactly — `proton_knowledge` → `knowledge`, `proton_cases` → `cases`, `proton_workforce` → `workforce`, `proton_customer360` → `customer360`, `proton_taxonomy` → `taxonomy`, `proton_rsa_incidents` → `rsa_incidents`, `proton_sla_policies` → `sla_policies`, `proton_escalation_routing` → `escalation_routing`, `proton_audit_log` → `audit_log`, `proton_roles_permissions` → `roles_permissions`, `proton_integrations` → `integrations`, `proton_departments_reports` → `reports_departments`, `proton_case_lifecycle_reports` → `reports_case_lifecycle`, `proton_anomaly_reports` → `reports_anomaly`, `proton_weekly_report` → `reports_weekly`, `proton_alert_preferences` → `alert_preferences`, `proton_my_status` → `agent_status`.

- [ ] **Step 2: Write the patch**

In the sidebar file, import the composable alongside the existing permissions one and destructure `hasFeature`:

```diff
 import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';
+import { useCustomFeatures } from 'dashboard/composables/useCustomFeatures';
```

```diff
 const { hasPermission: protonHasPermission } = useProtonPermissions();
+const { hasFeature: protonHasFeature } = useCustomFeatures();
```

Then convert each nav spread from a permission-only check to both gates:

```diff
-    ...(protonHasPermission('workforce.view')
+    ...(protonHasFeature('workforce') && protonHasPermission('workforce.view')
```

Repeat for every row in the Step 1 mapping. For surfaces with no paired permission (the four report pages, `ai_assist`, `copilot`, `faq_suggestion_popup`), the feature check replaces whatever gate is there today rather than joining it.

Take each hunk's context from the patch that introduced that nav entry — `0053` for workforce, `0041` for customer360, `0060` for taxonomy, and so on. Do not guess context lines.

- [ ] **Step 3: Verify no surface was missed**

```bash
cd deploy/chatwoot-fork/patches
rtk proxy grep -c 'protonHasFeature' 0074-feature-gate-surfaces.patch
```

Expected: at least 17 — one per route in the Step 1 mapping. If the count is lower, a surface is ungated and will render on a blank tenant.

- [ ] **Step 4: Commit**

```bash
git add deploy/chatwoot-fork/patches/0074-feature-gate-surfaces.patch
git commit -m "feat(chatwoot-fork): gate every custom surface on its feature key"
```

---

## Rollout (after all tasks pass)

Not part of the TDD loop — these are deploy steps, ordered so nothing goes dark. proton and aeon360 are live and currently reach every surface.

1. **Backend image only.** Sync source to `/opt/platform`, then per tenant:
   `docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d --build backend`.
   Invisible to users: the SPA still reads the ERB-stamped list.
2. **Populate the stores** for proton and aeon360 — POST every registry key with `enabled: true`, as a superadmin session. Verify with `GET /admin/custom-features`: `features` must have 24 entries for both.
3. **Only then** build the Chatwoot image via Cloud Build (`amd64`, never on the prod VM) and pull it.

Skipping step 2 opens both live tenants' CRMs blank. `default` is deliberately left with an empty store — it becomes the working example of the new baseline.
