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
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MIME_TYPE = "application/octet-stream"

# Generic/unhelpful Content-Type values that don't actually tell us anything
# about the attachment — treated the same as a missing header so the
# file_type_hint fallback kicks in.
_GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}

# file_type ("audio"/"image"/"video", as reported by Chatwoot) -> sensible
# default mime type, used only when Content-Type is missing/generic. "audio/ogg"
# matches WhatsApp/Twilio's actual voice-note format — the same default
# handle_voice_turn already uses for the voice channel (see
# backend/apps/backend/src/chatbot/features/chat/service.py).
_FILE_TYPE_MIME_DEFAULTS = {
    "audio": "audio/ogg",
    "image": "image/jpeg",
    # WhatsApp/Twilio deliver customer videos as MP4; used only when the
    # response Content-Type is missing or generic.
    "video": "video/mp4",
}


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
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            if content_type not in _GENERIC_CONTENT_TYPES:
                mime_type = content_type
            else:
                mime_type = _FILE_TYPE_MIME_DEFAULTS.get(file_type_hint or "", _DEFAULT_MIME_TYPE)
            return response.content, mime_type
    except Exception:
        logger.warning("media: failed to fetch attachment %s", data_url, exc_info=True)
        return None
