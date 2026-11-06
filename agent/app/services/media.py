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
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.services.media_registry import resolve_mime

logger = logging.getLogger(__name__)

# Preserved as the last-resort mime for a kind the registry has no default for.
# Reached only when Content-Type is missing/generic AND the registry declines to
# guess; sending *something* keeps the pre-registry behaviour of never dropping
# an attachment purely for want of a mime type.
_DEFAULT_MIME_TYPE = "application/octet-stream"


_ACTIVE_STORAGE_PREFIX = "/rails/active_storage/"


def to_internal_url(data_url: str, internal_base: str | None) -> str:
    """Point a Chatwoot local-storage attachment URL at the in-stack Chatwoot.

    Chatwoot builds `data_url` from its FRONTEND_URL, which is the browser's
    origin (`http://crm.localhost` on the local tenant, a public nip.io or real
    domain elsewhere). From inside the agent container that host either does
    not resolve at all (`crm.localhost`) or hairpins out through the public
    internet. Only Active Storage paths are rewritten: they are served by
    Chatwoot itself, so the internal `CHATWOOT_URL` answers them identically.
    A cloud-storage pre-signed URL is left alone.
    """
    if not internal_base:
        return data_url
    parsed = urlsplit(data_url)
    if not parsed.path.startswith(_ACTIVE_STORAGE_PREFIX):
        return data_url
    base = urlsplit(internal_base)
    return urlunsplit((base.scheme, base.netloc, parsed.path, parsed.query, ""))


async def fetch_attachment_bytes(
    data_url: str, file_type_hint: str | None = None, internal_base: str | None = None
) -> tuple[bytes, str] | None:
    """Download an attachment. Returns (bytes, mime_type), or None on any
    failure — a broken URL must never break the turn it's attached to.

    `file_type_hint` (Chatwoot's own attachment `file_type`, e.g. "audio" or
    "image") is used only as a fallback when the response's Content-Type is
    missing or generic — a real Content-Type header always wins.

    `internal_base` (the agent's `CHATWOOT_URL`) redirects Active Storage URLs
    to the in-stack Chatwoot; see `to_internal_url`.
    """
    data_url = to_internal_url(data_url, internal_base)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(data_url)
            response.raise_for_status()
            mime_type = resolve_mime(file_type_hint, response.headers.get("content-type"))
            return response.content, mime_type or _DEFAULT_MIME_TYPE
    except Exception:
        logger.warning("media: failed to fetch attachment %s", data_url, exc_info=True)
        return None
