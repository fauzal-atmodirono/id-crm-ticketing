from __future__ import annotations

import asyncio
import base64
import urllib.parse
from typing import TYPE_CHECKING

import httpx
import structlog
from google.cloud import firestore

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    ConversationLogResult,
    KnowledgePort,
    TicketingPort,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class ZendeskAdapter(ChatPort, TicketingPort, KnowledgePort, ConversationLogPort):
    """Production adapter integrating with Zendesk Guide, Support, and Sunshine Conversations APIs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._paused_sessions: set[str] = set()
        self._conv_tickets: dict[str, str] = {}

    def _sunshine_headers(self) -> dict[str, str]:
        auth_str = f"{self._settings.zendesk_key_id}:{self._settings.zendesk_secret_key}"
        encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        return {
            "Content-Type": "application/json",
            "Authorization": f"Basic {encoded}",
        }

    def _support_headers(self) -> dict[str, str]:
        auth_str = f"{self._settings.zendesk_email}/token:{self._settings.zendesk_api_token}"
        encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        return {
            "Content-Type": "application/json",
            "Authorization": f"Basic {encoded}",
        }

    # --- ChatPort Implementation (Zendesk Messaging via Sunshine Conversations) ---
    async def send_message(self, conversation_id: str, text: str) -> None:
        app_id = self._settings.zendesk_app_id
        url = f"https://api.smooch.io/v2/apps/{app_id}/conversations/{conversation_id}/messages"
        payload = {
            "author": {
                "role": "app",
            },
            "content": {
                "type": "text",
                "text": text,
            },
        }

        _log.info("sending_zendesk_messaging_reply", conversation_id=conversation_id)
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    url, json=payload, headers=self._sunshine_headers(), timeout=10.0
                )
                res.raise_for_status()
        except Exception as e:
            _log.error(
                "zendesk_messaging_reply_failed", conversation_id=conversation_id, error=str(e)
            )

    # --- TicketingPort Implementation (Zendesk Support) ---
    async def create_ticket(
        self,
        session_id: str,
        title: str,
        body: str,
        urgency: str,
        customer_name: str | None = None,
        customer_email: str | None = None,
        customer_phone: str | None = None,
    ) -> str:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets.json"

        priority_map = {"low": "low", "medium": "normal", "high": "high", "urgent": "urgent"}
        priority = priority_map.get(urgency.lower(), "normal")

        # Attribute the ticket to a session-scoped pseudo-user so it isn't
        # filed under the API token owner. Zendesk auto-creates an end-user
        # for the email on first sight.
        requester_email = customer_email or f"{session_id}@proton.devoteam.example"
        requester_name = customer_name or f"Proton AI Customer ({session_id})"

        requester: dict[str, str] = {
            "name": requester_name,
            "email": requester_email,
        }
        if customer_phone:
            requester["phone"] = customer_phone

        payload = {
            "ticket": {
                "subject": f"[Escalated] {title}",
                "comment": {
                    "body": body,
                    "public": True,
                },
                "priority": priority,
                "external_id": session_id,
                "requester": requester,
            }
        }

        _log.info("creating_zendesk_support_ticket", session_id=session_id)
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    url, json=payload, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
                data = res.json()
                return str(data.get("ticket", {}).get("id", "MOCK-ZEN-TKT"))
        except Exception as e:
            _log.error("zendesk_ticket_creation_failed", error=str(e))
            return "MOCK-ZEN-TKT"

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"

        payload = {
            "ticket": {
                "comment": {
                    "body": text,
                    "public": False,
                }
            }
        }

        _log.info("adding_zendesk_private_note", ticket_id=ticket_id)
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url, json=payload, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
        except Exception as e:
            _log.error("zendesk_add_private_note_failed", ticket_id=ticket_id, error=str(e))

    # --- ConversationLogPort (per-conversation capture) ---
    async def _create_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets.json"
        requester: dict[str, str] = {
            "name": customer_name or f"Proton AI Customer ({session_id})",
            "email": f"{session_id}@proton.devoteam.example",
        }
        if customer_phone:
            requester["phone"] = customer_phone
        payload = {
            "ticket": {
                "subject": subject,
                "external_id": session_id,
                "requester": requester,
                "comment": {"body": "Conversation started.", "public": False},
            }
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    url, json=payload, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
                return str(res.json().get("ticket", {}).get("id", "MOCK-ZEN-TKT"))
        except Exception as e:
            _log.error("zendesk_ensure_conversation_ticket_failed", error=str(e))
            return "MOCK-ZEN-TKT"

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        cached = self._conv_tickets.get(session_id)
        if cached:
            return cached
        ticket_id = await self._create_conversation_ticket(
            session_id, subject, customer_name, customer_phone
        )
        self._conv_tickets[session_id] = ticket_id
        return ticket_id

    async def rotate_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        # Always create a new ticket (the prior one is closed) and replace the
        # cached mapping so subsequent ensure()s use the fresh ticket.
        ticket_id = await self._create_conversation_ticket(
            session_id, subject, customer_name, customer_phone
        )
        _log.info(
            "zendesk_rotated_conversation_ticket",
            session_id=session_id,
            old_ticket_id=self._conv_tickets.get(session_id),
            new_ticket_id=ticket_id,
        )
        self._conv_tickets[session_id] = ticket_id
        return ticket_id

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"
        ticket: dict[str, object] = {"comment": {"body": text, "public": False}}
        if status:
            ticket["status"] = status
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url, json={"ticket": ticket}, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
            return ConversationLogResult.OK
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            # Log Zendesk's response body — the actual error code lives there, not
            # in httpx's generic message.
            _log.error(
                "zendesk_append_conversation_comment_failed",
                ticket_id=ticket_id,
                status_code=code,
                error=str(e),
                zendesk_response=e.response.text,
            )
            # A closed ticket (422 "closed") or a deleted one (404) can never take
            # a comment again → signal the caller to rotate to a fresh ticket.
            is_closed = code == httpx.codes.NOT_FOUND or (
                code == httpx.codes.UNPROCESSABLE_ENTITY and "closed" in e.response.text.lower()
            )
            if is_closed:
                return ConversationLogResult.TICKET_CLOSED
            return ConversationLogResult.FAILED
        except Exception as e:
            _log.error(
                "zendesk_append_conversation_comment_failed", ticket_id=ticket_id, error=str(e)
            )
            return ConversationLogResult.FAILED

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}/tags.json"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url, json={"tags": [tag]}, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
        except Exception as e:
            _log.error("zendesk_add_ticket_tag_failed", ticket_id=ticket_id, error=str(e))

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"
        ticket: dict[str, object] = {"comment": {"body": text, "public": True}}
        if status:
            ticket["status"] = status
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url, json={"ticket": ticket}, headers=self._support_headers(), timeout=10.0
                )
                res.raise_for_status()
        except Exception as e:
            _log.error("zendesk_post_public_reply_failed", ticket_id=ticket_id, error=str(e))

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        subdomain = self._settings.zendesk_subdomain
        url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.put(
                    url,
                    json={"ticket": {"external_id": external_id}},
                    headers=self._support_headers(),
                    timeout=10.0,
                )
                res.raise_for_status()
        except Exception as e:
            _log.error(
                "zendesk_set_ticket_external_id_failed",
                ticket_id=ticket_id,
                error=str(e),
            )

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        subdomain = self._settings.zendesk_subdomain
        base = f"https://{subdomain}.zendesk.com/api/v2"
        try:
            async with httpx.AsyncClient() as client:
                t = await client.get(
                    f"{base}/tickets/{ticket_id}.json?include=users",
                    headers=self._support_headers(),
                    timeout=10.0,
                )
                t.raise_for_status()
                tdata = t.json()
                requester_id = tdata.get("ticket", {}).get("requester_id")
                name: str | None = None
                email: str | None = None
                for u in tdata.get("users", []) or []:
                    if u.get("id") == requester_id:
                        name = u.get("name")
                        email = u.get("email")
                        break
                c = await client.get(
                    f"{base}/tickets/{ticket_id}/comments.json",
                    headers=self._support_headers(),
                    timeout=10.0,
                )
                c.raise_for_status()
                body = ""
                for cm in c.json().get("comments", []) or []:
                    if cm.get("public") and cm.get("author_id") == requester_id:
                        body = (
                            cm.get("body") or body
                        )  # comments are chronological; keep the latest match
                return (body, name, email)
        except Exception as e:
            _log.error(
                "zendesk_get_latest_public_comment_failed",
                ticket_id=ticket_id,
                error=str(e),
            )
            return ("", None, None)

    async def pause_ai_for_session(self, session_id: str) -> None:
        _log.info("pausing_ai_for_zendesk_session", session_id=session_id)
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
        _log.info("unpausing_ai_for_zendesk_session", session_id=session_id)
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

    # --- KnowledgePort Implementation (Zendesk Guide) ---
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        subdomain = self._settings.zendesk_subdomain
        encoded_query = urllib.parse.quote(query)
        url = f"https://{subdomain}.zendesk.com/api/v2/help_center/articles/search.json?query={encoded_query}&per_page={limit}"

        _log.info("searching_zendesk_guide", query=query)
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    url, headers={"Content-Type": "application/json"}, timeout=10.0
                )
                res.raise_for_status()
                data = res.json()
                results = data.get("results") or []

                articles = []
                for res_item in results:
                    title = res_item.get("title") or ""
                    body = res_item.get("body") or ""
                    html_url = res_item.get("html_url")

                    articles.append(KbArticle(title=title, content=body, url=html_url))
                return articles
        except Exception as e:
            _log.error("zendesk_guide_search_failed", query=query, error=str(e))
            return []
