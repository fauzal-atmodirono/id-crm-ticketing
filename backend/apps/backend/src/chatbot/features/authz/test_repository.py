import pytest

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/authz_repo.db")
    await init_authz_db(engine)
    return AuthzRepository(build_session_maker(engine))


@pytest.fixture
async def repo_seeded(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/authz_repo_seeded.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    return repo


@pytest.mark.asyncio
async def test_create_role_assign_and_lookup_permissions(repo):
    await repo.create_role("agent", "Agent", "Minimal access")
    await repo.grant_permission("agent", "kb.view")
    await repo.assign_role(chatwoot_user_id=42, role_id="agent")

    perms = await repo.permissions_for_user(42)
    assert perms == {"kb.view"}


@pytest.mark.asyncio
async def test_user_with_no_role_has_no_permissions(repo):
    assert await repo.permissions_for_user(999) == set()


@pytest.mark.asyncio
async def test_user_with_multiple_roles_gets_union_of_permissions(repo):
    await repo.create_role("a", "A", "")
    await repo.create_role("b", "B", "")
    await repo.grant_permission("a", "x.view")
    await repo.grant_permission("b", "y.view")
    await repo.assign_role(chatwoot_user_id=1, role_id="a")
    await repo.assign_role(chatwoot_user_id=1, role_id="b")
    assert await repo.permissions_for_user(1) == {"x.view", "y.view"}


@pytest.mark.asyncio
async def test_list_permissions_includes_seeded_registry(repo_seeded):
    perms = await repo_seeded.list_permissions()
    keys = {p.key for p in perms}
    assert "sla.manage" in keys
    assert "audit.view" in keys


@pytest.mark.asyncio
async def test_role_permissions_returns_granted_set(repo_seeded):
    perms = await repo_seeded.role_permissions("administrator")
    assert "roles.manage" in perms


@pytest.mark.asyncio
async def test_revoke_permission_removes_grant(repo_seeded):
    await repo_seeded.revoke_permission("administrator", "audit.view")
    perms = await repo_seeded.role_permissions("administrator")
    assert "audit.view" not in perms


@pytest.mark.asyncio
async def test_revoke_permission_absent_grant_is_noop(repo_seeded):
    await repo_seeded.revoke_permission("agent", "roles.manage")  # never granted
    # no exception


@pytest.mark.asyncio
async def test_users_for_role_empty_by_default(repo_seeded):
    assert await repo_seeded.users_for_role("administrator") == []


@pytest.mark.asyncio
async def test_assign_then_users_for_role(repo_seeded):
    await repo_seeded.assign_role(101, "administrator")
    assert await repo_seeded.users_for_role("administrator") == [101]


@pytest.mark.asyncio
async def test_unassign_role_removes_assignment(repo_seeded):
    await repo_seeded.assign_role(101, "administrator")
    await repo_seeded.unassign_role(101, "administrator")
    assert await repo_seeded.users_for_role("administrator") == []


@pytest.mark.asyncio
async def test_unassign_role_absent_assignment_is_noop(repo_seeded):
    await repo_seeded.unassign_role(999, "administrator")  # never assigned
    # no exception
