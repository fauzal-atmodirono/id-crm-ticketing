import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror, ChatwootRoleMirrorError
from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.router import build_authz_router
from chatbot.features.authz.seed import seed_defaults
from chatbot.platform.config import get_settings


@pytest.fixture
async def client(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/router_test.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=1, role_id="administrator")
    await repo.assign_role(chatwoot_user_id=2, role_id="agent")

    respx_mock.get(f"{settings.chatwoot_api_url}/auth/validate_token").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"data": {"id": 1 if request.headers["access-token"] == "admin-tok" else 2}}
        )
    )
    validator = TokenValidator(settings)
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings))
    return TestClient(app)


def test_permissions_endpoint_returns_callers_own_permissions(client):
    res = client.get("/authz/permissions", headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"})
    assert res.status_code == 200
    assert res.json()["permissions"] == ["knowledge.edit"]


def test_check_endpoint(client):
    res = client.get(
        "/authz/check",
        params={"permission": "sla.manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.json() == {"allowed": True}
    res = client.get(
        "/authz/check",
        params={"permission": "sla.manage"},
        headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"},
    )
    assert res.json() == {"allowed": False}


def test_create_role_requires_roles_manage_permission(client):
    res = client.post(
        "/authz/roles",
        json={"id": "leader", "name": "Team Leader", "description": ""},
        headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"},
    )
    assert res.status_code == 403


def test_administrator_can_create_role_and_assign(client):
    res = client.post(
        "/authz/roles",
        json={"id": "leader", "name": "Team Leader", "description": ""},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 200

    res = client.post(
        "/authz/roles/leader/assign",
        json={"chatwoot_user_id": 99},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 200


def test_permission_registry_endpoint(client):
    res = client.get("/authz/permission-registry", headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"})
    assert res.status_code == 200
    data = res.json()
    assert "permissions" in data
    assert len(data["permissions"]) > 0
    assert all("key" in p and "description" in p for p in data["permissions"])


def test_permission_registry_requires_roles_manage_permission(client):
    res = client.get("/authz/permission-registry", headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"})
    assert res.status_code == 403


def test_role_permissions_endpoint(client):
    res = client.get("/authz/roles/administrator/permissions", headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"})
    assert res.status_code == 200
    data = res.json()
    assert "permissions" in data
    assert isinstance(data["permissions"], list)


def test_role_permissions_requires_roles_manage_permission(client):
    res = client.get("/authz/roles/administrator/permissions", headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"})
    assert res.status_code == 403


def test_grant_role_permission_endpoint(client):
    res = client.post(
        "/authz/roles/agent/permissions",
        json={"permission_key": "sla.manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    # Verify the permission was actually granted
    res = client.get("/authz/roles/agent/permissions", headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"})
    assert res.status_code == 200
    perms = res.json()["permissions"]
    assert "sla.manage" in perms


def test_grant_role_permission_requires_roles_manage_permission(client):
    res = client.post(
        "/authz/roles/agent/permissions",
        json={"permission_key": "sla.manage"},
        headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"},
    )
    assert res.status_code == 403


def test_revoke_role_permission_endpoint(client):
    # First grant a permission
    client.post(
        "/authz/roles/agent/permissions",
        json={"permission_key": "sla.manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )

    # Now revoke it
    res = client.delete(
        "/authz/roles/agent/permissions/sla.manage",
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    # Verify the permission was actually revoked
    res = client.get("/authz/roles/agent/permissions", headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"})
    assert res.status_code == 200
    perms = res.json()["permissions"]
    assert "sla.manage" not in perms


def test_revoke_role_permission_requires_roles_manage_permission(client):
    res = client.delete(
        "/authz/roles/agent/permissions/sla.manage",
        headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"},
    )
    assert res.status_code == 403


def test_role_users_endpoint(client):
    res = client.get("/authz/roles/agent/users", headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"})
    assert res.status_code == 200
    data = res.json()
    assert "chatwoot_user_ids" in data
    assert 2 in data["chatwoot_user_ids"]


def test_role_users_requires_roles_manage_permission(client):
    res = client.get("/authz/roles/agent/users", headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"})
    assert res.status_code == 403


def test_unassign_role_endpoint(client):
    # First verify the user has the agent role
    res = client.get("/authz/roles/agent/users", headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"})
    assert 2 in res.json()["chatwoot_user_ids"]

    # Now unassign the role
    res = client.request(
        "DELETE",
        "/authz/roles/agent/assign",
        json={"chatwoot_user_id": 2},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    # Verify the role was actually removed
    res = client.get("/authz/roles/agent/users", headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"})
    assert 2 not in res.json()["chatwoot_user_ids"]


def test_unassign_role_requires_roles_manage_permission(client):
    res = client.request(
        "DELETE",
        "/authz/roles/agent/assign",
        json={"chatwoot_user_id": 2},
        headers={"x-chatwoot-access-token": "agent-tok", "x-chatwoot-client": "client-2", "x-chatwoot-uid": "uid-2"},
    )
    assert res.status_code == 403


class _FakeMirror:
    def __init__(self, fail: bool = False, fail_on_call: int | None = None):
        self.fail = fail
        # 1-indexed call number (across ensure/delete/set_agent combined) on
        # which to raise — lets tests prove that N-1 already-applied calls
        # get rolled back when call N fails, instead of only ever failing
        # everything from the first call.
        self.fail_on_call = fail_on_call
        self.call_count = 0
        self.ensure_calls = []
        self.delete_calls = []
        self.agent_calls = []
        self._next_id = 100

    def _maybe_fail(self):
        self.call_count += 1
        if self.fail or self.call_count == self.fail_on_call:
            raise ChatwootRoleMirrorError("boom")

    async def ensure_custom_role(self, chatwoot_role_id, name, description, permissions):
        self._maybe_fail()
        self.ensure_calls.append((chatwoot_role_id, name, description, list(permissions)))
        if chatwoot_role_id is not None:
            return chatwoot_role_id
        self._next_id += 1
        return self._next_id

    async def delete_custom_role(self, chatwoot_role_id):
        self._maybe_fail()
        self.delete_calls.append(chatwoot_role_id)

    async def set_agent_custom_role(self, chatwoot_user_id, chatwoot_role_id):
        self._maybe_fail()
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

    respx_mock.get(f"{settings.chatwoot_api_url}/auth/validate_token").mock(
        return_value=httpx.Response(200, json={"data": {"id": 1}})
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
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
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
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_unassigned_manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    perms = await repo.role_permissions("leader")
    assert "chatwoot.conversation_manage" not in perms
    assert "chatwoot.conversation_unassigned_manage" in perms
    assert mirror.ensure_calls[-1][3] == ["conversation_unassigned_manage"]


async def test_grant_native_key_rolls_back_db_on_mirror_failure(mirror_client):
    client, repo, mirror = mirror_client
    await repo.assign_role(chatwoot_user_id=5, role_id="leader")
    mirror.fail = True
    res = client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 502
    perms = await repo.role_permissions("leader")
    assert "chatwoot.conversation_manage" not in perms


async def test_grant_native_key_rolls_back_already_applied_user_on_partial_mirror_failure(
    mirror_client,
):
    """Two members of the same role: user 5 is processed first and succeeds
    (gets a Chatwoot custom role created + assigned), user 6 is processed
    second and fails. The whole request must still 502 — but crucially, user
    5's already-applied Chatwoot state must be rolled back too, not just the
    role-level DB grant. Regression test for the fail-open residue bug where
    only the triggering DB-level change was reverted, leaving user 5's
    Chatwoot access silently changed even though the operator saw a 502."""
    client, repo, mirror = mirror_client
    await repo.assign_role(chatwoot_user_id=5, role_id="leader")
    await repo.assign_role(chatwoot_user_id=6, role_id="leader")
    # Snapshot order is sorted ascending (5 before 6), so per user the
    # resync does: user 5 -> ensure (call 1) + set_agent (call 2);
    # user 6 -> ensure (call 3), which we make fail.
    mirror.fail_on_call = 3

    res = client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 502

    # Role-level DB grant was reverted (pre-existing behavior).
    assert "chatwoot.conversation_manage" not in await repo.role_permissions("leader")

    # User 5's already-applied Chatwoot state must have been rolled back:
    # the custom role created for them during the forward pass was deleted,
    # their mirror row was cleared, and their agent custom_role_id was unset
    # back to None (their pre-mutation state).
    assert await repo.get_native_role_mirror(5) is None
    assert await repo.get_native_role_mirror(6) is None
    assert len(mirror.ensure_calls) == 1  # only user 5's initial create succeeded
    assert mirror.ensure_calls[0][0] is None  # created fresh (no prior mirror)
    assert mirror.delete_calls == [101]  # the just-created role, deleted on rollback
    assert (5, 101) in mirror.agent_calls  # forward: assigned
    assert (5, None) in mirror.agent_calls  # rollback: unassigned back to none


async def test_grant_native_key_rolls_back_orphaned_state_when_set_agent_custom_role_fails(
    mirror_client,
):
    """Failure injected at set_agent_custom_role — AFTER ensure_custom_role
    already created a Chatwoot role and set_native_role_mirror already
    committed the DB row for THIS SAME user in this same forward-pass
    iteration (the in-flight-partial-application case: distinct from
    test_grant_native_key_rolls_back_already_applied_user_on_partial_mirror_failure
    above, which fails at a *different*, not-yet-started user).

    Regression test for the bug where the rollback loop only iterated
    `applied` — a user whose own forward pass raised mid-way was never added
    to `applied`, so their just-created Chatwoot role and just-committed
    mirror row were never cleaned up. A LATER successful sync touching the
    same user would then read that orphaned prior_mirror_id, find
    new_id == prior_mirror_id after a PATCH, and silently skip calling
    set_agent_custom_role again — the operator's intended restriction would
    never actually apply to that user."""
    client, repo, mirror = mirror_client
    await repo.assign_role(chatwoot_user_id=5, role_id="leader")
    # user 5 has no prior mirror, so the forward pass does: ensure_custom_role
    # (call 1, creates a fresh role + commits set_native_role_mirror) ->
    # set_agent_custom_role (call 2), which we make fail.
    mirror.fail_on_call = 2

    res = client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 502

    # (a) the orphaned mirror row was cleaned up, not left pointing at a
    # Chatwoot role the request as a whole reported as failed.
    assert await repo.get_native_role_mirror(5) is None

    # (b) the just-created Chatwoot role was deleted on rollback.
    assert mirror.delete_calls == [101]

    # (c) the user was never left pointing at the orphaned role: the forward
    # pass's set_agent_custom_role(5, 101) raised before being recorded, and
    # the rollback's compensating call explicitly unset it back to None.
    assert (5, 101) not in mirror.agent_calls
    assert (5, None) in mirror.agent_calls


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
    respx_mock.get(f"{settings.chatwoot_api_url}/auth/validate_token").mock(
        return_value=httpx.Response(200, json={"data": {"id": 1}})
    )
    validator = TokenValidator(settings)
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings))  # mirror defaults to None
    client = TestClient(app)
    res = client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "audit.view"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 200
    assert "audit.view" in await repo.role_permissions("leader")


async def test_revoke_last_native_key_deletes_mirror_and_clears_agent(mirror_client):
    client, repo, mirror = mirror_client
    await repo.assign_role(chatwoot_user_id=5, role_id="leader")
    client.post(
        "/authz/roles/leader/permissions",
        json={"permission_key": "chatwoot.conversation_manage"},
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    mirror.ensure_calls.clear()
    mirror.agent_calls.clear()
    res = client.delete(
        "/authz/roles/leader/permissions/chatwoot.conversation_manage",
        headers={"x-chatwoot-access-token": "admin-tok", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"},
    )
    assert res.status_code == 200
    assert await repo.get_native_role_mirror(5) is None
    assert (5, None) in mirror.agent_calls
