"""A small Chatwoot Application API client for the demo seeder, guarded.

Not the shared ``ChatwootAdapter``: that one is fail-open (a failed call
returns ``None`` and the caller carries on), which is right for a live chat
and wrong for a seeder, where an operator needs to see a failure. It also
has no notion of ownership. This client raises ``ChatwootError`` on any
non-2xx, and every write that targets an EXISTING record first re-reads
that record and applies the ``safety`` ownership rules to the fresh copy --
so it cannot write to, or delete, anything this seeder does not own, even
if the caller passes a wrong id:

* a conversation that is not marker-stamped is ours only if it is on the
  demo inbox AND its contact -- re-read fresh by id -- is ours;
* a contact is deleted only if EVERY one of its conversations is ours
  (Chatwoot destroys a contact's conversations with it, on any inbox). The
  contact conversations endpoint returns at most 20 and has no paging, so
  a contact at that cap is refused rather than half-checked.

Creates are constrained too: a contact must have a ``[DEMO] `` name and a
persona number (the ``demo_seed`` marker is stamped here); a conversation
must hang off one of our contacts, on the demo inbox, with a ``demo-``
source id (never ``whatsapp-...``, which the backend relays to WhatsApp);
message text must start with ``[DEMO]``; no ``escalate``/``dealer_*``
label is ever applied.

Deletes need an ADMINISTRATOR Chatwoot token, and Chatwoot deletes
conversations asynchronously (a background job), so a deleted conversation
can still be listed for a moment.

The token is sent in both header spellings, like ``ChatwootAdapter``, and is
never logged or put in an error message.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
import structlog

from chatbot.features.demo.personas import seeded_numbers
from chatbot.features.demo.safety import (
    CREATABLE_LABELS,
    DEMO_SEED_MARKER,
    DEMO_SOURCE_PREFIX,
    DemoSafetyError,
    assert_demo_text,
    conversation_labels,
    conversation_sender,
    forbidden_labels,
    has_demo_name,
    has_seeder_markers,
    is_seeder_contact,
    is_seeder_conversation,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_MAX_PAGES = 5
_STATUSES = frozenset({"open", "resolved", "pending", "snoozed"})
# Chatwoot's GET /contacts/{id}/conversations returns at most this many.
CONTACT_CONVERSATIONS_CAP = 20


class ChatwootError(RuntimeError):
    """Chatwoot answered with an error (or could not be reached)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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
        numbers: Iterable[str] | None = None,
    ) -> None:
        token = settings.chatwoot_api_token
        self.inbox_id = int(settings.demo_inbox_id or settings.chatwoot_inbox_id or 0)
        self.numbers = tuple(numbers) if numbers is not None else seeded_numbers()
        # Conversations this client deleted. Chatwoot deletes them in a
        # background job, so they can still be LISTED for a while; every
        # listing here leaves them out (so a reseed right after a reset does
        # not mistake the just-deleted history for existing history).
        self.deleted_conversation_ids: set[int] = set()
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
        where = path.split("?", maxsplit=1)[0]
        try:
            res = await self._client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise ChatwootError(f"{method} {where}: {type(exc).__name__}") from exc
        if res.status_code >= 400:  # noqa: PLR2004
            raise ChatwootError(f"{method} {where} -> HTTP {res.status_code}", res.status_code)
        return res.json() if res.content else {}

    # --- ownership (pure, on records already in hand) ---------------------------

    def owns_contact(self, contact: Any) -> bool:
        return is_seeder_contact(contact, self.numbers)

    def owns_conversation(self, conv: Any, sender: dict[str, Any] | None = None) -> bool:
        return is_seeder_conversation(
            conv, inbox_id=self.inbox_id, numbers=self.numbers, sender=sender
        )

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

    def _not_deleted(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if r.get("id") not in self.deleted_conversation_ids]

    async def contact_conversations(self, contact_id: int) -> list[dict[str, Any]]:
        return self._not_deleted(
            _payload_list(await self._call("GET", f"/contacts/{contact_id}/conversations"))
        )

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
        return self._not_deleted(out)

    # --- guarded fresh reads ------------------------------------------------------

    async def _owned_contact(self, contact_id: int) -> dict[str, Any]:
        contact = await self.get_contact(contact_id)
        if contact is None or not self.owns_contact(contact):
            raise DemoSafetyError(
                f"contact {contact_id} is not a contact this seeder created "
                "([DEMO] name + demo_seed=demo_persona + persona number); refusing to touch it"
            )
        return contact

    async def _owned_conversation(self, conversation_id: int) -> dict[str, Any]:
        conv = await self.get_conversation(conversation_id)
        if conv is not None and not has_seeder_markers(conv):
            # Unmarked (e.g. a rehearsal call): decide on a FRESH read of
            # its contact, not the copy embedded in the conversation.
            sender = conversation_sender(conv)
            sender_id = sender.get("id") if sender else None
            fresh = await self.get_contact(int(sender_id)) if sender_id is not None else None
            if self.owns_conversation(conv, sender=fresh or {}):
                return conv
        elif conv is not None:
            return conv
        raise DemoSafetyError(
            f"conversation {conversation_id} is not one this seeder owns; refusing to touch it"
        )

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
        attrs = {**custom_attributes, "demo_seed": DEMO_SEED_MARKER}
        candidate = {"name": name, "phone_number": phone_number, "custom_attributes": attrs}
        if not has_demo_name(candidate) or not self.owns_contact(candidate):
            raise DemoSafetyError(
                "a demo contact needs a '[DEMO] ' name and one of the persona numbers"
            )
        data = await self._call(
            "POST",
            "/contacts",
            {
                "inbox_id": inbox_id,
                "name": name,
                "phone_number": phone_number,
                "custom_attributes": attrs,
            },
        )
        contact = _unwrap_contact(data)
        if contact is None:
            raise ChatwootError("POST /contacts returned no contact id")
        return contact

    async def update_contact_attributes(
        self, contact_id: int, custom_attributes: dict[str, Any]
    ) -> None:
        if custom_attributes.get("demo_seed", DEMO_SEED_MARKER) != DEMO_SEED_MARKER:
            raise DemoSafetyError("the demo_seed marker cannot be changed")
        await self._owned_contact(contact_id)
        # PUT /contacts MERGES custom_attributes (unlike conversations).
        await self._call("PUT", f"/contacts/{contact_id}", {"custom_attributes": custom_attributes})

    async def delete_contact(self, contact_id: int) -> None:
        """Delete one of our contacts -- only when every conversation it has
        is ours too (the delete takes them all, on every inbox)."""
        await self._owned_contact(contact_id)
        convs = await self.contact_conversations(contact_id)
        # The cap counts what Chatwoot listed, async-deleted ones included.
        if len(convs) >= CONTACT_CONVERSATIONS_CAP:
            raise DemoSafetyError(
                f"contact {contact_id} has {len(convs)}+ conversations, more than can be "
                "verified; not deleting it"
            )
        for conv in convs:
            if conv.get("id") is None:
                raise DemoSafetyError(f"contact {contact_id} has an unreadable conversation")
            try:
                await self._owned_conversation(int(conv["id"]))
            except ChatwootError as exc:
                if exc.status == 404:  # noqa: PLR2004 -- deleted (async job), already gone
                    continue
                raise
            except DemoSafetyError as exc:
                raise DemoSafetyError(
                    f"contact {contact_id} has conversation {conv['id']} this seeder does not "
                    "own; not deleting the contact"
                ) from exc
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
        if not self.inbox_id or inbox_id != self.inbox_id:
            raise DemoSafetyError(f"demo conversations go to the demo inbox {self.inbox_id} only")
        await self._owned_contact(contact_id)
        data = await self._call(
            "POST",
            "/conversations",
            {
                "source_id": source_id,
                "inbox_id": inbox_id,
                "contact_id": contact_id,
                "custom_attributes": {**custom_attributes, "demo_seed": DEMO_SEED_MARKER},
                "additional_attributes": additional_attributes or {},
            },
        )
        if not isinstance(data, dict) or data.get("id") is None:
            raise ChatwootError("POST /conversations returned no conversation id")
        return data

    async def add_labels(self, conversation_id: int, labels: list[str]) -> list[str]:
        """Union ``labels`` into the conversation's set (the endpoint REPLACES
        the set, so the current labels are read and kept)."""
        bad = forbidden_labels(labels)
        if bad:
            raise DemoSafetyError(f"the seeder never applies {bad}")
        conv = await self._owned_conversation(conversation_id)
        union = list(dict.fromkeys([*conversation_labels(conv), *labels]))
        await self._call("POST", f"/conversations/{conversation_id}/labels", {"labels": union})
        return union

    async def set_status(self, conversation_id: int, status: str) -> None:
        if status not in _STATUSES:
            raise ValueError(f"unknown conversation status {status!r}")
        await self._owned_conversation(conversation_id)
        await self._call(
            "POST", f"/conversations/{conversation_id}/toggle_status", {"status": status}
        )

    async def post_incoming_message(self, conversation_id: int, text: str) -> None:
        assert_demo_text(text)
        await self._owned_conversation(conversation_id)
        await self._call(
            "POST",
            f"/conversations/{conversation_id}/messages",
            {"content": text, "message_type": "incoming", "private": False},
        )

    async def delete_conversation(self, conversation_id: int) -> None:
        await self._owned_conversation(conversation_id)
        await self._call("DELETE", f"/conversations/{conversation_id}")
        self.deleted_conversation_ids.add(conversation_id)


__all__ = ["CONTACT_CONVERSATIONS_CAP", "ChatwootError", "DemoCrmClient"]
