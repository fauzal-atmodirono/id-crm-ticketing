"""Chatwoot webhook receiver.

Verifies the HMAC signature, dedupes by `X-Chatwoot-Delivery`, and dispatches
sync work to a FastAPI background task keyed off the `event` field so the
response returns immediately — the slow Chatwoot/Zammad API calls never run
inline in the request/response path.
"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.config import get_settings
from app.security import verify_chatwoot_signature
from app.services import sync
from app.services.dedupe import claim_delivery

logger = logging.getLogger(__name__)

router = APIRouter()

_CONTACT_EVENTS = {"contact_created", "contact_updated"}
_STATUS_ONLY_EVENTS = {"conversation_status_changed", "conversation_resolved"}


@router.post("/webhooks/chatwoot")
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks):
    settings = get_settings()
    body = await request.body()

    signature = request.headers.get("X-Chatwoot-Signature")
    timestamp = request.headers.get("X-Chatwoot-Timestamp")
    delivery_id = request.headers.get("X-Chatwoot-Delivery")

    if not verify_chatwoot_signature(
        settings.chatwoot_webhook_secret, timestamp, body, signature
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    if not await claim_delivery(delivery_id, "chatwoot"):
        logger.info("chatwoot_webhook: duplicate delivery %s, skipping", delivery_id)
        return {"ok": True}

    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        logger.warning("chatwoot_webhook: could not parse JSON body, skipping")
        return {"ok": True}

    event = payload.get("event")
    if event in _CONTACT_EVENTS:
        background_tasks.add_task(sync.upsert_contact, payload)
    elif event == "conversation_updated":
        background_tasks.add_task(sync.maybe_escalate, payload)
    elif event in _STATUS_ONLY_EVENTS:
        background_tasks.add_task(sync.record_conversation_status, payload)
    else:
        logger.info("chatwoot_webhook: unhandled event %r, ignoring", event)

    return {"ok": True}
