"""Mock "Proton e.MAS customer app" chat routes (demo only).

The test console's phone-sized Customer page reads and posts here. The page
never talks to Chatwoot itself (and never holds a Chatwoot token): these
routes find the persona's open conversation on the Proton API inbox and post
the customer's text, photo, video or voice note into it as an INCOMING
message. From there it is the production path: the agent service's bot
answers with the same persona, KB, tools and handoff, and the conversation
sits on the persona's contact, so Customer 360 shows chat and phone history
together. Pattern ported from the Logisly driver app (logisly-demo branch,
`features/logisly/driver_chat.py`).

Guards, in order (each one alone keeps a real customer out of reach):
1. DEMO_CUSTOMER_APP_ENABLED (default false) -> 404 otherwise.
2. The persona ID must be on the server-side allow-list (personas.py); the
   page cannot name a number.
3. A matched contact's name must start with exactly "[DEMO] " (the demo
   console's strict rule), or nothing is posted (409): a persona number that
   somehow belongs to a real customer is never written to. Contacts this
   module creates are always named "[DEMO] ..." -- including the "new"
   persona, whose first chat creates "[DEMO] New caller" (after which the
   phone demo's "new caller" finds a contact; delete it to reset).
4. Browser Origin must be in FRONTEND_ORIGINS for a POST (a same-origin GET
   may omit Origin; a present foreign one is refused). Deployed behind the
   test console's Caddy basic auth (/apps/test-console/api/*).

Only the app's OWN conversation is ever read or posted into (marker
``additional_attributes.demo_customer_app == <persona id>``): never a seeded
demo case, a phone call or any other case of the contact. A new one is
opened pending, labelled ``demo_persona`` + ``audit_test`` and stamped
``demo_seed=demo_persona`` so the demo console's reset and
METRICS_EXCLUDE_DEMO_SEED cover it. A per-persona in-process lock keeps two
fast posts from opening two conversations (one backend container per
tenant; a second replica would not share it).

The POST rate limit (30/min) is keyed by client IP, and behind Caddy every
console user shares the proxy's IP, so in practice it is one GLOBAL budget.

Reading (GET) never creates a contact or a conversation. No log line here
carries a phone number (the persona id identifies the record).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from chatbot.features.chat.phone.rate_limit import RateLimiter
from chatbot.features.customer_app.personas import (
    DemoPersona,
    allowed_persona,
    contact_name,
)

if TYPE_CHECKING:
    from chatbot.features.customer_app.channel import CustomerAppChannel
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
# Explicit, not "image/*": what a phone camera / the page's WAV encoder makes
# and Chatwoot + Gemini can handle.
ALLOWED_UPLOAD_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "video/mp4",
        "video/quicktime",
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/ogg",
        "audio/webm",
    }
)
DEMO_NAME_PREFIX = "[DEMO] "
_POSTS_PER_MINUTE = 30


def customer_view(message: dict[str, Any]) -> dict[str, Any] | None:
    """A Chatwoot message as the customer sees it; None for private notes and
    activity rows. Agent names are not exposed."""
    if message.get("private") or message.get("message_type") not in (0, 1):
        return None
    incoming = message.get("message_type") == 0
    sender_type = str((message.get("sender") or {}).get("type") or "")
    by = "customer" if incoming else ("agent" if sender_type == "user" else "ai")
    return {
        "id": message.get("id"),
        "from": "customer" if incoming else "proton",
        "by": by,
        "text": message.get("content") or "",
        "at": message.get("created_at"),
        "attachments": [
            {"type": a.get("file_type"), "url": a.get("data_url"), "thumb": a.get("thumb_url")}
            for a in message.get("attachments") or []
        ],
    }


def build_customer_app_router(settings: Settings, channel: CustomerAppChannel) -> APIRouter:  # noqa: PLR0915
    router = APIRouter()
    limiter = RateLimiter(_POSTS_PER_MINUTE, 60.0)
    # Bounded by the allow-list (one lock per persona id).
    locks: dict[str, asyncio.Lock] = {}

    def _guard(request: Request, persona_id: str | None, *, write: bool) -> DemoPersona:
        if not settings.demo_customer_app_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        origin = request.headers.get("origin") or ""
        if (write or origin) and origin not in settings.frontend_origins:
            _log.warning("customer_app_origin_rejected", origin=origin)
            raise HTTPException(status_code=403, detail="Origin not allowed")
        persona = allowed_persona(persona_id)
        if persona is None:
            raise HTTPException(status_code=403, detail="Not a demo persona")
        return persona

    def _inbox_id() -> int:
        inbox_id = settings.demo_customer_app_inbox_id or settings.chatwoot_inbox_id
        if not inbox_id or not settings.chatwoot_api_token or not settings.chatwoot_enabled:
            raise HTTPException(
                status_code=503,
                detail="Chatwoot API token and inbox (DEMO_CUSTOMER_APP_INBOX_ID or CHATWOOT_INBOX_ID) must be set",
            )
        return int(inbox_id)

    def _demo_contact_id(contact: dict[str, Any], persona: DemoPersona) -> int:
        if not str(contact.get("name") or "").startswith(DEMO_NAME_PREFIX):
            _log.warning("customer_app_contact_not_demo", persona=persona.id)
            raise HTTPException(status_code=409, detail="That number belongs to a non-demo contact")
        return int(contact["id"])

    @router.get("/customer-app/messages")
    async def list_messages(
        request: Request, persona: Annotated[str | None, Query()] = None
    ) -> dict[str, Any]:
        p = _guard(request, persona, write=False)
        inbox_id = _inbox_id()
        try:
            contact = await channel.find_contact_by_phone(p.number)
            if contact is None:
                return {"conversation_id": None, "messages": []}
            contact_id = _demo_contact_id(contact, p)
            conversation_id = await channel.latest_own_conversation(contact_id, inbox_id, p.id)
            raw = await channel.messages(conversation_id) if conversation_id else []
        except httpx.HTTPError as exc:
            _log.warning("customer_app_read_failed", error_type=type(exc).__name__)
            raise HTTPException(status_code=502, detail="Chatwoot call failed") from exc
        views = [v for v in (customer_view(m) for m in raw) if v is not None]
        return {"conversation_id": conversation_id, "messages": views}

    @router.post("/customer-app/messages")
    async def post_message(
        request: Request,
        persona: Annotated[str | None, Form()] = None,
        text: Annotated[str, Form()] = "",
        file: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, Any]:
        p = _guard(request, persona, write=True)
        upload = None
        if file is not None:
            content_type = (file.content_type or "").split(";")[0].strip().lower()
            if content_type not in ALLOWED_UPLOAD_TYPES:
                raise HTTPException(status_code=415, detail="Photo, video or voice note only")
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="File too large")
            upload = (file.filename or "file", data, content_type)
        body = text.strip()
        if not body and upload is None:
            raise HTTPException(status_code=422, detail="Nothing to send")
        inbox_id = _inbox_id()
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            raise HTTPException(status_code=429, detail="Too many messages")
        lock = locks.setdefault(p.id, asyncio.Lock())
        try:
            async with lock:
                contact = await channel.find_contact_by_phone(p.number)
                if contact is None:
                    contact_id = await channel.create_contact(
                        contact_name(p), p.number, p.seed_attributes or None
                    )
                else:
                    contact_id = _demo_contact_id(contact, p)
                conversation_id = await channel.latest_own_conversation(contact_id, inbox_id, p.id)
                if conversation_id is None:
                    # The customer writes first: start a conversation for the bot.
                    conversation_id = await channel.open_conversation(contact_id, inbox_id, p.id)
                await channel.post_incoming(conversation_id, body, upload)
        except httpx.HTTPError as exc:
            _log.warning("customer_app_post_failed", error_type=type(exc).__name__)
            raise HTTPException(status_code=502, detail="Chatwoot call failed") from exc
        _log.info(
            "customer_app_message_posted",
            persona=p.id,
            conversation_id=conversation_id,
            attachment=upload[2] if upload else None,
        )
        return {"conversation_id": conversation_id}

    return router
