"""Data-scoped permissions: the scope model, its storage and its resolution
(Wave 2 Task 28, FIX-48). Enforcement on each surface is in
`test_scope_enforcement.py`."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.data_scope import (
    DENY_ALL,
    UNRESTRICTED,
    Scope,
    ScopeNotApplicable,
    apply_scope,
    build_scope_dependency,
    combine_role_scopes,
    conversation_dimensions,
    scope_predicates,
)
from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.platform.config import Settings, get_settings


class FakeValidator:
    """token -> (user_id, is_super_admin_type)."""

    def __init__(self, users: dict[str, tuple[int, bool]]) -> None:
        self._users = users

    async def resolve_identity(self, token, client, uid):
        return self._users.get(token)

    async def resolve_user_id(self, token, client, uid):
        identity = await self.resolve_identity(token, client, uid)
        return identity[0] if identity else None

    def display_name(self, user_id):
        return None


def session(token: str) -> dict[str, str]:
    return {
        "x-chatwoot-access-token": token,
        "x-chatwoot-client": "c",
        "x-chatwoot-uid": "u",
    }


async def make_repo(tmp_path, name: str = "scope") -> AuthzRepository:
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    return repo


def scoped_settings(**overrides) -> Settings:
    base = {"rbac_enabled": True, "data_scoped_rbac_enabled": True}
    base.update(overrides)
    return get_settings().model_copy(update=base)


def _scope_app(settings, repo, validator) -> TestClient:
    app = FastAPI()
    dep = build_scope_dependency(settings, repo=repo, validator=validator)

    @app.get("/whoami-scope")
    async def _w(scope: Scope = Depends(dep)) -> dict:
        return scope.to_dict()

    return TestClient(app)


# --- the model -------------------------------------------------------------


def test_scope_build_normalises_values() -> None:
    scope = Scope.build(
        inbox_ids=["3", 5, "x"], dealer_slugs=["Dealer_KL", "penang "], departments=["dept_Sales"]
    )
    assert scope.inbox_ids == frozenset({3, 5})
    assert scope.dealer_slugs == frozenset({"kl", "penang"})
    assert scope.departments == frozenset({"sales"})
    assert not scope.unrestricted


def test_combine_unions_values_and_ignores_unscoped_roles() -> None:
    kl = Scope.build(dealer_slugs=["kl"])
    penang = Scope.build(dealer_slugs=["penang"])
    assert combine_role_scopes([kl, penang]).dealer_slugs == {"kl", "penang"}
    assert combine_role_scopes([]) == UNRESTRICTED


def test_combine_keeps_every_restricted_dimension() -> None:
    combined = combine_role_scopes(
        [Scope.build(inbox_ids=[3]), Scope.build(dealer_slugs=["kl"])]
    )
    assert combined.inbox_ids == {3}
    assert combined.dealer_slugs == {"kl"}


def test_apply_scope_hides_rows_missing_a_restricted_dimension() -> None:
    rows = [
        {"id": 1, "inbox_id": 3, "labels": ["dealer_kl"]},
        {"id": 2, "inbox_id": 3, "labels": []},
        {"id": 3, "inbox_id": 4, "labels": ["dealer_kl"]},
    ]
    scope = Scope.build(inbox_ids=[3], dealer_slugs=["kl"])
    kept = apply_scope(rows, scope, conversation_dimensions)
    assert [r["id"] for r in kept] == [1]
    assert apply_scope(rows, UNRESTRICTED, conversation_dimensions) == rows
    assert apply_scope(rows, DENY_ALL, conversation_dimensions) == []


def test_scope_predicates_bind_values_and_refuse_unscopable_views() -> None:
    fragment, params = scope_predicates("v_dealer_escalation", Scope.build(dealer_slugs=["kl"]))
    assert fragment == "LOWER(dealer) IN (@scope_dealer_0)"
    assert params == {"scope_dealer_0": "kl"}
    with pytest.raises(ScopeNotApplicable):
        scope_predicates("v_csat", Scope.build(dealer_slugs=["kl"]))
    with pytest.raises(ScopeNotApplicable):
        scope_predicates("v_dealer_escalation", Scope.build(inbox_ids=[3]))
    assert scope_predicates("v_csat", UNRESTRICTED) == ("", {})


# --- storage ---------------------------------------------------------------


async def test_role_scope_round_trips_and_resolves(tmp_path) -> None:
    repo = await make_repo(tmp_path)
    await repo.create_role("kl_dealer", "KL dealer")
    await repo.set_role_scope("kl_dealer", Scope.build(dealer_slugs=["kl"], inbox_ids=[3]))
    assert (await repo.get_role_scope("kl_dealer")).dealer_slugs == {"kl"}
    await repo.assign_role(7, "agent")
    await repo.assign_role(7, "kl_dealer")
    eff = await repo.effective_scope(7)
    assert eff.dealer_slugs == {"kl"} and eff.inbox_ids == {3}
    # Saving an empty scope removes it: the role goes back to unrestricted.
    await repo.set_role_scope("kl_dealer", Scope())
    assert await repo.get_role_scope("kl_dealer") == UNRESTRICTED
    assert await repo.effective_scope(7) == UNRESTRICTED


# --- resolution (the dependency) ---------------------------------------------


async def test_admin_unrestricted(tmp_path) -> None:
    repo = await make_repo(tmp_path)
    await repo.create_role("kl_dealer", "KL dealer")
    await repo.set_role_scope("kl_dealer", Scope.build(dealer_slugs=["kl"]))
    # An administrator who ALSO holds a scoped role is still unrestricted.
    await repo.assign_role(5, "administrator")
    await repo.assign_role(5, "kl_dealer")
    validator = FakeValidator({"admin": (5, False), "super": (9, True)})
    client = _scope_app(scoped_settings(), repo, validator)
    assert client.get("/whoami-scope", headers=session("admin")).json()["unrestricted"] is True
    # Platform superadmin with no role at all: unrestricted.
    assert client.get("/whoami-scope", headers=session("super")).json()["unrestricted"] is True


async def test_empty_scope_unrestricted(tmp_path) -> None:
    """Flag on, nothing configured: everybody behaves exactly as before."""
    repo = await make_repo(tmp_path)
    await repo.assign_role(7, "agent")
    client = _scope_app(scoped_settings(), repo, FakeValidator({"agent": (7, False)}))
    body = client.get("/whoami-scope", headers=session("agent")).json()
    assert body["unrestricted"] is True
    assert body["deny_all"] is False


async def test_flag_off_unchanged(tmp_path) -> None:
    """Flag off: no session needed, scopes stored on roles are ignored."""
    repo = await make_repo(tmp_path)
    await repo.create_role("kl_dealer", "KL dealer")
    await repo.set_role_scope("kl_dealer", Scope.build(dealer_slugs=["kl"]))
    await repo.assign_role(7, "kl_dealer")
    settings = scoped_settings(data_scoped_rbac_enabled=False)
    client = _scope_app(settings, repo, FakeValidator({"agent": (7, False)}))
    assert client.get("/whoami-scope").json()["unrestricted"] is True
    assert client.get("/whoami-scope", headers=session("agent")).json()["unrestricted"] is True


async def test_scope_read_failure_fails_closed(tmp_path) -> None:
    repo = await make_repo(tmp_path)

    async def _boom(user_id):
        raise RuntimeError("rbac database unreachable")

    repo.effective_scope = _boom  # type: ignore[method-assign]
    client = _scope_app(scoped_settings(), repo, FakeValidator({"agent": (7, False)}))
    body = client.get("/whoami-scope", headers=session("agent")).json()
    assert body["deny_all"] is True
    assert body["unrestricted"] is False


async def test_flag_on_requires_a_session(tmp_path) -> None:
    repo = await make_repo(tmp_path)
    client = _scope_app(scoped_settings(), repo, FakeValidator({}))
    assert client.get("/whoami-scope", headers={"x-api-key": "shared"}).status_code == 401


def test_settings_accept_the_flag_with_rbac_and_refuse_it_without() -> None:
    assert Settings(data_scoped_rbac_enabled=True, rbac_enabled=True).data_scoped_rbac_enabled
    with pytest.raises(ValueError, match="DATA_SCOPED_RBAC_ENABLED requires RBAC_ENABLED"):
        Settings(data_scoped_rbac_enabled=True, rbac_enabled=False)
