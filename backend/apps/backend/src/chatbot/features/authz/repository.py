from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from chatbot.features.authz.db import (
    NATIVE_BOOLEAN_KEYS,
    NATIVE_CONVERSATION_KEYS,
    Permission,
    Role,
    RolePermission,
    UserNativeRoleMirror,
    UserRole,
)


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

    async def list_permissions(self) -> list[Permission]:
        async with self._sm() as session:
            return list((await session.execute(select(Permission))).scalars().all())

    async def role_permissions(self, role_id: str) -> set[str]:
        async with self._sm() as session:
            rows = await session.execute(
                select(RolePermission.permission_key).where(RolePermission.role_id == role_id)
            )
            return {r[0] for r in rows.all()}

    async def revoke_permission(self, role_id: str, permission_key: str) -> None:
        async with self._sm() as session:
            existing = await session.get(RolePermission, (role_id, permission_key))
            if existing is None:
                return
            await session.delete(existing)
            await session.commit()

    async def users_for_role(self, role_id: str) -> list[int]:
        async with self._sm() as session:
            rows = await session.execute(
                select(UserRole.chatwoot_user_id).where(UserRole.role_id == role_id)
            )
            return [r[0] for r in rows.all()]

    async def unassign_role(self, chatwoot_user_id: int, role_id: str) -> None:
        async with self._sm() as session:
            existing = await session.get(UserRole, (chatwoot_user_id, role_id))
            if existing is None:
                return
            await session.delete(existing)
            await session.commit()

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
