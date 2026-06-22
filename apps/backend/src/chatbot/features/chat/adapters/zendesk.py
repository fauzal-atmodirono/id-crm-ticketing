from __future__ import annotations

import base64
import urllib.parse
from typing import TYPE_CHECKING

import httpx
import structlog

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.ports import ChatPort, KnowledgePort, TicketingPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class ZendeskAdapter(ChatPort, TicketingPort, KnowledgePort):
    """Production adapter integrating with Zendesk Guide, Support, and Sunshine Conversations APIs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._paused_sessions: set[str] = set()

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

    async def pause_ai_for_session(self, session_id: str) -> None:
        _log.info("pausing_ai_for_zendesk_session", session_id=session_id)
        self._paused_sessions.add(session_id)

    async def unpause_ai_for_session(self, session_id: str) -> None:
        _log.info("unpausing_ai_for_zendesk_session", session_id=session_id)
        self._paused_sessions.discard(session_id)

    async def is_ai_paused(self, session_id: str) -> bool:
        return session_id in self._paused_sessions

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
