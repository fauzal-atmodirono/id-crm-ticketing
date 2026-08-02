"""Mirrors our RBAC role grants into Chatwoot's own dormant, already-shipped
CustomRole + account_users.custom_role_id mechanism — see
docs/superpowers/specs/2026-08-02-rbac-phase3-native-conversation-visibility-design.md.
Self-contained: owns its own httpx client and constructs dual-auth headers
from settings directly, mirroring features/routing/assigner.py's
RoutingAssigner exactly. UNLIKE RoutingAssigner, this is FAIL-CLOSED — every
method raises ChatwootRoleMirrorError on any HTTP failure instead of
swallowing it, because this governs human access control (Phase 1's
fail-closed boundary), not AI orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class ChatwootRoleMirrorError(Exception):
    pass


class ChatwootRoleMirror:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Deferred import avoids a circular dependency between the authz
        # package and the chat adapter package (matches RoutingAssigner).
        import httpx  # noqa: PLC0415

        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        url = f"{self._base()}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(
                    method, url, json=payload, headers=headers, timeout=10.0
                )
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("chatwoot_role_mirror_request_failed", method=method, path=path, error=str(e))
            raise ChatwootRoleMirrorError(f"{method} {path} failed: {e}") from e

    async def ensure_custom_role(
        self,
        chatwoot_role_id: int | None,
        name: str,
        description: str,
        permissions: list[str],
    ) -> int:
        body = {
            "custom_role": {"name": name, "description": description, "permissions": permissions}
        }
        if chatwoot_role_id is None:
            res = await self._request("POST", "/custom_roles", body)
        else:
            res = await self._request("PATCH", f"/custom_roles/{chatwoot_role_id}", body)
        return int(res["id"])

    async def delete_custom_role(self, chatwoot_role_id: int) -> None:
        await self._request("DELETE", f"/custom_roles/{chatwoot_role_id}")

    async def set_agent_custom_role(
        self, chatwoot_user_id: int, chatwoot_role_id: int | None
    ) -> None:
        await self._request(
            "PATCH", f"/agents/{chatwoot_user_id}", {"custom_role_id": chatwoot_role_id}
        )
