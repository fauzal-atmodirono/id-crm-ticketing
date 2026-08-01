"""Fetch inbound Chatwoot message attachment bytes for multimodal AI turns.

Chatwoot attachment data_urls are absolute, directly-fetchable URLs (either
pre-signed cloud storage or Chatwoot's own served asset route) — a plain,
unauthenticated client is used deliberately, NOT ChatwootClient, so the
account API token is never sent to an external host.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MIME_TYPE = "application/octet-stream"


async def fetch_attachment_bytes(data_url: str) -> tuple[bytes, str] | None:
    """Download an attachment. Returns (bytes, mime_type), or None on any
    failure — a broken URL must never break the turn it's attached to."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(data_url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            mime_type = content_type or _DEFAULT_MIME_TYPE
            return response.content, mime_type
    except Exception:
        logger.warning("media: failed to fetch attachment %s", data_url, exc_info=True)
        return None
