"""The mock driver app's chat: the driver page reads and posts messages here.

The page never talks to Chatwoot itself (and never holds a Chatwoot token):
these routes find the demo driver's open conversation on the driver app's
API inbox and post the driver's text, photo, video or voice note into it as
an incoming message. From there it is the same path as WhatsApp: the agent
bot answers, with the same persona, trip tools and escalation tickets.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from chatbot.features.logisly.channel import APP_DRIVER_IDENTIFIER, ChatwootDriverChannel, _e164

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_MAX_UPLOAD_BYTES = 15 * 1024 * 1024


async def driver_address(
    settings: Settings, channel: ChatwootDriverChannel, which: str, driver_name: str
) -> tuple[int, int, str]:
    """(contact_id, inbox_id, source_id) of the demo driver on a channel."""
    if which == "app":
        if not settings.logisly_app_inbox_id:
            raise HTTPException(status_code=503, detail="LOGISLY_APP_INBOX_ID must be set")
        contact_id = await channel.find_contact_by_identifier(APP_DRIVER_IDENTIFIER)
        if contact_id is None:
            raise HTTPException(status_code=503, detail="Demo driver contact not provisioned")
        return contact_id, settings.logisly_app_inbox_id, APP_DRIVER_IDENTIFIER
    phone = _e164(settings.logisly_demo_driver_phone)
    if not phone or not settings.logisly_driver_inbox_id:
        raise HTTPException(
            status_code=503,
            detail="LOGISLY_DEMO_DRIVER_PHONE and LOGISLY_DRIVER_INBOX_ID must be set",
        )
    contact_id = await channel.find_or_create_contact(phone, driver_name)
    return contact_id, settings.logisly_driver_inbox_id, f"whatsapp:{phone}"


def driver_view(message: dict[str, Any]) -> dict[str, Any] | None:
    """A Chatwoot message as the driver sees it; None for notes and activity."""
    if message.get("private") or message.get("message_type") not in (0, 1):
        return None
    return {
        "id": message.get("id"),
        "from": "driver" if message.get("message_type") == 0 else "logisly",
        "text": message.get("content") or "",
        "at": message.get("created_at"),
        "attachments": [
            {"type": a.get("file_type"), "url": a.get("data_url"), "thumb": a.get("thumb_url")}
            for a in message.get("attachments") or []
        ],
    }


def build_driver_chat_router(
    settings: Settings,
    _authorize: Callable[[str | None], None],
    channel: ChatwootDriverChannel,
) -> APIRouter:
    router = APIRouter()

    async def _address() -> tuple[int, int, str]:
        return await driver_address(settings, channel, "app", "Pak Budi Santoso")

    @router.get("/logisly/driver/messages")
    async def list_messages(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        try:
            contact_id, inbox_id, _ = await _address()
            conversation_id = await channel.latest_open_conversation(contact_id, inbox_id)
            raw = await channel.messages(conversation_id) if conversation_id else []
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Chatwoot call failed") from exc
        views = [v for v in (driver_view(m) for m in raw) if v is not None]
        return {"conversation_id": conversation_id, "messages": views}

    @router.post("/logisly/driver/messages")
    async def post_message(
        text: Annotated[str, Form()] = "",
        file: Annotated[UploadFile | None, File()] = None,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        upload = None
        if file is not None:
            data = await file.read()
            if len(data) > _MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="File too large")
            upload = (
                file.filename or "file",
                data,
                file.content_type or "application/octet-stream",
            )
        if not text.strip() and upload is None:
            raise HTTPException(status_code=422, detail="Nothing to send")
        try:
            contact_id, inbox_id, source_id = await _address()
            conversation_id = await channel.latest_open_conversation(contact_id, inbox_id)
            if conversation_id is None:
                # The driver writes first: start a conversation for the bot.
                conversation_id = await channel.open_conversation(contact_id, inbox_id, source_id)
            await channel.post_driver_message(conversation_id, text.strip(), upload)
        except httpx.HTTPError as exc:
            _log.warning("logisly_driver_post_failed", error=str(exc))
            raise HTTPException(status_code=502, detail="Chatwoot call failed") from exc
        return {"conversation_id": conversation_id}

    return router
