"""Driver-app call routes: the driver page, its Voice token, placing the
agent's call, and the Twilio Media Stream that bridges it to Gemini Live.
Split from router.py; mounted by `build_logisly_router`."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

from chatbot.features.logisly import call as call_module
from chatbot.features.logisly.call import CALL_TOOLS, DRIVER_IDENTITY, LogislyCallBridge
from chatbot.features.logisly.driver_page import DRIVER_HTML
from chatbot.features.logisly.trips import ACTIVE_SESSIONS, TRIPS

if TYPE_CHECKING:
    from chatbot.features.logisly.channel import ChatwootDriverChannel
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class CallBody(BaseModel):
    order_id: str


def latest_session_for(order_id: str) -> str | None:
    """The most recent alert conversation for an order (dict keeps insertion order)."""
    found = None
    for session_id, active in ACTIVE_SESSIONS.items():
        if active["order_id"] == order_id:
            found = session_id
    return found


def mint_driver_token(settings: Settings) -> str:
    """Voice token that lets the driver page RECEIVE calls (Proton's softphone
    token only places them)."""
    token = AccessToken(
        settings.twilio_account_sid,
        settings.twilio_api_key_sid,
        settings.twilio_api_key_secret,
        identity=DRIVER_IDENTITY,
        ttl=3600,
    )
    token.add_grant(VoiceGrant(incoming_allow=True))
    return str(token.to_jwt())


async def run_call_stream(
    websocket: WebSocket,
    settings: Settings,
    channel: ChatwootDriverChannel | None,
    live_session_factory: Callable[..., Any],
) -> None:
    await websocket.accept()
    try:
        start_msg = json.loads(await websocket.receive_text())
        if start_msg.get("event") == "connected":
            start_msg = json.loads(await websocket.receive_text())
        session_id = call_module.session_from_start(start_msg)
        if session_id not in ACTIVE_SESSIONS:
            _log.warning("logisly_call_unknown_session", session_id=session_id)
            await websocket.close()
            return
        async with live_session_factory(
            settings, call_module.call_instruction(session_id), CALL_TOOLS
        ) as live:
            bridge = LogislyCallBridge(live, websocket.send_json, settings, session_id, channel)
            await bridge.handle_twilio(start_msg)

            async def from_twilio() -> None:
                while True:
                    msg = json.loads(await websocket.receive_text())
                    if msg.get("event") == "stop":
                        return
                    await bridge.handle_twilio(msg)

            tasks = {asyncio.create_task(from_twilio()), asyncio.create_task(bridge.pump())}
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await bridge.finalize()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        _log.error("logisly_call_stream_failed", error=str(exc))
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


def build_call_router(
    settings: Settings,
    _authorize: Callable[[str | None], None],
    channel: ChatwootDriverChannel | None,
    live_session_factory: Callable[..., Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/logisly/driver", response_class=HTMLResponse)
    async def driver_page() -> str:
        return DRIVER_HTML

    @router.get("/logisly/driver/token")
    async def driver_token(x_api_key: str | None = Header(default=None)) -> dict[str, str]:
        _authorize(x_api_key)
        if not (settings.twilio_api_key_sid and settings.twilio_api_key_secret):
            raise HTTPException(status_code=503, detail="Twilio API key not configured")
        return {"token": mint_driver_token(settings), "identity": DRIVER_IDENTITY}

    @router.post("/logisly/call")
    async def call_driver(
        body: CallBody, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        trip = TRIPS.get(body.order_id)
        if trip is None:
            raise HTTPException(status_code=404, detail=f"Unknown order {body.order_id}")
        session_id = latest_session_for(trip["order_id"])
        if session_id is None:
            raise HTTPException(status_code=409, detail="Raise an alert on this trip first")
        if not settings.twilio_webhook_base_url:
            raise HTTPException(status_code=503, detail="TWILIO_WEBHOOK_BASE_URL not configured")
        try:
            call_sid = await call_module.start_call(settings, session_id)
        except httpx.HTTPError as exc:
            _log.warning("logisly_call_start_failed", order_id=trip["order_id"], error=str(exc))
            raise HTTPException(status_code=502, detail="Twilio call failed") from exc
        TRIPS.update(trip["order_id"], "alert:call", "Telepon otomatis ke driver")
        _log.info(
            "logisly_call_started",
            order_id=trip["order_id"],
            session_id=session_id,
            call_sid=call_sid,
        )
        return {"call_sid": call_sid, "session_id": session_id}

    @router.websocket("/logisly/call/stream")
    async def call_stream(websocket: WebSocket) -> None:
        """Twilio Media Stream for a call placed by POST /logisly/call. Twilio
        does not sign websocket upgrades, so the stream is accepted only for a
        session the simulator registered."""
        await run_call_stream(websocket, settings, channel, live_session_factory)

    return router
