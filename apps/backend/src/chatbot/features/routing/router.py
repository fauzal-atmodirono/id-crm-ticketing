from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class _PriorityIn(BaseModel):
    agent_id: int
    channel_priorities: list[str]


class _PriorityUpdate(BaseModel):
    channel_priorities: list[str]


class _AgentOut(BaseModel):
    id: int
    name: str
    availability_status: str


class _PriorityOut(BaseModel):
    agent_id: int
    channel_priorities: list[str]


def _require_api_key(settings: "Settings"):
    """Return a FastAPI dependency that 401s when the x-api-key header is wrong."""

    def _check(x_api_key: str | None = Header(default=None)) -> None:
        expected = settings.routing_admin_api_key
        if not expected or not x_api_key:
            raise HTTPException(status_code=401, detail="Missing or invalid API key")
        if not hmac.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=401, detail="Missing or invalid API key")

    return _check


def build_routing_router(
    settings: "Settings",
    store: ChannelPriorityStore,
    presence: PresenceFetcher,
) -> APIRouter:
    """Build and return the routing config FastAPI router."""
    router = APIRouter(tags=["routing"])
    auth = _require_api_key(settings)

    @router.get("/routing/agents", response_model=list[_AgentOut])
    async def list_agents() -> list[AgentRecord]:
        return await presence.fetch_agents()

    @router.get("/routing/priorities", response_model=list[_PriorityOut])
    async def list_priorities() -> list[AgentPriority]:
        return await store.list_all()

    @router.post(
        "/routing/priorities",
        response_model=_PriorityOut,
        dependencies=[Depends(auth)],
    )
    async def create_priority(body: _PriorityIn) -> AgentPriority:
        await store.set(body.agent_id, body.channel_priorities)
        return AgentPriority(
            agent_id=body.agent_id, channel_priorities=body.channel_priorities
        )

    @router.put(
        "/routing/priorities/{agent_id}",
        response_model=_PriorityOut,
        dependencies=[Depends(auth)],
    )
    async def update_priority(agent_id: int, body: _PriorityUpdate) -> AgentPriority:
        await store.set(agent_id, body.channel_priorities)
        return AgentPriority(agent_id=agent_id, channel_priorities=body.channel_priorities)

    @router.delete("/routing/priorities/{agent_id}", dependencies=[Depends(auth)])
    async def delete_priority(agent_id: int) -> dict[str, str]:
        await store.delete(agent_id)
        return {"status": "deleted", "agent_id": str(agent_id)}

    return router
