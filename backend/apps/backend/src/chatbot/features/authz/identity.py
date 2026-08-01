"""Resolve a Chatwoot access token to a Chatwoot user id, via GET
/api/v1/profile, with a short-TTL in-memory cache to bound the round-trip on
every admin request. A validation failure (bad token, network error) returns
None — callers must treat that as "deny", never "allow"."""

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
        self._cache: dict[str, tuple[int, float]] = {}  # token -> (user_id, expires_at)

    async def resolve_user_id(self, access_token: str) -> int | None:
        cached = self._cache.get(access_token)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        url = f"{self._settings.chatwoot_api_url.rstrip('/')}/api/v1/profile"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    url,
                    headers={"api_access_token": access_token, "Api-Access-Token": access_token},
                    timeout=5.0,
                )
                res.raise_for_status()
                user_id = res.json()["id"]
        except Exception as exc:
            _log.warning("authz_token_validation_failed", error=str(exc))
            return None

        self._cache[access_token] = (user_id, time.monotonic() + self._ttl)
        return user_id
