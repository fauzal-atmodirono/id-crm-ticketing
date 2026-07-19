"""Read-only Chatwoot lookups used as Copilot tools.

Deliberately separate from ChatwootAdapter (which owns write/escalation flows):
this client only reads, never raises, and returns empty structures on failure or
when Chatwoot is disabled — so a tool call can never crash a Copilot turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_MESSAGE_TYPE_CUSTOMER = 0
_MESSAGE_TYPE_AGENT = 1
_MESSAGE_TYPE_ACTIVITY = 2


class ChatwootContextClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        s = self._settings
        return f"{s.chatwoot_api_url}/api/v1/accounts/{s.chatwoot_account_id}"

    async def _get(self, path: str) -> Any:
        if not self._settings.chatwoot_enabled:
            return None
        token = self._settings.chatwoot_api_token
        headers = {"api_access_token": token, "Api-Access-Token": token}
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self._base()}{path}", headers=headers, timeout=10.0
                )
                res.raise_for_status()
                return res.json() if res.content else None
        except Exception as e:  # noqa: BLE001 — never raise into a tool call
            _log.error("chatwoot_context_get_failed", path=path, error=str(e))
            return None

    async def _sender_id(self, conversation_id: str) -> int | None:
        data = await self._get(f"/conversations/{conversation_id}")
        if not data:
            return None
        return (data.get("meta") or {}).get("sender", {}).get("id")

    async def get_transcript(self, conversation_id: str) -> list[dict[str, Any]]:
        data = await self._get(f"/conversations/{conversation_id}")
        if not data:
            return []
        turns: list[dict[str, Any]] = []
        for m in data.get("messages", []):
            mtype = m.get("message_type")
            if mtype == _MESSAGE_TYPE_ACTIVITY:
                continue
            content = (m.get("content") or "").strip()
            if not content:
                continue
            turns.append(
                {
                    "sender": "customer" if mtype == _MESSAGE_TYPE_CUSTOMER else "agent",
                    "private": bool(m.get("private")),
                    "content": content,
                }
            )
        return turns

    async def get_contact(self, conversation_id: str) -> dict[str, Any]:
        sender_id = await self._sender_id(conversation_id)
        if sender_id is None:
            return {}
        data = await self._get(f"/contacts/{sender_id}")
        p = (data or {}).get("payload") or {}
        if not p:
            return {}
        return {
            "id": p.get("id"),
            "name": p.get("name"),
            "email": p.get("email"),
            "phone": p.get("phone_number"),
            "custom_attributes": p.get("custom_attributes") or {},
        }

    async def list_contact_conversations(
        self, conversation_id: str
    ) -> list[dict[str, Any]]:
        sender_id = await self._sender_id(conversation_id)
        if sender_id is None:
            return []
        data = await self._get(f"/contacts/{sender_id}/conversations")
        payload = (data or {}).get("payload") or []
        current = str(conversation_id)
        out: list[dict[str, Any]] = []
        for c in payload:
            if str(c.get("id")) == current:
                continue
            out.append(
                {
                    "id": c.get("id"),
                    "status": c.get("status"),
                    "last_activity_at": c.get("last_activity_at"),
                    "labels": c.get("labels") or [],
                }
            )
        return out
