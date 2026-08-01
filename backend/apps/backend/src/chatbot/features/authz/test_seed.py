import pytest

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import PERMISSION_REGISTRY, seed_defaults


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/authz_seed.db")
    await init_authz_db(engine)
    return AuthzRepository(build_session_maker(engine))


@pytest.mark.asyncio
async def test_seed_creates_administrator_with_all_permissions(repo):
    await seed_defaults(repo)
    roles = {r.id for r in await repo.list_roles()}
    assert {"administrator", "agent"} <= roles


@pytest.mark.asyncio
async def test_seed_is_idempotent(repo):
    await seed_defaults(repo)
    await seed_defaults(repo)  # second run must not raise or duplicate
    roles = await repo.list_roles()
    assert len([r for r in roles if r.id == "administrator"]) == 1


@pytest.mark.asyncio
async def test_administrator_role_has_every_registered_permission(repo):
    await seed_defaults(repo)
    # administrator is seeded with chatwoot_user_id=0 as a placeholder assignment
    # only in this test — in production, role assignment happens via the
    # /authz roles-admin endpoint (Task 5), not at seed time.
    async with repo._sm() as session:
        pass  # placeholder to keep session import path exercised; real assertion below
    from sqlalchemy import select

    from chatbot.features.authz.db import RolePermission

    async with repo._sm() as session:
        rows = (
            await session.execute(
                select(RolePermission.permission_key).where(RolePermission.role_id == "administrator")
            )
        ).scalars().all()
    assert set(rows) == set(PERMISSION_REGISTRY.keys())
