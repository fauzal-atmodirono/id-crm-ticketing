from __future__ import annotations

from unittest.mock import AsyncMock

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
    app.include_router(build_routing_router(settings, store, fetcher, AsyncMock(), AsyncMock()))
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


def test_put_priority_requires_api_key() -> None:
    client, _ = _app()
    resp = client.put(
        "/routing/priorities/1",
        json={"channel_priorities": ["web"]},
    )
    assert resp.status_code == 401


# --- /routing/assign tests ---


def _assign_app(routing_enabled: bool, pick_result: int | None, key: str = "k"):  # type: ignore[return]
    settings = Settings(
        routing_admin_api_key=key,
        routing_enabled=routing_enabled,
        chatwoot_api_url="http://cw",
        chatwoot_account_id=1,
        chatwoot_api_token="t",
    )
    store = AsyncMock()
    presence = AsyncMock()
    routing_svc = AsyncMock()
    routing_svc.pick_agent = AsyncMock(return_value=pick_result)
    assigner = AsyncMock()
    assigner.resolve_channel = AsyncMock(return_value="whatsapp")
    assigner.assign = AsyncMock()
    app = FastAPI()
    app.include_router(build_routing_router(settings, store, presence, routing_svc, assigner))
    return TestClient(app), assigner, routing_svc


def test_assign_picks_and_assigns() -> None:
    client, assigner, svc = _assign_app(True, 9)
    r = client.post("/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "k"})
    assert r.status_code == 200 and r.json()["assigned_agent_id"] == 9
    assigner.assign.assert_awaited_once_with(5, 9)


def test_assign_no_agent_no_assign() -> None:
    client, assigner, svc = _assign_app(True, None)
    r = client.post("/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "k"})
    assert r.json()["assigned_agent_id"] is None
    assigner.assign.assert_not_awaited()


def test_assign_disabled_noop() -> None:
    client, assigner, svc = _assign_app(False, 9)
    r = client.post("/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "k"})
    assert r.json() == {"assigned_agent_id": None, "disabled": True}
    svc.pick_agent.assert_not_awaited()
    assigner.assign.assert_not_awaited()


def test_assign_auth_401_without_key() -> None:
    client, _, _ = _assign_app(True, 9)
    assert client.post("/routing/assign", json={"conversation_id": 5}).status_code == 401


def test_assign_accepts_proton_backend_key() -> None:
    settings = Settings(
        proton_backend_key="pk",
        routing_enabled=True,
        chatwoot_api_url="http://cw",
        chatwoot_account_id=1,
        chatwoot_api_token="t",
    )
    store = AsyncMock()
    presence = AsyncMock()
    svc = AsyncMock()
    svc.pick_agent = AsyncMock(return_value=None)
    assigner = AsyncMock()
    assigner.resolve_channel = AsyncMock(return_value="whatsapp")
    app = FastAPI()
    app.include_router(build_routing_router(settings, store, presence, svc, assigner))
    client = TestClient(app)
    assert (
        client.post(
            "/routing/assign", json={"conversation_id": 5}, headers={"x-api-key": "pk"}
        ).status_code
        == 200
    )
