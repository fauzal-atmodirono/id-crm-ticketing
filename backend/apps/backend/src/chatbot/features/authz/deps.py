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


def require_permission(
    permission: str,
    *,
    repo: AuthzRepository | None,
    validator: TokenValidator | None,
    settings: Settings,
):
    async def _check(
        x_api_key: str | None = Header(default=None),
        x_chatwoot_access_token: str | None = Header(default=None),
    ) -> None:
        if not settings.rbac_enabled:
            _shared_secret_check(settings, x_api_key)
            return

        if not x_chatwoot_access_token or repo is None or validator is None:
            raise HTTPException(status_code=401, detail="Missing Chatwoot access token")

        user_id = await validator.resolve_user_id(x_chatwoot_access_token)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        perms = await repo.permissions_for_user(user_id)
        if permission not in perms:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")

    return _check
