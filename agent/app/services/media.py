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

# Generic/unhelpful Content-Type values that don't actually tell us anything
# about the attachment — treated the same as a missing header so the
# file_type_hint fallback kicks in.
_GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}

# file_type ("image"/"audio", as reported by Chatwoot) -> sensible default
# mime type, used only when Content-Type is missing/generic. "audio/ogg"
# matches WhatsApp/Twilio's actual voice-note format — the same default
# handle_voice_turn already uses for the voice channel (see
# backend/apps/backend/src/chatbot/features/chat/service.py).
_FILE_TYPE_MIME_DEFAULTS = {
    "audio": "audio/ogg",
    "image": "image/jpeg",
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
        async with httpx.AsyncClient(timeout=15.0) as client:
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
