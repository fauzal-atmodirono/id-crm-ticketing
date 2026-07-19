from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AgentRecord:
    id: int
    name: str
    availability_status: str  # "online" | "busy" | "offline"


class PresenceFetcher:
    """Reads agent availability from the Chatwoot account API.

    Inherits the _request / _base pattern from ChatwootAdapter so the same
    dual-auth-header httpx pattern is used without duplication. In production
    a ChatwootAdapter instance is passed and its _request/_base are used
    directly; in tests a _FakeAdapter stub is monkey-patched in.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        # Deferred import avoids a circular dependency between the routing
        # package and the chat adapter package.
        import httpx  # noqa: PLC0415

        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        url = f"{self._base()}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(
                    method, url, json=payload, headers=headers, timeout=10.0
                )
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("presence_request_failed", method=method, path=path, error=str(e))
            return None

    async def fetch_agents(self) -> list[AgentRecord]:
        """Return all agents on this Chatwoot account with their availability status.

        Calls GET /api/v1/accounts/{account_id}/agents. Returns an empty list
        when Chatwoot is unreachable or the response is not a list.
        """
        res = await self._request("GET", "/agents")
        if not isinstance(res, list):
            _log.warning("presence_fetch_agents_unexpected_response", response_type=type(res).__name__)
            return []
        agents: list[AgentRecord] = []
        for item in res:
            if not isinstance(item, dict):
                continue
            agent_id = item.get("id")
            name = item.get("name") or ""
            status = item.get("availability_status") or "offline"
            if agent_id is None:
                continue
            agents.append(AgentRecord(id=int(agent_id), name=name, availability_status=status))
        return agents

    async def fetch_agent_availability(self, agent_id: int) -> str:
        """Return the availability status string for a single agent.

        Returns ``"offline"`` when the agent is not found or Chatwoot is
        unreachable — a safe default that prevents routing to unknown agents.
        """
        agents = await self.fetch_agents()
        for agent in agents:
            if agent.id == agent_id:
                return agent.availability_status
        return "offline"
