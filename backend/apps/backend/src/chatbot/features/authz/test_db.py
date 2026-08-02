import pytest
from sqlalchemy import select

from chatbot.features.authz.db import (
    Permission,
    Role,
    RolePermission,
    UserNativeRoleMirror,
    UserRole,
    build_engine,
    build_session_maker,
    init_authz_db,
)


@pytest.fixture
async def session_maker(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/authz_test.db")
    await init_authz_db(engine)
    return build_session_maker(engine)


@pytest.mark.asyncio
async def test_tables_created_and_roundtrip(session_maker):
    async with session_maker() as session:
        role = Role(id="administrator", name="Administrator", description="Full access")
        session.add(role)
        perm = Permission(key="sla.manage", description="Manage SLA policies")
        session.add(perm)
        await session.flush()
        session.add(RolePermission(role_id=role.id, permission_key=perm.key))
        session.add(UserRole(chatwoot_user_id=7, role_id=role.id))
        await session.commit()

    async with session_maker() as session:
        roles = (await session.execute(select(Role))).scalars().all()
        assert [r.id for r in roles] == ["administrator"]
        user_roles = (await session.execute(select(UserRole))).scalars().all()
        assert user_roles[0].chatwoot_user_id == 7
        assert user_roles[0].role_id == "administrator"


@pytest.mark.asyncio
async def test_user_native_role_mirror_table_created(session_maker):
    async with session_maker() as session:
        session.add(UserNativeRoleMirror(chatwoot_user_id=42, chatwoot_custom_role_id=7))
        await session.commit()
        row = await session.get(UserNativeRoleMirror, 42)
        assert row.chatwoot_custom_role_id == 7
        assert row.updated_at is not None
