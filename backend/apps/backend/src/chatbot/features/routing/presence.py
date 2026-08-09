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
    # P2: PIC records key on email, not Chatwoot agent id, so joining presence
    # to a PIC needs this. Defaulted for every existing construction site.
    email: str = ""


class PresenceFetcher:
    """Reads agent availability from the Chatwoot account API.

    Self-contained: owns its own httpx client and constructs dual-auth headers
    (both ``api_access_token`` and ``Api-Access-Token``) from ``settings``
    directly. It is not coupled to ChatwootAdapter; ``main.py`` constructs it
    standalone with just the settings object.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
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
                res = await client.request(method, url, json=payload, headers=headers, timeout=10.0)
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
            _log.warning(
                "presence_fetch_agents_unexpected_response", response_type=type(res).__name__
            )
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
            agents.append(
                AgentRecord(
                    id=int(agent_id),
                    name=name,
                    availability_status=status,
                    email=str(item.get("email") or ""),
                )
            )
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

    async def fetch_agent_open_counts(self) -> dict[int, int]:
        """agent_id -> count of currently-open conversations assigned to them.

        Calls ``GET /conversations?status=open&page=N``, paging until a page
        returns an empty conversation list. Verified against a live local
        Chatwoot instance: the response is
        ``{"data": {"meta": {...counts...}, "payload": [...]}}`` — there is
        no ``total_pages``/``next_page`` field in ``meta`` (unlike this
        method's first-draft assumption), so pagination has to stop on an
        empty page instead, matching the same convention already used by
        ``chatbot.features.metrics.sync.fetch_conversations``. Each
        conversation's assignee is at ``conversation["meta"]["assignee"]["id"]``
        (a *different* ``meta`` key, nested per-conversation rather than the
        page-level one).

        Empty dict on any failure (fail-open -- the cap check in pick_agent
        becomes a no-op rather than blocking routing when this can't be
        determined). This is unconditional: ``_request`` itself never raises
        (it swallows HTTP errors and returns ``None``), so a failed fetch on
        *any* page -- including a page after earlier pages already
        succeeded -- is treated as a failure of the whole call and returns
        ``{}`` rather than the partial tally accumulated so far. A partial
        undercount would be worse than no data: pick_agent would treat
        agents as having fewer open conversations than they really do.
        """
        counts: dict[int, int] = {}
        page = 1
        try:
            while True:
                res = await self._request("GET", f"/conversations?status=open&page={page}")
                if not isinstance(res, dict):
                    # _request itself never raises -- it already swallows
                    # HTTP errors and returns None -- so this is how a
                    # mid-pagination failure actually surfaces. Discard any
                    # partial tally from earlier pages rather than returning
                    # an undercount.
                    _log.error(
                        "presence_fetch_open_counts_failed",
                        error="page request returned non-dict response",
                        page=page,
                    )
                    return {}
                data = res.get("data")
                payload = data.get("payload") if isinstance(data, dict) else res.get("payload")
                if not isinstance(payload, list) or not payload:
                    break
                for conv in payload:
                    if not isinstance(conv, dict):
                        continue
                    assignee = (conv.get("meta") or {}).get("assignee") or {}
                    agent_id = assignee.get("id")
                    if agent_id is not None:
                        counts[int(agent_id)] = counts.get(int(agent_id), 0) + 1
                page += 1
            return counts
        except Exception as e:
            _log.error("presence_fetch_open_counts_failed", error=str(e))
            return {}
