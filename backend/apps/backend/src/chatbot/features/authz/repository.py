from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from chatbot.features.authz.db import Permission, Role, RolePermission, UserRole


class AuthzRepository:
    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._sm = session_maker

    async def create_role(self, role_id: str, name: str, description: str = "") -> None:
        async with self._sm() as session:
            existing = await session.get(Role, role_id)
            if existing is not None:
                return
            session.add(Role(id=role_id, name=name, description=description))
            await session.commit()

    async def create_permission(self, key: str, description: str = "") -> None:
        async with self._sm() as session:
            existing = await session.get(Permission, key)
            if existing is not None:
                return
            session.add(Permission(key=key, description=description))
            await session.commit()

    async def grant_permission(self, role_id: str, permission_key: str) -> None:
        async with self._sm() as session:
            await self.create_permission_if_absent(session, permission_key)
            existing = await session.get(RolePermission, (role_id, permission_key))
            if existing is not None:
                return
            session.add(RolePermission(role_id=role_id, permission_key=permission_key))
            await session.commit()

    async def create_permission_if_absent(self, session, key: str) -> None:
        existing = await session.get(Permission, key)
        if existing is None:
            session.add(Permission(key=key, description=""))
            await session.flush()

    async def assign_role(self, chatwoot_user_id: int, role_id: str) -> None:
        async with self._sm() as session:
            existing = await session.get(UserRole, (chatwoot_user_id, role_id))
            if existing is not None:
                return
            session.add(UserRole(chatwoot_user_id=chatwoot_user_id, role_id=role_id))
            await session.commit()

    async def permissions_for_user(self, chatwoot_user_id: int) -> set[str]:
        async with self._sm() as session:
            rows = await session.execute(
                select(RolePermission.permission_key)
                .join(UserRole, UserRole.role_id == RolePermission.role_id)
                .where(UserRole.chatwoot_user_id == chatwoot_user_id)
            )
            return {r[0] for r in rows.all()}

    async def list_roles(self) -> list[Role]:
        async with self._sm() as session:
            return list((await session.execute(select(Role))).scalars().all())
