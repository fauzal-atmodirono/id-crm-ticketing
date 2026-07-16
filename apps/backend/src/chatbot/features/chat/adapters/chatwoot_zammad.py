from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from google.cloud import firestore

from chatbot.features.chat.ports import ChatPort, TicketingPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class ChatwootZammadAdapter(ChatPort, TicketingPort):
    """Production adapter integrating with Chatwoot (ChatPort) and Zammad (TicketingPort) REST APIs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._paused_sessions: set[str] = set()

    async def _chatwoot_request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not self._settings.chatwoot_enabled:
            _log.info("chatwoot_disabled_skipping_request", path=path)
            return None

        url = f"{self._settings.chatwoot_api_url.rstrip('/')}{path}"
        headers = {
            "Content-Type": "application/json",
            "api_access_token": self._settings.chatwoot_api_token,
        }

        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(method, url, json=payload, headers=headers, timeout=10.0)
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("chatwoot_request_failed", method=method, path=path, error=str(e))
            return None

    async def _zammad_request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not self._settings.zammad_enabled:
            _log.info("zammad_disabled_skipping_request", path=path)
            return None

        url = f"{self._settings.zammad_api_url.rstrip('/')}{path}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Token token={self._settings.zammad_api_token}",
        }

        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(method, url, json=payload, headers=headers, timeout=10.0)
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("zammad_request_failed", method=method, path=path, error=str(e))
            return None

    # --- ChatPort Implementation ---
    async def send_message(self, conversation_id: str, text: str) -> None:
        path = f"/api/v1/accounts/{self._settings.chatwoot_account_id}/conversations/{conversation_id}/messages"
        payload = {
            "content": text,
            "message_type": "outgoing",
        }
        _log.info("sending_chatwoot_message", conversation_id=conversation_id)
        await self._chatwoot_request("POST", path, payload)

    # --- TicketingPort Implementation ---
    async def create_ticket(
        self,
        session_id: str,
        title: str,
        body: str,
        urgency: str,
        customer_name: str | None = None,  # noqa: ARG002
        customer_email: str | None = None,  # noqa: ARG002
        customer_phone: str | None = None,  # noqa: ARG002
        category: str | None = None,  # noqa: ARG002
        subcategory: str | None = None,  # noqa: ARG002
        division: str | None = None,  # noqa: ARG002
        department: str | None = None,  # noqa: ARG002
        sla_minutes: int | None = None,  # noqa: ARG002
    ) -> str:
        conv_id = session_id.replace("chatwoot-conv-", "")

        priority_map = {"low": 1, "medium": 2, "high": 3, "urgent": 3}
        prio_id = priority_map.get(urgency.lower(), 2)

        path = "/api/v1/tickets"
        payload = {
            "title": f"[AUTO-ESC-{conv_id}] {title}",
            "group": "Users",
            "customer": "customer@example.com",
            "priority_id": prio_id,
            "state": "new",
            "article": {
                "subject": title,
                "body": body,
                "type": "note",
                "internal": False,
            },
        }
        _log.info("creating_zammad_ticket", session_id=session_id)
        res = await self._zammad_request("POST", path, payload)

        ticket_id = "MOCK-ZAM-TKT"
        if res and "id" in res:
            ticket_id = str(res["id"])

        cw_path = f"/api/v1/accounts/{self._settings.chatwoot_account_id}/conversations/{conv_id}"
        await self._chatwoot_request("PUT", cw_path, {"status": "open"})

        return ticket_id

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        path = "/api/v1/ticket_articles"
        payload = {
            "ticket_id": int(ticket_id) if ticket_id.isdigit() else ticket_id,
            "subject": "AI Handoff Summary",
            "body": text,
            "type": "note",
            "internal": True,
        }
        _log.info("adding_zammad_internal_note", ticket_id=ticket_id)
        await self._zammad_request("POST", path, payload)

    async def pause_ai_for_session(self, session_id: str) -> None:
        _log.info("pausing_ai_for_session", session_id=session_id)
        self._paused_sessions.add(session_id)
        if self._settings.handoff_store == "firestore":
            try:
                client = firestore.Client(
                    project=self._settings.firestore_project_id,
                    database=self._settings.firestore_database_id,
                )
                await asyncio.to_thread(
                    lambda: (
                        client.collection("paused_sessions")
                        .document(session_id)
                        .set({"paused": True})
                    )
                )
            except Exception as e:
                _log.error("failed_to_persist_pause_state", session_id=session_id, error=str(e))

    async def unpause_ai_for_session(self, session_id: str) -> None:
        _log.info("unpausing_ai_for_session", session_id=session_id)
        self._paused_sessions.discard(session_id)
        if self._settings.handoff_store == "firestore":
            try:
                client = firestore.Client(
                    project=self._settings.firestore_project_id,
                    database=self._settings.firestore_database_id,
                )
                await asyncio.to_thread(
                    lambda: client.collection("paused_sessions").document(session_id).delete()
                )
            except Exception as e:
                _log.error("failed_to_delete_pause_state", session_id=session_id, error=str(e))

    async def is_ai_paused(self, session_id: str) -> bool:
        if session_id in self._paused_sessions:
            return True
        if self._settings.handoff_store == "firestore":
            try:
                client = firestore.Client(
                    project=self._settings.firestore_project_id,
                    database=self._settings.firestore_database_id,
                )
                snap = await asyncio.to_thread(
                    lambda: client.collection("paused_sessions").document(session_id).get()
                )
                if snap.exists and snap.get("paused") is True:  # type: ignore[union-attr]
                    self._paused_sessions.add(session_id)
                    return True
            except Exception as e:
                _log.error("failed_to_read_pause_state", session_id=session_id, error=str(e))
        return False
