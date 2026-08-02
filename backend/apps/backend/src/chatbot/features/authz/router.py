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

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirrorError
from chatbot.features.authz.deps import require_permission
from chatbot.features.authz.seed import ALL_NATIVE_KEYS, NATIVE_CONVERSATION_KEYS

if TYPE_CHECKING:
    from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class CreateRoleBody(BaseModel):
    id: str
    name: str
    description: str = ""


class AssignRoleBody(BaseModel):
    chatwoot_user_id: int


class GrantPermissionBody(BaseModel):
    permission_key: str


def build_authz_router(
    repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
    mirror: ChatwootRoleMirror | None = None,
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

    async def _snapshot_users(
        user_ids: set[int],
    ) -> dict[int, tuple[int | None, list[str]]]:
        """Capture every user's PRE-mutation (mirror_id, resolved native
        permissions) so `_resync_role_mirror` can roll back an already-applied
        user's Chatwoot state if a later user in the same call fails."""
        snapshot: dict[int, tuple[int | None, list[str]]] = {}
        for user_id in sorted(user_ids):
            snapshot[user_id] = (
                await repo.get_native_role_mirror(user_id),
                await repo.resolve_native_permissions(user_id),
            )
        return snapshot

    async def _resync_role_mirror(  # noqa: PLR0912 — forward pass + per-user compensating rollback, both branchy by nature
        pre_snapshot: dict[int, tuple[int | None, list[str]]],
    ) -> None:
        """Push every snapshotted user's POST-mutation resolved native set to
        Chatwoot. On any ChatwootRoleMirrorError mid-loop, roll back EVERY
        user in pre_snapshot to their pre-mutation state (not just the users
        whose forward-pass iteration fully completed, and not just the
        caller's own DB mutation) before re-raising.

        Rolling back the whole snapshot — not just an `applied` subset — is
        required because a single user's forward pass is not atomic: for a
        fresh user it does ensure_custom_role (creates a Chatwoot role) →
        set_native_role_mirror (commits a DB row) → set_agent_custom_role. If
        set_agent_custom_role raises for the in-flight user, that user would
        never be marked "applied", leaving their just-created Chatwoot role
        and just-committed mirror row orphaned — and a LATER successful sync
        touching the same user would read that orphaned prior_mirror_id, find
        new_id == prior_mirror_id after a PATCH, and skip calling
        set_agent_custom_role again, silently never applying the intended
        restriction. Rolling back every snapshotted user closes that window:
        untouched users are a harmless no-op (their live state already
        matches the snapshot), and the in-flight user is correctly detected
        via repo.get_native_role_mirror and cleaned up. Callers must
        compensating-revert their own DB change and re-raise as a 502."""
        if mirror is None:
            return
        try:
            for user_id, (prior_mirror_id, _prior_resolved) in pre_snapshot.items():
                resolved = await repo.resolve_native_permissions(user_id)
                stripped = [p.removeprefix("chatwoot.") for p in resolved]
                if not stripped:
                    if prior_mirror_id is not None:
                        await mirror.delete_custom_role(prior_mirror_id)
                        await repo.delete_native_role_mirror(user_id)
                        await mirror.set_agent_custom_role(user_id, None)
                else:
                    new_id = await mirror.ensure_custom_role(
                        prior_mirror_id,
                        f"RBAC user {user_id}",
                        "Mirrored by RBAC Phase 3",
                        stripped,
                    )
                    if new_id != prior_mirror_id:
                        await repo.set_native_role_mirror(user_id, new_id)
                        await mirror.set_agent_custom_role(user_id, new_id)
        except ChatwootRoleMirrorError:
            for user_id in pre_snapshot:
                prior_mirror_id, prior_resolved = pre_snapshot[user_id]
                prior_stripped = [p.removeprefix("chatwoot.") for p in prior_resolved]
                try:
                    if not prior_stripped:
                        current_mirror_id = await repo.get_native_role_mirror(user_id)
                        if current_mirror_id is not None:
                            await mirror.delete_custom_role(current_mirror_id)
                            await repo.delete_native_role_mirror(user_id)
                            await mirror.set_agent_custom_role(user_id, None)
                    else:
                        restored_id = await mirror.ensure_custom_role(
                            prior_mirror_id,
                            f"RBAC user {user_id}",
                            "Mirrored by RBAC Phase 3",
                            prior_stripped,
                        )
                        if restored_id != prior_mirror_id:
                            await repo.set_native_role_mirror(user_id, restored_id)
                        await mirror.set_agent_custom_role(user_id, prior_mirror_id)
                except ChatwootRoleMirrorError:
                    # Best-effort: rollback of an already-applied user can
                    # itself fail. Log and continue rolling back the rest
                    # rather than abandoning the loop — partial rollback is
                    # still strictly better than no rollback. The original
                    # failure re-raises after this loop regardless.
                    _log.error("chatwoot_role_mirror_rollback_failed", user_id=user_id)
            raise

    @router.post("/roles", dependencies=[Depends(manage_roles)])
    async def create_role(body: CreateRoleBody) -> dict:
        await repo.create_role(body.id, body.name, body.description)
        return {"ok": True}

    @router.post("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def assign_role(role_id: str, body: AssignRoleBody) -> dict:
        pre_snapshot: dict[int, tuple[int | None, list[str]]] = {}
        if mirror is not None:
            affected_user_ids = set(await repo.users_for_role(role_id))
            # Not yet a member pre-mutation — add explicitly so their
            # pre-grant state is captured too.
            affected_user_ids.add(body.chatwoot_user_id)
            pre_snapshot = await _snapshot_users(affected_user_ids)
        await repo.assign_role(body.chatwoot_user_id, role_id)
        try:
            await _resync_role_mirror(pre_snapshot)
        except ChatwootRoleMirrorError as exc:
            await repo.unassign_role(body.chatwoot_user_id, role_id)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
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
        key = body.permission_key
        if key not in ALL_NATIVE_KEYS or mirror is None:
            await repo.grant_permission(role_id, key)
            return {"ok": True}
        pre_snapshot = await _snapshot_users(set(await repo.users_for_role(role_id)))
        if key in NATIVE_CONVERSATION_KEYS:
            previous = await repo.role_permissions(role_id) & NATIVE_CONVERSATION_KEYS
            await repo.grant_conversation_permission_exclusive(role_id, key)
        else:
            previous = set()
            await repo.grant_permission(role_id, key)
        try:
            await _resync_role_mirror(pre_snapshot)
        except ChatwootRoleMirrorError as exc:
            await repo.revoke_permission(role_id, key)
            for old_key in previous:
                await repo.grant_permission(role_id, old_key)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        return {"ok": True}

    @router.delete(
        "/roles/{role_id}/permissions/{permission_key}", dependencies=[Depends(manage_roles)]
    )
    async def revoke_role_permission(role_id: str, permission_key: str) -> dict:
        was_native = permission_key in ALL_NATIVE_KEYS and mirror is not None
        pre_snapshot = (
            await _snapshot_users(set(await repo.users_for_role(role_id))) if was_native else {}
        )
        await repo.revoke_permission(role_id, permission_key)
        if not was_native:
            return {"ok": True}
        try:
            await _resync_role_mirror(pre_snapshot)
        except ChatwootRoleMirrorError as exc:
            await repo.grant_permission(role_id, permission_key)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        return {"ok": True}

    @router.get("/roles/{role_id}/users", dependencies=[Depends(manage_roles)])
    async def role_users(role_id: str) -> dict:
        user_ids = await repo.users_for_role(role_id)
        return {"chatwoot_user_ids": user_ids}

    @router.delete("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def unassign_role(role_id: str, body: AssignRoleBody) -> dict:
        pre_snapshot = (
            await _snapshot_users(set(await repo.users_for_role(role_id)))
            if mirror is not None
            else {}
        )
        await repo.unassign_role(body.chatwoot_user_id, role_id)
        try:
            await _resync_role_mirror(pre_snapshot)
        except ChatwootRoleMirrorError as exc:
            await repo.assign_role(body.chatwoot_user_id, role_id)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        return {"ok": True}

    return router
