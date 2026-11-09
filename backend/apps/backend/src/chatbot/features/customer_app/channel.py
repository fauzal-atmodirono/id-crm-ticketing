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
import structlog

from chatbot.features.chat.customer360_router import _phones_match

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 15.0

# Marks the conversations this app opened (additional_attributes), per persona.
APP_MARKER = "demo_customer_app"
# Same markers as the demo console (features/demo/safety.py on demo-console).
DEMO_SEED_KEY = "demo_seed"
DEMO_SEED_VALUE = "demo_persona"
DEMO_LABELS = ("demo_persona", "audit_test")


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
        # demo_seed marks the contact as demo data the demo console may reset.
        body: dict[str, Any] = {
            "name": name,
            "phone_number": phone,
            "custom_attributes": {**(custom_attributes or {}), DEMO_SEED_KEY: DEMO_SEED_VALUE},
        }
        created = await self._request("POST", "/contacts", json=body)
        payload = created.get("payload") or created
        contact = payload.get("contact") or payload
        return int(contact["id"])

    async def latest_own_conversation(
        self, contact_id: int, inbox_id: int, persona_id: str
    ) -> dict[str, Any] | None:
        """The newest unresolved conversation THIS APP opened for the persona
        (marker ``additional_attributes.demo_customer_app == persona_id``,
        stamped by ``open_conversation``). A seeded demo conversation, a phone
        call or any other conversation of the contact is never returned, so the
        app never posts into a case it did not start. Chatwoot's conversation
        JSON carries no source_id, hence the attribute marker."""
        listed = await self._request("GET", f"/contacts/{contact_id}/conversations")
        own = [
            c
            for c in listed.get("payload") or []
            if c.get("inbox_id") == inbox_id
            and c.get("status") != "resolved"
            and (c.get("additional_attributes") or {}).get(APP_MARKER) == persona_id
        ]
        return max(own, key=lambda c: int(c["id"])) if own else None

    async def open_conversation(self, contact_id: int, inbox_id: int, persona_id: str) -> int:
        """New conversation waiting for the bot (status pending), marked as
        this app's and as demo data: ``demo_seed`` custom attribute and the
        ``demo_persona`` + ``audit_test`` labels, so the demo console's reset
        rule and METRICS_EXCLUDE_DEMO_SEED cover it. The labels endpoint
        replaces the set, which is safe on a conversation created just now."""
        created = await self._request(
            "POST",
            "/conversations",
            json={
                "source_id": f"demo-customer-app-{persona_id}",
                "inbox_id": inbox_id,
                "contact_id": contact_id,
                "status": "pending",
                "custom_attributes": {DEMO_SEED_KEY: DEMO_SEED_VALUE},
                "additional_attributes": {APP_MARKER: persona_id},
            },
        )
        conversation_id = int(created["id"])
        try:
            await self._request(
                "POST",
                f"/conversations/{conversation_id}/labels",
                json={"labels": list(DEMO_LABELS)},
            )
        except httpx.HTTPError as exc:
            # The marker attribute is already on it, so the next post finds
            # this conversation and ensure_demo_labels retries.
            _log.warning(
                "customer_app_labels_failed",
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )
        return conversation_id

    async def ensure_demo_labels(self, conversation: dict[str, Any]) -> None:
        """Re-apply demo_persona + audit_test when missing (a labels POST that
        failed at create). Merges with the current set (the endpoint replaces
        it); never raises -- a missing label must not lose the message."""
        listed = conversation.get("labels")
        if isinstance(listed, list) and set(DEMO_LABELS) <= {str(x) for x in listed}:
            return
        conversation_id = int(conversation["id"])
        try:
            current = await self._request("GET", f"/conversations/{conversation_id}/labels")
            labels = [str(x) for x in (current.get("payload") or [])]
            if set(DEMO_LABELS) <= set(labels):
                return
            merged = list(dict.fromkeys([*labels, *DEMO_LABELS]))
            await self._request(
                "POST", f"/conversations/{conversation_id}/labels", json={"labels": merged}
            )
        except httpx.HTTPError as exc:
            _log.warning(
                "customer_app_labels_retry_failed",
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )

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
