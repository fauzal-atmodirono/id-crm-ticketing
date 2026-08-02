"""Resolve a Chatwoot browser session (the devise_token_auth access-token /
client / uid triplet issued at sign-in — NOT Chatwoot's separate static
per-user "Access Token" API credential, which the SPA has no way to obtain
automatically) to a Chatwoot user id, via GET /api/v1/profile, with a
short-TTL in-memory cache to bound the round-trip on every admin request.

/api/v1/profile (not /auth/validate_token) is deliberate: Api::BaseController
only requires the bot-style `api_access_token` header when present, otherwise
falls through to normal Devise `authenticate_user!`, which accepts these same
session headers — and its response body reliably includes the user `id`.
/auth/validate_token's JSON body does NOT (Chatwoot's own
`devise/token.json.jbuilder` overwrites the user-data key with just
`{created_at}`, discarding `id`), which was confirmed live via a KeyError.

A validation failure (bad/expired session, network error) returns None —
callers must treat that as "deny", never "allow"."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class TokenValidator:
    def __init__(self, settings: Settings, cache_ttl_seconds: float = 60.0) -> None:
        self._settings = settings
        self._ttl = cache_ttl_seconds
        # (access_token, client, uid) -> (user_id, expires_at)
        self._cache: dict[tuple[str, str, str], tuple[int, float]] = {}

    async def resolve_user_id(self, access_token: str, client: str, uid: str) -> int | None:
        cache_key = (access_token, client, uid)
        cached = self._cache.get(cache_key)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        url = f"{self._settings.chatwoot_api_url.rstrip('/')}/api/v1/profile"
        try:
            async with httpx.AsyncClient() as http_client:
                res = await http_client.get(
                    url,
                    headers={"access-token": access_token, "client": client, "uid": uid},
                    timeout=5.0,
                )
                res.raise_for_status()
                user_id = res.json()["id"]
        except Exception as exc:
            _log.warning("authz_token_validation_failed", error=str(exc))
            return None

        self._cache[cache_key] = (user_id, time.monotonic() + self._ttl)
        return user_id
