"""POST /escalation/notify -- two-thread email escalation (EM-7) for a
natively-escalated Email-channel conversation.

Called by the agent/ service's sync.maybe_escalate() when a human applies
the `escalate` label to a conversation on an Email inbox. Deliberately
separate from EscalationNotifier.notify(), which is the AI's own autonomous
escalation path (fired from ChatwootAdapter._fire_escalation) and never
reaches this endpoint -- the codebase already suppresses the `escalate`
label on AI-driven escalations to avoid the two paths colliding.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_CWRequest = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]


class _NotifyIn(BaseModel):
    conversation_id: str
    title: str
    body: str
    department: str | None = None
    dealer: str | None = None


def _require_api_key(settings: Settings):
    """401s unless x-api-key matches proton_backend_key -- the same key the
    agent/ service already authenticates its other backend calls with."""

    def _check(x_api_key: str | None = Header(default=None)) -> None:
        if (
            not x_api_key
            or not settings.proton_backend_key
            or not hmac.compare_digest(x_api_key, settings.proton_backend_key)
        ):
            raise HTTPException(status_code=401, detail="Missing or invalid API key")

    return _check


async def _resolve_customer_email(chatwoot_request: _CWRequest, conv_id: str) -> str | None:
    """Best-effort lookup of the conversation's contact email via
    GET /conversations/{id} (meta.sender.email). None on any failure or
    missing field -- the caller sends the ack only when this resolves."""
    try:
        data = await chatwoot_request("GET", f"/conversations/{conv_id}", None)
    except Exception:
        _log.warning("escalation_notify_customer_email_lookup_failed", conv_id=conv_id)
        return None
    if not isinstance(data, dict):
        return None
    sender = (data.get("meta") or {}).get("sender") or {}
    email = sender.get("email")
    return str(email) if email else None


def build_escalation_router(
    notifier: EscalationNotifier,
    chatwoot_request: _CWRequest,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()
    auth = _require_api_key(settings)

    @router.post("/escalation/notify", dependencies=[Depends(auth)])
    async def notify(payload: _NotifyIn) -> dict[str, str]:
        customer_email = await _resolve_customer_email(chatwoot_request, payload.conversation_id)
        await notifier.notify_email_channel_escalation(
            conv_id=payload.conversation_id,
            title=payload.title,
            body=payload.body,
            department=payload.department,
            dealer=payload.dealer,
            customer_email=customer_email,
        )
        return {"status": "ok"}

    return router
