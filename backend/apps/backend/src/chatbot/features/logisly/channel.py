"""The Chatwoot calls the Logisly demo makes: opening a driver conversation
(simulator) and filing an escalation ticket (notes, labels, priority)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from chatbot.platform.config import Settings


def _e164(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"+{digits}" if digits else ""


# The demo driver's contact on the driver app's API inbox: its Chatwoot
# `identifier` and its contact_inbox source_id.
APP_DRIVER_IDENTIFIER = "logisly-demo-driver"


class ChatwootDriverChannel:
    """The Chatwoot calls the Logisly demo makes, nothing more."""

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
        created = await self._request(
            "POST", "/contacts", json={"name": name, "phone_number": phone}
        )
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
                    "POST",
                    f"/conversations/{conv['id']}/toggle_status",
                    json={"status": "resolved"},
                )

    async def find_contact_by_identifier(self, identifier: str) -> int | None:
        found = await self._request("GET", "/contacts/search", params={"q": identifier})
        for contact in found.get("payload") or []:
            if contact.get("identifier") == identifier:
                return int(contact["id"])
        return None

    async def open_conversation(
        self, contact_id: int, inbox_id: int, source_id: str, text: str = ""
    ) -> int:
        """New conversation, waiting for the bot. `source_id` is the channel's
        address for the contact: "whatsapp:+62…" on WhatsApp, the contact's
        identifier on the driver app's API inbox. `text`, when given, is posted
        as Logisly's opening message."""
        body: dict[str, Any] = {
            "source_id": source_id,
            "inbox_id": inbox_id,
            "contact_id": contact_id,
            "status": "pending",
        }
        if text:
            body["message"] = {"content": text}
        created = await self._request("POST", "/conversations", json=body)
        return int(created["id"])

    async def latest_open_conversation(self, contact_id: int, inbox_id: int) -> int | None:
        listed = await self._request("GET", f"/contacts/{contact_id}/conversations")
        open_ids = [
            int(c["id"])
            for c in listed.get("payload") or []
            if c.get("inbox_id") == inbox_id and c.get("status") != "resolved"
        ]
        return max(open_ids) if open_ids else None

    async def messages(self, conversation_id: int) -> list[dict[str, Any]]:
        listed = await self._request("GET", f"/conversations/{conversation_id}/messages")
        return list(listed.get("payload") or [])

    async def post_driver_message(
        self, conversation_id: int, text: str, file: tuple[str, bytes, str] | None = None
    ) -> None:
        """Post as the driver (an incoming message; only API inboxes allow it)."""
        data = {"content": text, "message_type": "incoming"}
        if file is None:
            await self._request("POST", f"/conversations/{conversation_id}/messages", json=data)
        else:
            await self._request(
                "POST",
                f"/conversations/{conversation_id}/messages",
                data=data,
                files={"attachments[]": file},
            )

    async def add_private_note(self, conversation_id: int, text: str) -> None:
        await self._request(
            "POST",
            f"/conversations/{conversation_id}/messages",
            json={"content": text, "private": True, "message_type": "outgoing"},
        )

    async def add_labels(self, conversation_id: int, labels: list[str]) -> None:
        current = await self._request("GET", f"/conversations/{conversation_id}/labels")
        merged = sorted(set(current.get("payload") or []) | set(labels))
        await self._request(
            "POST", f"/conversations/{conversation_id}/labels", json={"labels": merged}
        )

    async def set_priority(self, conversation_id: int, priority: str) -> None:
        await self._request(
            "POST", f"/conversations/{conversation_id}/toggle_priority", json={"priority": priority}
        )

    async def set_status(self, conversation_id: int, status: str) -> None:
        await self._request(
            "POST", f"/conversations/{conversation_id}/toggle_status", json={"status": status}
        )
