"""require_permission — the FastAPI dependency that gates admin endpoints.

Default-preserving: when RBAC is unconfigured (settings.rbac_enabled is
False), behaves EXACTLY like today's shared-secret check
(features/routing/router.py's _require_api_key) — no behavior change for any
tenant that hasn't opted into RBAC. Once enabled, resolves the caller's
Chatwoot access token to a user id, looks up their permission set, and denies
(403) if the required permission is absent. Any resolution failure (missing
token, invalid token, network error) is a 401 deny — never a silent allow.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

from fastapi import Header, HTTPException

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings


def _shared_secret_check(settings: Settings, x_api_key: str | None) -> None:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    candidates = [settings.faq_admin_api_key, settings.proton_backend_key]
    for key in candidates:
        if key and hmac.compare_digest(x_api_key, key):
            return
    raise HTTPException(status_code=401, detail="Missing or invalid API key")


# The platform owner. Id 1 is the account that set the instance up, verified
# to be the vendor on every tenant here. It is a hardcoded floor rather than
# a stored grant precisely so that no sequence of administrative accidents --
# a revoked role, a stripped SuperAdmin type, a botched migration -- can lock
# the owner out of the switchboard.
_PLATFORM_OWNER_USER_ID = 1


def is_platform_superadmin(user_id: int, is_super_admin_type: bool) -> bool:
    """Chatwoot's own SuperAdmin type, plus the id-1 floor.

    Granting superadmin to somebody else is Chatwoot's `/super_admin` console,
    which already does it and is already how this platform's other superadmins
    were made. Deliberately NOT a second grant list of our own: it would be
    free to disagree with `users.type`, producing someone revoked in our UI
    who is still a Chatwoot superadmin.
    """
    return user_id == _PLATFORM_OWNER_USER_ID or is_super_admin_type


def require_platform_superadmin(
    *,
    validator: TokenValidator | None = None,
    settings: Settings,  # noqa: ARG001 -- accepted for call-site symmetry with require_permission; deliberately never consulted (see docstring)
):
    """Gate for the custom-feature switchboard.

    Never honours the shared-secret path, and does NOT consult
    `settings.rbac_enabled`: this is a platform-level authority that exists
    whether or not a tenant has opted into RBAC. Feature management is
    deliberately not an RBAC permission -- `seed_defaults` grants
    "administrator" every key in PERMISSION_REGISTRY, so a `features.manage`
    key would hand each customer's own admin the power to switch on surfaces
    they did not buy.
    """

    async def _check(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),  # noqa: ARG001 -- shared secret deliberately ignored
    ) -> int:
        if (
            not x_chatwoot_access_token
            or not x_chatwoot_client
            or not x_chatwoot_uid
            or validator is None
        ):
            raise HTTPException(status_code=401, detail="Chatwoot session required")

        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id, is_super_admin_type = identity
        if not is_platform_superadmin(user_id, is_super_admin_type):
            raise HTTPException(status_code=403, detail="Platform superadmin required")
        return user_id

    return _check


def require_permission(
    permission: str,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    settings: Settings,
):
    async def _check(
        x_api_key: str | None = Header(default=None),
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> None:
        if not settings.rbac_enabled:
            _shared_secret_check(settings, x_api_key)
            return

        if (
            not x_chatwoot_access_token
            or not x_chatwoot_client
            or not x_chatwoot_uid
            or repo is None
            or validator is None
        ):
            raise HTTPException(status_code=401, detail="Missing Chatwoot access token")

        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id, is_super_admin_type = identity
        # A platform superadmin holds every RBAC permission. Without this the
        # platform owner is locked out of a tenant's own Roles & Permissions
        # page on any tenant where they were never assigned a role.
        if is_platform_superadmin(user_id, is_super_admin_type):
            return

        perms = await repo.permissions_for_user(user_id)
        if permission not in perms:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")

    return _check


def require_permission_with_identity(
    permission: str,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    settings: Settings,
):
    """Like `require_permission`, but RETURNS the resolved Chatwoot user id
    and never honours the shared-secret path.

    That second difference is the point, not an oversight. `require_permission`
    falls back to `_shared_secret_check` when `rbac_enabled` is off, which is
    correct for endpoints that merely need to be *authorised* -- but a shared
    secret identifies a service, not a person, and the only caller of this
    dependency mints a credential in a specific person's name. With RBAC off
    there is no person, so there is no token to mint: 401.
    """

    async def _check(
        x_api_key: str | None = Header(default=None),  # noqa: ARG001 -- shared secret deliberately ignored
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> int:
        if (
            not settings.rbac_enabled
            or not x_chatwoot_access_token
            or not x_chatwoot_client
            or not x_chatwoot_uid
            or repo is None
            or validator is None
        ):
            raise HTTPException(status_code=401, detail="Chatwoot session required")

        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id, is_super_admin_type = identity
        # Same bypass as require_permission: a platform superadmin holds every
        # permission, so they are never locked out of a tenant surface they
        # were never assigned a role on.
        if is_platform_superadmin(user_id, is_super_admin_type):
            return user_id

        perms = await repo.permissions_for_user(user_id)
        if permission not in perms:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
        return user_id

    return _check
