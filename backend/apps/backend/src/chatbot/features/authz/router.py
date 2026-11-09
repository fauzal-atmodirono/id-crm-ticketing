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
from chatbot.features.authz.data_scope import (
    UNSCOPABLE_ROLE_IDS,
    Scope,
    build_scope_dependency,
)
from chatbot.features.authz.deps import (
    Actor,
    is_platform_superadmin,
    require_permission,
    resolve_actor,
)
from chatbot.features.authz.seed import (
    ALL_NATIVE_KEYS,
    NATIVE_CONVERSATION_KEYS,
    PERMISSION_REGISTRY,
)
from chatbot.features.chat.audit_events import EVENT_ROLE_CHANGE, record_event

if TYPE_CHECKING:
    from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.ports import AuditLogPort
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


class RoleScopeBody(BaseModel):
    """A role's whole data scope (PUT replaces it). Omitted/empty dimension =
    not restricted on it; all empty = the role restricts nothing."""

    inbox_ids: list[int] = []
    team_ids: list[int] = []
    dealer_slugs: list[str] = []
    departments: list[str] = []


def build_authz_router(
    repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
    mirror: ChatwootRoleMirror | None = None,
    audit: AuditLogPort | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/authz", tags=["authz"])

    async def _caller_user_id(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> int:
        if not x_chatwoot_access_token or not x_chatwoot_client or not x_chatwoot_uid:
            raise HTTPException(status_code=401, detail="Missing Chatwoot access token")
        user_id = await validator.resolve_user_id(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return user_id

    async def _effective_permissions(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> set[str]:
        """What the caller may do, as `require_permission` decides it.

        `require_permission` lets a platform superadmin (`deps.
        is_platform_superadmin`: Chatwoot's SuperAdmin type or the id-1 floor)
        through every permission check when RBAC is on, whatever roles they
        hold. The UI gates its surfaces on THIS answer, so it must say the
        same, or the platform owner is shown "no access" for things the
        backend would serve (review R55). Their set is the whole RBAC
        catalogue (`PERMISSION_REGISTRY`); the native `chatwoot.*` mirror keys
        are role-mirror plumbing that `require_permission` never checks, so
        they are not listed. With RBAC off nothing changes: role keys only.
        """
        if not x_chatwoot_access_token or not x_chatwoot_client or not x_chatwoot_uid:
            raise HTTPException(status_code=401, detail="Missing Chatwoot access token")
        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id, is_super_admin_type = identity
        if settings.rbac_enabled and is_platform_superadmin(user_id, is_super_admin_type):
            return set(PERMISSION_REGISTRY)
        return set(await repo.permissions_for_user(user_id))

    @router.get("/permissions")
    async def my_permissions(perms: set[str] = Depends(_effective_permissions)) -> dict:
        return {"permissions": sorted(perms)}

    @router.get("/check")
    async def check(permission: str, perms: set[str] = Depends(_effective_permissions)) -> dict:
        return {"allowed": permission in perms}

    @router.get("/roles")
    async def list_roles(user_id: int = Depends(_caller_user_id)) -> dict:  # noqa: ARG001 — auth-gating dependency, value unused
        roles = await repo.list_roles()
        return {
            "roles": [{"id": r.id, "name": r.name, "description": r.description} for r in roles]
        }

    # The actor-returning variant: same gate, but the write endpoints below
    # also learn WHO is calling so the audit trail names a real user.
    manage_roles = require_permission(
        "roles.manage", repo=repo, validator=validator, settings=settings
    )
    caller = resolve_actor(validator=validator, settings=settings)

    async def _audit_role(
        actor: Actor, role_id: str, before: dict | None, after: dict | None, remarks: str
    ) -> None:
        await record_event(
            audit,
            event_type=EVENT_ROLE_CHANGE,
            target=f"role:{role_id}",
            actor_user_id=actor.user_id,
            actor_name=actor.name,
            before=before,
            after=after,
            remarks=remarks,
        )

    # The extra reads exist only to feed the audit row. A failure here must
    # never turn an already-applied write into a 500 -> None (no snapshot).
    async def _members(role_id: str) -> dict | None:
        try:
            return {"members": sorted(await repo.users_for_role(role_id))}
        except Exception:
            _log.warning("audit_role_snapshot_failed", role_id=role_id)
            return None

    async def _perms(role_id: str) -> dict | None:
        try:
            return {"permissions": sorted(await repo.role_permissions(role_id))}
        except Exception:
            _log.warning("audit_role_snapshot_failed", role_id=role_id)
            return None

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
    async def create_role(body: CreateRoleBody, actor: Actor = Depends(caller)) -> dict:
        await repo.create_role(body.id, body.name, body.description)
        await _audit_role(
            actor, body.id, {}, {"name": body.name, "description": body.description}, "role created"
        )
        return {"ok": True}

    @router.post("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def assign_role(
        role_id: str, body: AssignRoleBody, actor: Actor = Depends(caller)
    ) -> dict:
        before = await _members(role_id)
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
        await _audit_role(
            actor, role_id, before, await _members(role_id), f"user {body.chatwoot_user_id} assigned"
        )
        return {"ok": True}

    @router.get("/permission-registry", dependencies=[Depends(manage_roles)])
    async def permission_registry() -> dict:
        perms = await repo.list_permissions()
        return {"permissions": [{"key": p.key, "description": p.description} for p in perms]}

    # --- data scopes (Wave 2 Task 28; rules in data_scope.py) -------------
    # Edited and readable whether or not DATA_SCOPED_RBAC_ENABLED is on, so an
    # operator can configure scopes before switching enforcement on;
    # `enabled` tells the page which state it is in.
    my_scope = build_scope_dependency(settings, repo=repo, validator=validator)

    def _scope_payload(scope: Scope) -> dict:
        return {
            "scope": scope.to_dict(),
            "enabled": bool(getattr(settings, "data_scoped_rbac_enabled", False)),
        }

    @router.get("/me/scope")
    async def get_my_scope(
        user_id: int = Depends(_caller_user_id),  # noqa: ARG001 -- auth-gating dependency
        scope: Scope = Depends(my_scope),
    ) -> dict:
        return _scope_payload(scope)

    @router.get("/roles/{role_id}/scope", dependencies=[Depends(manage_roles)])
    async def get_role_scope(role_id: str) -> dict:
        payload = _scope_payload(await repo.get_role_scope(role_id))
        payload["scopable"] = role_id not in UNSCOPABLE_ROLE_IDS
        return payload

    @router.put("/roles/{role_id}/scope", dependencies=[Depends(manage_roles)])
    async def put_role_scope(
        role_id: str, body: RoleScopeBody, actor: Actor = Depends(caller)
    ) -> dict:
        if role_id in UNSCOPABLE_ROLE_IDS:
            raise HTTPException(
                status_code=400,
                detail=f"The {role_id} role is always unrestricted and cannot carry a data scope.",
            )
        if not await repo.role_exists(role_id):
            raise HTTPException(status_code=404, detail=f"Unknown role: {role_id}")
        before = (await repo.get_role_scope(role_id)).to_dict()
        scope = Scope.build(**body.model_dump())
        await repo.set_role_scope(role_id, scope)
        # The write is applied; a failed re-read must not turn it into a 500.
        # The normalised scope we wrote is what is now stored, so answer with
        # that and audit against it.
        try:
            stored = await repo.get_role_scope(role_id)
        except Exception:
            _log.warning("role_scope_reread_failed", role_id=role_id)
            stored = scope
        after = stored.to_dict()
        if after != before:
            await _audit_role(
                actor, role_id, {"scope": before}, {"scope": after}, "data scope set"
            )
        return _scope_payload(stored)

    @router.get("/roles/{role_id}/permissions", dependencies=[Depends(manage_roles)])
    async def role_permissions(role_id: str) -> dict:
        perms = await repo.role_permissions(role_id)
        return {"permissions": sorted(perms)}

    @router.post("/roles/{role_id}/permissions", dependencies=[Depends(manage_roles)])
    async def grant_role_permission(
        role_id: str, body: GrantPermissionBody, actor: Actor = Depends(caller)
    ) -> dict:
        key = body.permission_key
        before = await _perms(role_id)
        if key not in ALL_NATIVE_KEYS or mirror is None:
            await repo.grant_permission(role_id, key)
            await _audit_role(actor, role_id, before, await _perms(role_id), f"granted {key}")
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
        await _audit_role(actor, role_id, before, await _perms(role_id), f"granted {key}")
        return {"ok": True}

    @router.delete(
        "/roles/{role_id}/permissions/{permission_key}", dependencies=[Depends(manage_roles)]
    )
    async def revoke_role_permission(
        role_id: str, permission_key: str, actor: Actor = Depends(caller)
    ) -> dict:
        before = await _perms(role_id)
        was_native = permission_key in ALL_NATIVE_KEYS and mirror is not None
        pre_snapshot = (
            await _snapshot_users(set(await repo.users_for_role(role_id))) if was_native else {}
        )
        await repo.revoke_permission(role_id, permission_key)
        if not was_native:
            await _audit_role(
                actor, role_id, before, await _perms(role_id), f"revoked {permission_key}"
            )
            return {"ok": True}
        try:
            await _resync_role_mirror(pre_snapshot)
        except ChatwootRoleMirrorError as exc:
            await repo.grant_permission(role_id, permission_key)
            raise HTTPException(status_code=502, detail=f"Chatwoot sync failed: {exc}") from exc
        await _audit_role(
            actor, role_id, before, await _perms(role_id), f"revoked {permission_key}"
        )
        return {"ok": True}

    @router.get("/roles/{role_id}/users", dependencies=[Depends(manage_roles)])
    async def role_users(role_id: str) -> dict:
        user_ids = await repo.users_for_role(role_id)
        return {"chatwoot_user_ids": user_ids}

    @router.delete("/roles/{role_id}/assign", dependencies=[Depends(manage_roles)])
    async def unassign_role(
        role_id: str, body: AssignRoleBody, actor: Actor = Depends(caller)
    ) -> dict:
        before = await _members(role_id)
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
        await _audit_role(
            actor, role_id, before, await _members(role_id), f"user {body.chatwoot_user_id} unassigned"
        )
        return {"ok": True}

    return router
