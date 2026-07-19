from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.platform.config import Settings


class _FakeAdapter:
    """Stub that replaces _request/_base on a PresenceFetcher."""

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.calls: list[tuple[str, str]] = []
        self._responses = responses

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path))
        for (m, sub), resp in self._responses.items():
            if m == method and sub in path:
                return resp
        return None

    def _base(self) -> str:
        return "http://cw/api/v1/accounts/1"


def _fetcher(fake: _FakeAdapter) -> PresenceFetcher:
    f = PresenceFetcher(Settings())
    f._request = fake._request  # type: ignore[method-assign]
    f._base = fake._base  # type: ignore[method-assign]
    return f


@pytest.mark.asyncio
async def test_fetch_agents_returns_list() -> None:
    fake = _FakeAdapter(
        {
            ("GET", "/agents"): [
                {"id": 1, "name": "Alice", "availability_status": "online"},
                {"id": 2, "name": "Bob", "availability_status": "busy"},
                {"id": 3, "name": "Carol", "availability_status": "offline"},
            ]
        }
    )
    fetcher = _fetcher(fake)
    agents = await fetcher.fetch_agents()
    assert len(agents) == 3
    assert agents[0] == AgentRecord(id=1, name="Alice", availability_status="online")
    assert agents[1] == AgentRecord(id=2, name="Bob", availability_status="busy")
    assert agents[2] == AgentRecord(id=3, name="Carol", availability_status="offline")


@pytest.mark.asyncio
async def test_fetch_agents_tolerates_none_response() -> None:
    fake = _FakeAdapter({})  # _request returns None for unknown paths
    fetcher = _fetcher(fake)
    agents = await fetcher.fetch_agents()
    assert agents == []


@pytest.mark.asyncio
async def test_fetch_agent_availability_found() -> None:
    fake = _FakeAdapter(
        {
            ("GET", "/agents"): [
                {"id": 7, "name": "Dana", "availability_status": "online"},
            ]
        }
    )
    fetcher = _fetcher(fake)
    status = await fetcher.fetch_agent_availability(7)
    assert status == "online"


@pytest.mark.asyncio
async def test_fetch_agent_availability_not_found_returns_offline() -> None:
    fake = _FakeAdapter(
        {("GET", "/agents"): [{"id": 1, "name": "Alice", "availability_status": "online"}]}
    )
    fetcher = _fetcher(fake)
    status = await fetcher.fetch_agent_availability(999)
    assert status == "offline"


@pytest.mark.asyncio
async def test_fetch_agents_calls_correct_path() -> None:
    fake = _FakeAdapter({("GET", "/agents"): []})
    fetcher = _fetcher(fake)
    await fetcher.fetch_agents()
    assert ("GET", "/agents") in fake.calls
