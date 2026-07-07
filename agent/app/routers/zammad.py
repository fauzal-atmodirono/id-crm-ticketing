"""Zammad webhook (trigger) receiver.

Verifies the HMAC signature, dedupes by `X-Zammad-Delivery`, drops payloads
authored by our own integration user (loop prevention — otherwise a note
posted back into a ticket by `sync.on_ticket_event` could itself re-trigger
this webhook), and background-dispatches everything else to
`sync.on_ticket_event`.
"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.config import get_settings
from app.security import verify_zammad_signature
from app.services import sync
from app.services.dedupe import claim_delivery

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/zammad")
async def zammad_webhook(request: Request, background_tasks: BackgroundTasks):
    settings = get_settings()
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature")
    delivery_id = request.headers.get("X-Zammad-Delivery")

    if not verify_zammad_signature(settings.zammad_webhook_secret, body, signature):
        raise HTTPException(status_code=401, detail="invalid signature")

    if not await claim_delivery(delivery_id, "zammad"):
        logger.info("zammad_webhook: duplicate delivery %s, skipping", delivery_id)
        return {"ok": True}

    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        logger.warning("zammad_webhook: could not parse JSON body, skipping")
        return {"ok": True}

    article = payload.get("article") or {}
    author_login = (article.get("created_by") or {}).get("login")
    if author_login and author_login == settings.zammad_integration_login:
        logger.info(
            "zammad_webhook: dropping self-authored payload for loop prevention"
        )
        return {"ok": True}

    background_tasks.add_task(sync.on_ticket_event, payload)
    return {"ok": True}
