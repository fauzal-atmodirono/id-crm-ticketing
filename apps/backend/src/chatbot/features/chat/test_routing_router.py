from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.router import build_routing_router
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore
from chatbot.platform.config import Settings


def _app(api_key: str = "secret") -> tuple[TestClient, ChannelPriorityStore]:
    settings = Settings(routing_admin_api_key=api_key)
    store = ChannelPriorityStore(settings)
    store_data: dict[int, AgentPriority] = {}

    async def _list_all() -> list[AgentPriority]:
        return list(store_data.values())

    async def _get(agent_id: int) -> AgentPriority | None:
        return store_data.get(agent_id)

    async def _set(agent_id: int, channel_priorities: list[str]) -> None:
        store_data[agent_id] = AgentPriority(
            agent_id=agent_id, channel_priorities=channel_priorities
        )

    async def _delete(agent_id: int) -> None:
        store_data.pop(agent_id, None)

    store.list_all = _list_all  # type: ignore[method-assign]
    store.get = _get  # type: ignore[method-assign]
    store.set = _set  # type: ignore[method-assign]
    store.delete = _delete  # type: ignore[method-assign]

    fetcher = PresenceFetcher(settings)

    async def _fetch_agents() -> list[AgentRecord]:
        return [
            AgentRecord(id=1, name="Alice", availability_status="online"),
            AgentRecord(id=2, name="Bob", availability_status="busy"),
        ]

    fetcher.fetch_agents = _fetch_agents  # type: ignore[method-assign]

    app = FastAPI()
    app.include_router(build_routing_router(settings, store, fetcher))
    return TestClient(app), store


def test_get_agents_returns_list() -> None:
    client, _ = _app()
    resp = client.get("/routing/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["availability_status"] == "online"


def test_get_priorities_empty() -> None:
    client, _ = _app()
    resp = client.get("/routing/priorities")
    assert resp.status_code == 200
    assert resp.json() == []


def test_post_priority_requires_api_key() -> None:
    client, _ = _app()
    resp = client.post(
        "/routing/priorities",
        json={"agent_id": 1, "channel_priorities": ["WhatsApp"]},
    )
    assert resp.status_code == 401


def test_post_priority_creates_entry() -> None:
    client, _ = _app()
    resp = client.post(
        "/routing/priorities",
        json={"agent_id": 1, "channel_priorities": ["WhatsApp", "email"]},
        headers={"x-api-key": "secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == 1
    assert body["channel_priorities"] == ["WhatsApp", "email"]


def test_put_priority_updates_entry() -> None:
    client, _ = _app()
    client.post(
        "/routing/priorities",
        json={"agent_id": 2, "channel_priorities": ["email"]},
        headers={"x-api-key": "secret"},
    )
    resp = client.put(
        "/routing/priorities/2",
        json={"channel_priorities": ["web", "email"]},
        headers={"x-api-key": "secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["channel_priorities"] == ["web", "email"]


def test_delete_priority_removes_entry() -> None:
    client, _ = _app()
    client.post(
        "/routing/priorities",
        json={"agent_id": 3, "channel_priorities": ["WhatsApp"]},
        headers={"x-api-key": "secret"},
    )
    resp = client.delete("/routing/priorities/3", headers={"x-api-key": "secret"})
    assert resp.status_code == 200
    resp2 = client.get("/routing/priorities")
    ids = [p["agent_id"] for p in resp2.json()]
    assert 3 not in ids


def test_delete_requires_api_key() -> None:
    client, _ = _app()
    resp = client.delete("/routing/priorities/1")
    assert resp.status_code == 401
