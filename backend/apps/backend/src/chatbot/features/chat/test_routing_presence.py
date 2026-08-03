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

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
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


class _PagedAdapter:
    """Stub returning a distinct response per page, keyed by the page query param."""

    def __init__(self, pages: dict[int, Any]) -> None:
        self.calls: list[str] = []
        self._pages = pages

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        self.calls.append(path)
        if "/conversations" not in path:
            return None
        page_num = int(path.rsplit("page=", 1)[1])
        return self._pages.get(page_num)

    def _base(self) -> str:
        return "http://cw/api/v1/accounts/1"


def _paged_fetcher(fake: _PagedAdapter) -> PresenceFetcher:
    f = PresenceFetcher(Settings())
    f._request = fake._request  # type: ignore[method-assign]
    f._base = fake._base  # type: ignore[method-assign]
    return f


def _conv(conv_id: int, assignee_id: int | None) -> dict[str, Any]:
    conv: dict[str, Any] = {"id": conv_id, "status": "open"}
    if assignee_id is not None:
        conv["meta"] = {"assignee": {"id": assignee_id}}
    else:
        conv["meta"] = {}
    return conv


@pytest.mark.asyncio
async def test_fetch_agent_open_counts_tallies_per_agent() -> None:
    # Mirrors the real Chatwoot conversations-list shape observed against a
    # live local instance: {"data": {"meta": {...}, "payload": [...]}}.
    fake = _PagedAdapter(
        {
            1: {
                "data": {
                    "meta": {
                        "mine_count": 3,
                        "assigned_count": 3,
                        "unassigned_count": 0,
                        "all_count": 3,
                    },
                    "payload": [
                        _conv(1, assignee_id=10),
                        _conv(2, assignee_id=10),
                        _conv(3, assignee_id=20),
                    ],
                }
            },
            2: {"data": {"meta": {}, "payload": []}},
        }
    )
    fetcher = _paged_fetcher(fake)
    counts = await fetcher.fetch_agent_open_counts()
    assert counts == {10: 2, 20: 1}


@pytest.mark.asyncio
async def test_fetch_agent_open_counts_paginates_until_empty_page() -> None:
    fake = _PagedAdapter(
        {
            1: {"data": {"meta": {}, "payload": [_conv(1, assignee_id=10)]}},
            2: {"data": {"meta": {}, "payload": [_conv(2, assignee_id=10)]}},
            3: {"data": {"meta": {}, "payload": []}},
        }
    )
    fetcher = _paged_fetcher(fake)
    counts = await fetcher.fetch_agent_open_counts()
    assert counts == {10: 2}
    assert any("page=3" in c for c in fake.calls)


@pytest.mark.asyncio
async def test_fetch_agent_open_counts_skips_unassigned_conversations() -> None:
    fake = _PagedAdapter(
        {
            1: {"data": {"meta": {}, "payload": [_conv(1, assignee_id=None)]}},
            2: {"data": {"meta": {}, "payload": []}},
        }
    )
    fetcher = _paged_fetcher(fake)
    counts = await fetcher.fetch_agent_open_counts()
    assert counts == {}


@pytest.mark.asyncio
async def test_fetch_agent_open_counts_returns_empty_dict_on_failure() -> None:
    class _RaisingAdapter:
        async def _request(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

        def _base(self) -> str:
            return "http://cw/api/v1/accounts/1"

    fetcher = PresenceFetcher(Settings())
    raising = _RaisingAdapter()
    fetcher._request = raising._request  # type: ignore[method-assign]
    fetcher._base = raising._base  # type: ignore[method-assign]
    counts = await fetcher.fetch_agent_open_counts()
    assert counts == {}


@pytest.mark.asyncio
async def test_fetch_agent_open_counts_discards_partial_tally_on_mid_page_failure() -> None:
    """Page 1 succeeds (contributing to the running tally) and page 2's
    ``_request`` returns ``None`` -- the real, non-raising way ``_request``
    surfaces an HTTP failure (it swallows exceptions internally and never
    raises). The whole call must fail open to ``{}``, not leak the partial
    count accumulated from page 1: pick_agent must never see a tally that
    silently undercounts a busy agent's real open-conversation load."""
    fake = _PagedAdapter(
        {
            1: {
                "data": {
                    "meta": {},
                    "payload": [_conv(1, assignee_id=10), _conv(2, assignee_id=10)],
                }
            },
            # page 2 deliberately omitted -> _PagedAdapter._request returns
            # None for it, mirroring _request's real failure behavior.
        }
    )
    fetcher = _paged_fetcher(fake)
    counts = await fetcher.fetch_agent_open_counts()
    assert counts == {}
