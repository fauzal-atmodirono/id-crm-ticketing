"""Client for the proton-conversational-ai backend's configuration endpoints.

Provides per-inbox agent mode and tenant-level debounce settings fetched from:
  GET {base_url}/kb/inboxes   → list of inbox configs (mode per inbox)
  GET {base_url}/kb/settings  → tenant-level settings (debounce_seconds etc.)

Both are cached in-process with a configurable TTL (default 60 s) to avoid
hammering the backend on every bot event. The cache key is the URL itself; the
value is (data, monotonic_fetch_time). Staleness is checked on each access.

All public methods return None on any failure (network error, non-2xx, missing
key, bad shape) — never raise. This keeps the orchestrator's fail-open pattern:
if the proton backend is unreachable, the agent falls back to global settings.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ProtonConfigClient:
    """Thin cached client for the proton-conversational-ai config API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        ttl: float = 60.0,
    ) -> None:
        self._ttl = ttl
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"x-api-key": api_key},
            timeout=10.0,
        )
        # Cache entries: path → (data, fetch_monotonic_time)
        self._cache: dict[str, tuple[Any, float]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_cached(self, path: str) -> Any | None:
        """Return cached JSON for *path*, fetching from backend when stale."""
        entry = self._cache.get(path)
        if entry is not None:
            data, fetched_at = entry
            if time.monotonic() - fetched_at < self._ttl:
                return data

        try:
            response = await self._client.get(path)
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.debug("proton_config: failed to fetch %s", path, exc_info=True)
            return None

        self._cache[path] = (data, time.monotonic())
        return data

    async def effective_inbox_mode(self, inbox_id: int) -> str | None:
        """Return the mode string for *inbox_id* from /kb/inboxes, or None.

        None means the backend is unconfigured, unreachable, or has no row for
        this inbox — caller should fall back to the global agent_mode setting.
        """
        try:
            data = await self._fetch_cached("/kb/inboxes")
            if not isinstance(data, dict):
                return None
            inboxes = data.get("inboxes")
            if not isinstance(inboxes, list):
                return None
            for row in inboxes:
                if isinstance(row, dict) and row.get("inbox_id") == inbox_id:
                    mode = row.get("mode")
                    return str(mode) if mode is not None else None
            return None
        except Exception:
            logger.debug(
                "proton_config: error resolving inbox mode for inbox %s", inbox_id, exc_info=True
            )
            return None

    async def effective_debounce_seconds(self) -> float | None:
        """Return the tenant debounce_seconds from /kb/settings, or None.

        None means the backend is unreachable or the value is missing/invalid
        — caller should fall back to the module-level DEBOUNCE_SECONDS constant.
        """
        try:
            data = await self._fetch_cached("/kb/settings")
            if not isinstance(data, dict):
                return None
            settings = data.get("settings")
            if not isinstance(settings, dict):
                return None
            debounce = settings.get("debounce_seconds")
            if not isinstance(debounce, dict):
                return None
            value = debounce.get("value")
            if value is None:
                return None
            return float(value)
        except Exception:
            logger.debug("proton_config: error resolving debounce_seconds", exc_info=True)
            return None
