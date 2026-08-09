"""Collect a conversation's attachments so escalation mail carries the evidence.

A complaint escalation whose photo of the damaged part stays behind in Chatwoot
forces the PIC to open the CRM to see what the customer sent. Attaching it is
the difference between an actionable email and a notification.

Everything here is best-effort by construction. The escalation is the payload;
the attachments are a courtesy. A download failure, an unreadable message list
or an oversized file produces a *skip note* that the caller appends to the mail
body, never an exception -- losing the photo is a nuisance, losing the
escalation is the defect this package exists to eliminate.

Newest-first is deliberate: when the budget cannot fit everything, the most
recent evidence is the most relevant to whatever was just escalated.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

_log = structlog.get_logger(__name__)

Attachment = tuple[str, bytes, str]  # (filename, content, mimetype) -- see email_sender

# Deliberately a small allowlist, not a blocklist. Escalation mail is forwarded
# to dealers outside the company, and "everything except the extensions we
# thought of" is not a posture worth defending.
DEFAULT_ALLOWED = (
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
    "application/pdf",
    "video/mp4",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
)


class AttachmentFetcher(Protocol):
    """The two Chatwoot calls this module needs, named so tests can fake them
    without a transport."""

    async def list_messages(self, conv_id: str) -> list[dict[str, Any]]: ...
    async def download(self, url: str) -> bytes: ...


class ChatwootAttachmentFetcher:
    """The real fetcher: Chatwoot for the message list, plain HTTP for the file.

    ``data_url`` is an absolute URL to the stored blob, so the download is a
    bare GET and deliberately does NOT carry the Chatwoot API token -- the
    token belongs to the API host, and attachment storage may be a different
    origin entirely (GCS, S3) where sending it would leak the credential.
    """

    def __init__(self, request: Any, *, timeout: float = 15.0) -> None:
        self._request = request
        self._timeout = timeout

    async def list_messages(self, conv_id: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/conversations/{conv_id}/messages", None)
        if isinstance(data, dict):
            payload = data.get("payload")
            return list(payload) if isinstance(payload, list) else []
        return list(data) if isinstance(data, list) else []

    async def download(self, url: str) -> bytes:
        import httpx

        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=self._timeout, follow_redirects=True)
            res.raise_for_status()
            return bytes(res.content)


def _rows(messages: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """Flatten messages to (created_at, attachment) pairs, newest first."""
    out: list[tuple[int, dict[str, Any]]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        try:
            stamp = int(message.get("created_at") or 0)
        except (TypeError, ValueError):
            stamp = 0
        for attachment in message.get("attachments") or []:
            if isinstance(attachment, dict):
                out.append((stamp, attachment))
    out.sort(key=lambda pair: pair[0], reverse=True)
    return out


def _name(attachment: dict[str, Any], index: int) -> str:
    raw = attachment.get("file_name") or attachment.get("filename")
    return str(raw) if raw else f"attachment-{index}"


async def collect(
    fetcher: AttachmentFetcher,
    conv_id: str,
    *,
    budget_bytes: int,
    allowed: tuple[str, ...] = DEFAULT_ALLOWED,
) -> tuple[list[Attachment], list[str]]:
    """Return ``(attachments, skip_notes)`` for a conversation.

    ``budget_bytes`` of 0 (the default when the feature is off) short-circuits
    before any HTTP call -- the caller must not pay for a fetch whose result it
    discards.

    ``skip_notes`` are human sentences meant to be appended to the mail body,
    so a PIC reading the escalation knows something exists that they did not
    receive, rather than silently not knowing.
    """
    if budget_bytes <= 0:
        return [], []

    try:
        messages = await fetcher.list_messages(conv_id)
    except Exception as exc:
        _log.warning("escalation_attachments_list_failed", conv_id=conv_id, error=str(exc))
        return [], ["Attachments could not be read from the conversation."]

    files: list[Attachment] = []
    skipped: list[str] = []
    used = 0

    for index, (_stamp, attachment) in enumerate(_rows(messages), start=1):
        name = _name(attachment, index)
        mimetype = str(attachment.get("file_type") or attachment.get("content_type") or "")
        url = attachment.get("data_url") or attachment.get("thumb_url")

        if mimetype not in allowed:
            skipped.append(f"{name} (unsupported type {mimetype or 'unknown'})")
            continue
        if not url:
            skipped.append(f"{name} (no download URL)")
            continue

        # Chatwoot already tells us the size; trust it to skip before paying
        # for a download we would only discard.
        declared = attachment.get("file_size")
        if isinstance(declared, (int, float)) and used + int(declared) > budget_bytes:
            skipped.append(f"{name} (too large for the {budget_bytes // 1024} KB limit)")
            continue

        try:
            blob = await fetcher.download(str(url))
        except Exception as exc:
            _log.warning(
                "escalation_attachment_download_failed",
                conv_id=conv_id,
                name=name,
                error=str(exc),
            )
            skipped.append(f"{name} (could not be downloaded)")
            continue

        if used + len(blob) > budget_bytes:
            skipped.append(f"{name} (too large for the {budget_bytes // 1024} KB limit)")
            continue

        files.append((name, blob, mimetype))
        used += len(blob)

    if skipped:
        _log.info(
            "escalation_attachments_partial",
            conv_id=conv_id,
            attached=len(files),
            skipped=len(skipped),
        )
    return files, skipped
