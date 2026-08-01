import pytest

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.repository import AuthzRepository


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/authz_repo.db")
    await init_authz_db(engine)
    return AuthzRepository(build_session_maker(engine))


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
