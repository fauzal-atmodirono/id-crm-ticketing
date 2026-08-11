"""Fetch inbound Chatwoot message attachment bytes for multimodal AI turns.

Chatwoot attachment data_urls are absolute and unauthenticated, but they are
NOT always a direct 200: with local disk storage (ACTIVE_STORAGE_SERVICE=local,
what the tenant stacks run) Chatwoot hands out the Active Storage
`/rails/active_storage/blobs/redirect/...` route, which answers **302** and
points at the real `/disk/...` URL. httpx does not follow redirects by default,
so `follow_redirects=True` below is load-bearing, not a nicety — without it
`raise_for_status()` raised on the 302 and every WhatsApp voice note was
silently dropped (the agent-bot then never replied at all). Cloud-storage
tenants get a pre-signed URL and 200 directly; both must work.

A plain, unauthenticated client is used deliberately, NOT ChatwootClient, so the
account API token is never sent to an external host — which is also why
following the redirect is safe here: there is no credential to leak onto the
redirect target.

Mime resolution lives in `media_registry`, shared by specification with the
backend's assist media path so a voice note is understood identically whether
the bot answers automatically or an agent clicks "Suggest a reply". A parity
test asserts the two registries stay identical.
"""

from __future__ import annotations

import logging

import httpx

from app.services.media_registry import resolve_mime

logger = logging.getLogger(__name__)

# Preserved as the last-resort mime for a kind the registry has no default for.
# Reached only when Content-Type is missing/generic AND the registry declines to
# guess; sending *something* keeps the pre-registry behaviour of never dropping
# an attachment purely for want of a mime type.
_DEFAULT_MIME_TYPE = "application/octet-stream"


async def fetch_attachment_bytes(
    data_url: str, file_type_hint: str | None = None
) -> tuple[bytes, str] | None:
    """Download an attachment. Returns (bytes, mime_type), or None on any
    failure — a broken URL must never break the turn it's attached to.

    `file_type_hint` (Chatwoot's own attachment `file_type`, e.g. "audio" or
    "image") is used only as a fallback when the response's Content-Type is
    missing or generic — a real Content-Type header always wins.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(data_url)
            response.raise_for_status()
            mime_type = resolve_mime(file_type_hint, response.headers.get("content-type"))
            return response.content, mime_type or _DEFAULT_MIME_TYPE
    except Exception:
        logger.warning("media: failed to fetch attachment %s", data_url, exc_info=True)
        return None
