from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Zammad exposes priority as a numeric id (1=low, 2=normal, 3=high). We map the
# AI urgency vocabulary onto it; "urgent" has no distinct id so it shares "high".
_PRIORITY_ID_MAP = {"low": 1, "medium": 2, "high": 3, "urgent": 3}


@dataclass(frozen=True)
class ZammadTicket:
    """Identifiers for a Zammad ticket the backend just created.

    ``number`` is the human-facing ticket number (e.g. "12034") surfaced to
    agents; ``id`` is the internal record id used to build the agent zoom URL
    (``/#ticket/zoom/<id>``).
    """

    number: str
    id: str


class ZammadClient:
    """Thin async client that owns direct Zammad REST ticketing.

    Single responsibility: turn a complaint escalation into a Zammad ticket (and
    internal note) against the dedicated Chatwoot+Zammad instance, so the flow is
    self-contained and does not depend on the CTO's external agent-service sync.

    Guarded by ``zammad_enabled``: when disabled every call is a no-op returning
    ``None``. All HTTP errors are caught and logged — a Zammad failure must never
    break the customer turn, so the caller keeps going with ``None`` in hand.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _request(
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

    async def create_ticket(
        self,
        title: str,
        body: str,
        customer_email: str,
        priority: str,
        group: str | None = None,
    ) -> ZammadTicket | None:
        """Create a Zammad ticket and return its ``ZammadTicket`` (number + id).

        Posts to ``POST /api/v1/tickets`` with the "Users" group (overridable),
        the synthesized customer email, a mapped ``priority_id``, state ``new``,
        and a first public article carrying the summary + transcript. Returns
        ``None`` (never raises) when Zammad is disabled or the request fails.
        """
        payload = {
            "title": title,
            "group": group or self._settings.zammad_group,
            # `guess:<email>` tells Zammad to look up the customer by email and
            # auto-create the user if absent. A bare `customer: <email>` 422s with
            # "No lookup value found" unless the user already exists.
            "customer_id": f"guess:{customer_email}",
            "priority_id": _PRIORITY_ID_MAP.get(priority.lower(), 2),
            "state": "new",
            "article": {
                "subject": title,
                "body": body,
                "type": "note",
                "internal": False,
            },
        }
        _log.info("creating_zammad_ticket", customer=customer_email, priority=priority)
        res = await self._request("POST", "/api/v1/tickets", payload)
        if not res:
            return None
        number = res.get("number")
        ticket_id = res.get("id")
        if number is None or ticket_id is None:
            _log.warning("zammad_ticket_missing_ids", response_keys=sorted(res.keys()))
            return None
        return ZammadTicket(number=str(number), id=str(ticket_id))

    async def add_note(self, ticket_id: str, text: str) -> None:
        """Add an INTERNAL article (agent-only note) to an existing ticket."""
        payload = {
            "ticket_id": int(ticket_id) if ticket_id.isdigit() else ticket_id,
            "subject": "AI Handoff Summary",
            "body": text,
            "type": "note",
            "internal": True,
        }
        _log.info("adding_zammad_internal_note", ticket_id=ticket_id)
        await self._request("POST", "/api/v1/ticket_articles", payload)
