"""`/authz` API router — self-permission check + roles-admin CRUD.

Consumes Task 1-4's models/repository/identity/deps. Read-only endpoints
(`/permissions`, `/check`, `/roles`) require only a valid Chatwoot access
token (any authenticated user may inspect their own permissions or the role
catalog); the roles-admin write endpoints are gated behind the `roles.manage`
permission via `require_permission`, which is itself default-preserving (see
deps.py docstring).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings


class CreateRoleBody(BaseModel):
    id: str
    name: str
    description: str = ""


class AssignRoleBody(BaseModel):
    chatwoot_user_id: int


class GrantPermissionBody(BaseModel):
    permission_key: str


def build_authz_router(
    repo: AuthzRepository, validator: TokenValidator, settings: Settings
) -> APIRouter:
    router = APIRouter(prefix="/authz", tags=["authz"])

    async def _caller_user_id(x_chatwoot_access_token: str | None = Header(default=None)) -> int:
        if not x_chatwoot_access_token:
            raise HTTPException(status_code=401, detail="Missing Chatwoot access token")
        user_id = await validator.resolve_user_id(x_chatwoot_access_token)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return user_id

    @router.get("/permissions")
    async def my_permissions(user_id: int = Depends(_caller_user_id)) -> dict:
        perms = await repo.permissions_for_user(user_id)
        return {"permissions": sorted(perms)}

    @router.get("/check")
    async def check(permission: str, user_id: int = Depends(_caller_user_id)) -> dict:
        perms = await repo.permissions_for_user(user_id)
        return {"allowed": permission in perms}

    @router.get("/roles")
    async def list_roles(user_id: int = Depends(_caller_user_id)) -> dict:  # noqa: ARG001 — auth-gating dependency, value unused
        roles = await repo.list_roles()
        return {
            "roles": [{"id": r.id, "name": r.name, "description": r.description} for r in roles]
        }

    manage_roles = require_permission(
        "roles.manage", repo=repo, validator=validator, settings=settings
    )

    @router.post("/roles", dependencies=[Depends(manage_roles)])
    async def create_role(body: CreateRoleBody) -> dict:
        await repo.create_role(body.id, body.name, body.description)
        return {"ok": True}

    @router.post("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def assign_role(role_id: str, body: AssignRoleBody) -> dict:
        await repo.assign_role(body.chatwoot_user_id, role_id)
        return {"ok": True}

    @router.get("/permission-registry", dependencies=[Depends(manage_roles)])
    async def permission_registry() -> dict:
        perms = await repo.list_permissions()
        return {"permissions": [{"key": p.key, "description": p.description} for p in perms]}

    @router.get("/roles/{role_id}/permissions", dependencies=[Depends(manage_roles)])
    async def role_permissions(role_id: str) -> dict:
        perms = await repo.role_permissions(role_id)
        return {"permissions": sorted(perms)}

    @router.post("/roles/{role_id}/permissions", dependencies=[Depends(manage_roles)])
    async def grant_role_permission(role_id: str, body: GrantPermissionBody) -> dict:
        await repo.grant_permission(role_id, body.permission_key)
        return {"ok": True}

    @router.delete(
        "/roles/{role_id}/permissions/{permission_key}", dependencies=[Depends(manage_roles)]
    )
    async def revoke_role_permission(role_id: str, permission_key: str) -> dict:
        await repo.revoke_permission(role_id, permission_key)
        return {"ok": True}

    @router.get("/roles/{role_id}/users", dependencies=[Depends(manage_roles)])
    async def role_users(role_id: str) -> dict:
        user_ids = await repo.users_for_role(role_id)
        return {"chatwoot_user_ids": user_ids}

    @router.delete("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def unassign_role(role_id: str, body: AssignRoleBody) -> dict:
        await repo.unassign_role(body.chatwoot_user_id, role_id)
        return {"ok": True}

    return router
