import pytest

from chatbot.features.authz.db import (
    NATIVE_CONVERSATION_KEYS,
    build_engine,
    build_session_maker,
    init_authz_db,
)
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


@pytest.mark.asyncio
async def test_resolve_native_permissions_empty_when_no_roles_granted(repo_seeded):
    assert await repo_seeded.resolve_native_permissions(101) == []


@pytest.mark.asyncio
async def test_resolve_native_permissions_single_role(repo_seeded):
    await repo_seeded.create_role("leader", "Leader")
    await repo_seeded.grant_permission("leader", "chatwoot.conversation_unassigned_manage")
    await repo_seeded.assign_role(101, "leader")
    assert await repo_seeded.resolve_native_permissions(101) == [
        "chatwoot.conversation_unassigned_manage"
    ]


@pytest.mark.asyncio
async def test_resolve_native_permissions_most_permissive_wins_across_roles(repo_seeded):
    await repo_seeded.create_role("role_a", "A")
    await repo_seeded.grant_permission("role_a", "chatwoot.conversation_participating_manage")
    await repo_seeded.create_role("role_b", "B")
    await repo_seeded.grant_permission("role_b", "chatwoot.conversation_manage")
    await repo_seeded.assign_role(101, "role_a")
    await repo_seeded.assign_role(101, "role_b")
    result = await repo_seeded.resolve_native_permissions(101)
    assert result == ["chatwoot.conversation_manage"]


@pytest.mark.asyncio
async def test_resolve_native_permissions_combines_conversation_and_boolean_keys(repo_seeded):
    await repo_seeded.create_role("role_a", "A")
    await repo_seeded.grant_permission("role_a", "chatwoot.conversation_unassigned_manage")
    await repo_seeded.create_role("role_b", "B")
    await repo_seeded.grant_permission("role_b", "chatwoot.contact_manage")
    await repo_seeded.assign_role(101, "role_a")
    await repo_seeded.assign_role(101, "role_b")
    result = await repo_seeded.resolve_native_permissions(101)
    assert set(result) == {"chatwoot.conversation_unassigned_manage", "chatwoot.contact_manage"}


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_grant_conversation_permission_exclusive_leaves_boolean_keys_untouched(repo_seeded):
    await repo_seeded.create_role("leader", "Leader")
    await repo_seeded.grant_permission("leader", "chatwoot.contact_manage")
    await repo_seeded.grant_conversation_permission_exclusive(
        "leader", "chatwoot.conversation_manage"
    )
    perms = await repo_seeded.role_permissions("leader")
    assert "chatwoot.contact_manage" in perms
    assert "chatwoot.conversation_manage" in perms


@pytest.mark.asyncio
async def test_native_role_mirror_roundtrip(repo_seeded):
    assert await repo_seeded.get_native_role_mirror(101) is None
    await repo_seeded.set_native_role_mirror(101, 55)
    assert await repo_seeded.get_native_role_mirror(101) == 55
    await repo_seeded.set_native_role_mirror(101, 56)  # overwrite
    assert await repo_seeded.get_native_role_mirror(101) == 56
    await repo_seeded.delete_native_role_mirror(101)
    assert await repo_seeded.get_native_role_mirror(101) is None


@pytest.mark.asyncio
async def test_delete_native_role_mirror_absent_is_noop(repo_seeded):
    await repo_seeded.delete_native_role_mirror(999)  # never set — no exception
