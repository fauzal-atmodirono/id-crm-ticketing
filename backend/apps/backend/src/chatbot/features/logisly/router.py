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
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from chatbot.features.logisly.console import CONSOLE_HTML
from chatbot.features.logisly.trips import ANOMALIES, TRIPS, register_session, render

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class AnomalyBody(BaseModel):
    order_id: str
    kind: str


def _e164(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"+{digits}" if digits else ""


class ChatwootDriverChannel:
    """The four Chatwoot calls the simulator needs, nothing more."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base = f"{settings.chatwoot_api_url.rstrip('/')}/api/v1/accounts/{settings.chatwoot_account_id}"
        self._headers = {"api_access_token": settings.chatwoot_api_token}
        self._client = client

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        if self._client is not None:
            res = await self._client.request(method, self._base + path, headers=self._headers, **kw)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.request(method, self._base + path, headers=self._headers, **kw)
        res.raise_for_status()
        return res.json() if res.content else {}

    async def find_or_create_contact(self, phone: str, name: str) -> int:
        found = await self._request("GET", "/contacts/search", params={"q": phone})
        for contact in found.get("payload") or []:
            if _e164(contact.get("phone_number") or "") == phone:
                return int(contact["id"])
        created = await self._request("POST", "/contacts", json={"name": name, "phone_number": phone})
        payload = created.get("payload") or created
        contact = payload.get("contact") or payload
        return int(contact["id"])

    async def resolve_open_conversations(self, contact_id: int, inbox_id: int) -> None:
        """A driver reply lands in the contact's latest unresolved conversation,
        so close the others first or the reply could go to an older one."""
        listed = await self._request("GET", f"/contacts/{contact_id}/conversations")
        for conv in listed.get("payload") or []:
            if conv.get("inbox_id") == inbox_id and conv.get("status") != "resolved":
                await self._request(
                    "POST", f"/conversations/{conv['id']}/toggle_status", json={"status": "resolved"}
                )

    async def open_conversation(self, contact_id: int, inbox_id: int, phone: str, text: str) -> int:
        created = await self._request(
            "POST",
            "/conversations",
            json={
                "source_id": f"whatsapp:{phone}",
                "inbox_id": inbox_id,
                "contact_id": contact_id,
                "status": "pending",
                "message": {"content": text},
            },
        )
        return int(created["id"])


def build_logisly_router(settings: Settings, channel: ChatwootDriverChannel | None = None) -> APIRouter:
    router = APIRouter()
    driver_channel = channel or ChatwootDriverChannel(settings)

    def _authorize(x_api_key: str | None) -> None:
        key = settings.proton_backend_key
        if not key or x_api_key is None or not hmac.compare_digest(
            x_api_key.encode("utf-8"), key.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

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
    async def raise_anomaly(body: AnomalyBody, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        trip = TRIPS.get(body.order_id)
        if trip is None:
            raise HTTPException(status_code=404, detail=f"Unknown order {body.order_id}")
        anomaly = ANOMALIES.get(body.kind)
        if anomaly is None:
            raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(ANOMALIES)}")
        phone = _e164(settings.logisly_demo_driver_phone)
        if not phone or not settings.logisly_driver_inbox_id:
            raise HTTPException(
                status_code=503,
                detail="LOGISLY_DEMO_DRIVER_PHONE and LOGISLY_DRIVER_INBOX_ID must be set",
            )
        opening = render(anomaly["opening"], trip)
        inbox_id = settings.logisly_driver_inbox_id
        try:
            contact_id = await driver_channel.find_or_create_contact(phone, trip["driver_name"])
            await driver_channel.resolve_open_conversations(contact_id, inbox_id)
            conversation_id = await driver_channel.open_conversation(contact_id, inbox_id, phone, opening)
        except httpx.HTTPError as exc:
            _log.warning("logisly_anomaly_chatwoot_failed", order_id=trip["order_id"], error=str(exc))
            raise HTTPException(status_code=502, detail="Chatwoot call failed") from exc
        register_session(f"crm-{conversation_id}", trip["order_id"], body.kind, opening)
        TRIPS.update(trip["order_id"], f"alert:{body.kind}", render(anomaly["event"], trip))
        _log.info("logisly_anomaly_raised", order_id=trip["order_id"], kind=body.kind, conversation_id=conversation_id)
        return {"conversation_id": conversation_id, "order_id": trip["order_id"], "message": opening}

    return router
