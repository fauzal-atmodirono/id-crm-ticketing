"""Simulated tracking system for the Logisly driver follow-up demo.

`POST /logisly/anomaly` stands in for Logisly's tracking system raising an
alert on a trip: it opens a WhatsApp conversation with the driver in the
Chatwoot driver inbox, posts the opening message, and registers the
conversation so the driver agent knows which trip the replies are about
(`trips.context_for_session`). `GET /logisly/console` is a one-page operator
view for running the demo.

Mounted only when AGENT_PERSONA=logisly. Every route but the console page
needs the backend API key; the page itself holds no secret and sends the key
the operator opens it with (`?key=`), because on the local tenant
crm.localhost is also reachable through the Twilio tunnel.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from chatbot.features.chat.phone.gemini_live import connect_live
from chatbot.features.logisly.call_router import build_call_router
from chatbot.features.logisly.channel import ChatwootDriverChannel
from chatbot.features.logisly.console import CONSOLE_HTML
from chatbot.features.logisly.driver_chat import build_driver_chat_router, driver_address
from chatbot.features.logisly.trips import (
    ANOMALIES,
    TRIPS,
    register_session,
    render,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class AnomalyBody(BaseModel):
    order_id: str
    kind: str
    # "app" (mock driver app) or "whatsapp"; empty picks the app when configured.
    channel: str = ""


def make_authorizer(settings: Settings) -> Callable[[str | None], None]:
    def _authorize(x_api_key: str | None) -> None:
        key = settings.proton_backend_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode("utf-8"), key.encode("utf-8"))
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    return _authorize


def build_logisly_router(
    settings: Settings,
    channel: ChatwootDriverChannel | None = None,
    live_session_factory: Callable[..., Any] = connect_live,
) -> APIRouter:
    router = APIRouter()
    driver_channel = channel or ChatwootDriverChannel(settings)
    _authorize = make_authorizer(settings)
    router.include_router(build_call_router(settings, _authorize, channel, live_session_factory))
    router.include_router(build_driver_chat_router(settings, _authorize, driver_channel))

    @router.get("/logisly/console", response_class=HTMLResponse)
    async def console() -> str:
        return CONSOLE_HTML

    @router.get("/logisly/trips")
    async def list_trips(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        return {
            "trips": list(TRIPS.trips.values()),
            "anomalies": {k: v["label"] for k, v in ANOMALIES.items()},
        }

    @router.post("/logisly/anomaly")
    async def raise_anomaly(
        body: AnomalyBody, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        trip = TRIPS.get(body.order_id)
        if trip is None:
            raise HTTPException(status_code=404, detail=f"Unknown order {body.order_id}")
        anomaly = ANOMALIES.get(body.kind)
        if anomaly is None:
            raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(ANOMALIES)}")
        which = body.channel or ("app" if settings.logisly_app_inbox_id else "whatsapp")
        if which not in ("app", "whatsapp"):
            raise HTTPException(status_code=422, detail="channel must be app or whatsapp")
        opening = render(anomaly["opening"], trip)
        try:
            contact_id, inbox_id, source_id = await driver_address(
                settings, driver_channel, which, trip["driver_name"]
            )
            await driver_channel.resolve_open_conversations(contact_id, inbox_id)
            conversation_id = await driver_channel.open_conversation(
                contact_id, inbox_id, source_id, opening
            )
        except httpx.HTTPError as exc:
            _log.warning(
                "logisly_anomaly_chatwoot_failed", order_id=trip["order_id"], error=str(exc)
            )
            raise HTTPException(status_code=502, detail="Chatwoot call failed") from exc
        register_session(f"crm-{conversation_id}", trip["order_id"], body.kind, opening)
        TRIPS.update(trip["order_id"], f"alert:{body.kind}", render(anomaly["event"], trip))
        _log.info(
            "logisly_anomaly_raised",
            order_id=trip["order_id"],
            kind=body.kind,
            conversation_id=conversation_id,
        )
        return {
            "conversation_id": conversation_id,
            "order_id": trip["order_id"],
            "message": opening,
            "channel": which,
        }

    return router
