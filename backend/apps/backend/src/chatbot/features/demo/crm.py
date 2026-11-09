"""A small Chatwoot Application API client for the demo seeder, guarded.

Not the shared ``ChatwootAdapter``: that one is fail-open (a failed call
returns ``None`` and the caller carries on), which is right for a live chat
and wrong for a seeder, where an operator needs to see a failure. It also
has no notion of "demo record". This client raises ``ChatwootError`` on any
non-2xx, and every write that targets an EXISTING record first re-reads
that record and runs a ``safety.assert_*`` check on the fresh copy -- so the
seeder cannot write to, or delete, anything that is not a demo record, even
if the caller passes a wrong id. Creates are constrained too: a contact name
must start with ``[DEMO] ``, a conversation must hang off a ``[DEMO]``
contact with a ``demo-`` source id (never ``whatsapp-...``, which the
backend would relay to WhatsApp), and message text must start with
``[DEMO]``.

The token is sent in both header spellings, like ``ChatwootAdapter``, and is
never logged or put in an error message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
import structlog

from chatbot.features.demo.safety import (
    CREATABLE_LABELS,
    DEMO_LABEL,
    DEMO_SOURCE_PREFIX,
    FORBIDDEN_SEED_LABELS,
    DemoSafetyError,
    assert_demo_contact,
    assert_demo_conversation,
    assert_demo_text,
    conversation_labels,
    is_demo_contact,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_MAX_PAGES = 5
_STATUSES = frozenset({"open", "resolved", "pending", "snoozed"})


class ChatwootError(RuntimeError):
    """Chatwoot answered with an error (or could not be reached)."""


def _payload_list(data: Any) -> list[dict[str, Any]]:
    payload = data.get("payload") if isinstance(data, dict) else data
    if isinstance(payload, dict):  # some list endpoints nest under payload.data
        payload = payload.get("data") or payload.get("payload")
    return [x for x in (payload or []) if isinstance(x, dict)] if isinstance(payload, list) else []


def _unwrap_contact(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    payload = data.get("payload", data)
    if isinstance(payload, dict) and isinstance(payload.get("contact"), dict):
        payload = payload["contact"]
    return payload if isinstance(payload, dict) and payload.get("id") is not None else None


class DemoCrmClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        token = settings.chatwoot_api_token
        self._client = httpx.AsyncClient(
            base_url=(
                f"{settings.chatwoot_api_url.rstrip('/')}/api/v1/accounts/{settings.chatwoot_account_id}"
            ),
            headers={"api_access_token": token, "Api-Access-Token": token},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            res = await self._client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise ChatwootError(f"{method} {path.split('?', maxsplit=1)[0]}: {type(exc).__name__}") from exc
        if res.status_code >= 400:  # noqa: PLR2004
            raise ChatwootError(f"{method} {path.split('?', maxsplit=1)[0]} -> HTTP {res.status_code}")
        return res.json() if res.content else {}

    # --- reads -----------------------------------------------------------------

    async def get_inbox(self, inbox_id: int) -> dict[str, Any]:
        data = await self._call("GET", f"/inboxes/{inbox_id}")
        return data if isinstance(data, dict) else {}

    async def list_label_titles(self) -> set[str]:
        return {str(x.get("title")) for x in _payload_list(await self._call("GET", "/labels"))}

    async def search_contacts(self, query: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for page in range(1, _MAX_PAGES + 1):
            rows = _payload_list(
                await self._call("GET", f"/contacts/search?q={quote(query, safe='')}&page={page}")
            )
            fresh = [r for r in rows if r.get("id") not in seen]
            if not fresh:
                break
            seen.update(r.get("id") for r in fresh)
            out.extend(fresh)
        return out

    async def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        return _unwrap_contact(await self._call("GET", f"/contacts/{contact_id}"))

    async def contact_conversations(self, contact_id: int) -> list[dict[str, Any]]:
        return _payload_list(await self._call("GET", f"/contacts/{contact_id}/conversations"))

    async def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        data = await self._call("GET", f"/conversations/{conversation_id}")
        return data if isinstance(data, dict) and data.get("id") is not None else None

    async def conversations_with_label(self, label: str) -> list[dict[str, Any]]:
        body = {
            "payload": [
                {
                    "attribute_key": "labels",
                    "filter_operator": "equal_to",
                    "values": [label],
                    "query_operator": None,
                }
            ]
        }
        out: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for page in range(1, _MAX_PAGES + 1):
            rows = _payload_list(
                await self._call("POST", f"/conversations/filter?page={page}", body)
            )
            fresh = [r for r in rows if r.get("id") not in seen]
            if not fresh:
                break
            seen.update(r.get("id") for r in fresh)
            out.extend(fresh)
        return out

    # --- guarded fresh reads ------------------------------------------------------

    async def _demo_contact(self, contact_id: int) -> dict[str, Any]:
        contact = await self.get_contact(contact_id)
        assert_demo_contact(contact, contact_id)
        return contact  # type: ignore[return-value]  # assert_demo_contact rejects None

    async def _demo_conversation(self, conversation_id: int) -> dict[str, Any]:
        conv = await self.get_conversation(conversation_id)
        assert_demo_conversation(conv, conversation_id)
        return conv  # type: ignore[return-value]  # assert_demo_conversation rejects None

    # --- guarded writes ------------------------------------------------------------

    async def create_label(self, title: str) -> None:
        if title not in CREATABLE_LABELS:
            raise DemoSafetyError(
                f"the seeder may only create the {sorted(CREATABLE_LABELS)} labels"
            )
        await self._call(
            "POST",
            "/labels",
            {
                "title": title,
                "description": "[DEMO] test console demo data",
                "show_on_sidebar": False,
            },
        )

    async def create_contact(
        self, name: str, phone_number: str, custom_attributes: dict[str, Any], *, inbox_id: int
    ) -> dict[str, Any]:
        if not is_demo_contact({"name": name}):
            raise DemoSafetyError("a demo contact name must start with '[DEMO] '")
        data = await self._call(
            "POST",
            "/contacts",
            {
                "inbox_id": inbox_id,
                "name": name,
                "phone_number": phone_number,
                "custom_attributes": custom_attributes,
            },
        )
        contact = _unwrap_contact(data)
        if contact is None:
            raise ChatwootError("POST /contacts returned no contact id")
        return contact

    async def update_contact_attributes(
        self, contact_id: int, custom_attributes: dict[str, Any]
    ) -> None:
        await self._demo_contact(contact_id)
        # PUT /contacts MERGES custom_attributes (unlike conversations).
        await self._call("PUT", f"/contacts/{contact_id}", {"custom_attributes": custom_attributes})

    async def delete_contact(self, contact_id: int) -> None:
        await self._demo_contact(contact_id)
        await self._call("DELETE", f"/contacts/{contact_id}")

    async def create_conversation(
        self,
        contact_id: int,
        *,
        inbox_id: int,
        source_id: str,
        custom_attributes: dict[str, Any],
        additional_attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not source_id.startswith(DEMO_SOURCE_PREFIX):
            raise DemoSafetyError(
                f"a demo conversation source id must start with {DEMO_SOURCE_PREFIX!r}"
            )
        await self._demo_contact(contact_id)
        data = await self._call(
            "POST",
            "/conversations",
            {
                "source_id": source_id,
                "inbox_id": inbox_id,
                "contact_id": contact_id,
                "custom_attributes": custom_attributes,
                "additional_attributes": additional_attributes or {},
            },
        )
        if not isinstance(data, dict) or data.get("id") is None:
            raise ChatwootError("POST /conversations returned no conversation id")
        return data

    async def add_labels(self, conversation_id: int, labels: list[str]) -> list[str]:
        """Union ``labels`` into the conversation's set (the endpoint REPLACES
        the set, so the current labels are read and kept)."""
        bad = FORBIDDEN_SEED_LABELS.intersection(labels)
        if bad:
            raise DemoSafetyError(f"the seeder never applies {sorted(bad)}")
        conv = await self._demo_conversation(conversation_id)
        union = list(dict.fromkeys([*conversation_labels(conv), *labels]))
        await self._call("POST", f"/conversations/{conversation_id}/labels", {"labels": union})
        return union

    async def set_status(self, conversation_id: int, status: str) -> None:
        if status not in _STATUSES:
            raise ValueError(f"unknown conversation status {status!r}")
        await self._demo_conversation(conversation_id)
        await self._call(
            "POST", f"/conversations/{conversation_id}/toggle_status", {"status": status}
        )

    async def post_incoming_message(self, conversation_id: int, text: str) -> None:
        assert_demo_text(text)
        await self._demo_conversation(conversation_id)
        await self._call(
            "POST",
            f"/conversations/{conversation_id}/messages",
            {"content": text, "message_type": "incoming", "private": False},
        )

    async def delete_conversation(self, conversation_id: int) -> None:
        await self._demo_conversation(conversation_id)
        await self._call("DELETE", f"/conversations/{conversation_id}")


__all__ = ["DEMO_LABEL", "ChatwootError", "DemoCrmClient"]
