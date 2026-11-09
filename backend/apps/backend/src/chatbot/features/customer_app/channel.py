"""The Chatwoot calls the demo customer app makes, nothing more.

Ported from the Logisly driver app's channel (logisly-demo branch,
`features/logisly/channel.py`). Separate from `ChatwootAdapter` because the
adapter's `_request` is JSON-only and swallows errors, while posting a photo
or voice note needs multipart and the page needs a real error (502) to show.
Errors propagate as `httpx.HTTPError`; the router maps them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from chatbot.features.chat.customer360_router import _phones_match

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_TIMEOUT_SECONDS = 15.0


class CustomerAppChannel:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base = f"{settings.chatwoot_api_url.rstrip('/')}/api/v1/accounts/{settings.chatwoot_account_id}"
        token = settings.chatwoot_api_token
        # Both header spellings: a proxy that strips underscore headers still
        # passes the dash form (same as ChatwootAdapter).
        self._headers = {"api_access_token": token, "Api-Access-Token": token}
        self._client = client

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        if self._client is not None:
            res = await self._client.request(method, self._base + path, headers=self._headers, **kw)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                res = await client.request(method, self._base + path, headers=self._headers, **kw)
        res.raise_for_status()
        return res.json() if res.content else {}

    async def find_contact_by_phone(self, phone: str) -> dict[str, Any] | None:
        """The contact whose phone matches (digits-only, country-code
        tolerant, the Customer 360 rule), or None."""
        found = await self._request("GET", "/contacts/search", params={"q": phone})
        for contact in found.get("payload") or []:
            if contact.get("id") is not None and _phones_match(phone, contact.get("phone_number")):
                return dict(contact)
        return None

    async def create_contact(
        self, name: str, phone: str, custom_attributes: dict[str, Any] | None = None
    ) -> int:
        body: dict[str, Any] = {"name": name, "phone_number": phone}
        if custom_attributes:
            body["custom_attributes"] = custom_attributes
        created = await self._request("POST", "/contacts", json=body)
        payload = created.get("payload") or created
        contact = payload.get("contact") or payload
        return int(contact["id"])

    async def latest_open_conversation(self, contact_id: int, inbox_id: int) -> int | None:
        listed = await self._request("GET", f"/contacts/{contact_id}/conversations")
        open_ids = [
            int(c["id"])
            for c in listed.get("payload") or []
            if c.get("inbox_id") == inbox_id and c.get("status") != "resolved"
        ]
        return max(open_ids) if open_ids else None

    async def open_conversation(self, contact_id: int, inbox_id: int, source_id: str) -> int:
        """New conversation waiting for the bot (status pending). `source_id`
        is the contact's address on the API inbox; Chatwoot builds the
        contact_inbox for it."""
        created = await self._request(
            "POST",
            "/conversations",
            json={
                "source_id": source_id,
                "inbox_id": inbox_id,
                "contact_id": contact_id,
                "status": "pending",
            },
        )
        return int(created["id"])

    async def messages(self, conversation_id: int) -> list[dict[str, Any]]:
        listed = await self._request("GET", f"/conversations/{conversation_id}/messages")
        return list(listed.get("payload") or [])

    async def post_incoming(
        self, conversation_id: int, text: str, file: tuple[str, bytes, str] | None = None
    ) -> None:
        """Post as the customer (an incoming message; only API inboxes allow it)."""
        data = {"content": text, "message_type": "incoming"}
        path = f"/conversations/{conversation_id}/messages"
        if file is None:
            await self._request("POST", path, json=data)
        else:
            await self._request("POST", path, data=data, files={"attachments[]": file})
