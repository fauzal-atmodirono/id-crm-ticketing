from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.features.chat.ports import ConversationLogResult
from chatbot.platform.config import get_settings


def _client_returning(response: httpx.Response) -> MagicMock:
    client = MagicMock()
    client.put = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _adapter() -> ZendeskAdapter:
    settings = SimpleNamespace(
        zendesk_subdomain="proton",
        zendesk_email="agent@proton.test",
        zendesk_api_token="tok",
        handoff_store="memory",
    )
    return ZendeskAdapter(settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_conversation_ticket_creates_once_and_caches() -> None:
    adapter = _adapter()
    response = MagicMock(status_code=201, raise_for_status=lambda: None)
    response.json = lambda: {"ticket": {"id": 4242}}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        first = await adapter.ensure_conversation_ticket(
            "whatsapp-+60123456789", "[WhatsApp] chat", "Yuda", "+60123456789"
        )
        second = await adapter.ensure_conversation_ticket(
            "whatsapp-+60123456789", "[WhatsApp] chat", "Yuda", "+60123456789"
        )

    assert first == "4242"
    assert second == "4242"
    # Created exactly once; second call is served from cache.
    assert client.post.call_count == 1
    body = client.post.call_args.kwargs["json"]["ticket"]
    assert body["external_id"] == "whatsapp-+60123456789"


@pytest.mark.asyncio
async def test_append_conversation_comment_sets_status_private() -> None:
    adapter = _adapter()
    response = MagicMock(status_code=200, raise_for_status=lambda: None)
    client = MagicMock()
    client.put = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        await adapter.append_conversation_comment("4242", "USER: hi", status="solved")

    payload = client.put.call_args.kwargs["json"]["ticket"]
    assert payload["comment"]["public"] is False
    assert payload["comment"]["body"] == "USER: hi"
    assert payload["status"] == "solved"


@pytest.mark.asyncio
async def test_append_returns_ok_on_success() -> None:
    adapter = _adapter()
    req = httpx.Request("PUT", "https://proton.zendesk.com/api/v2/tickets/49.json")
    client = _client_returning(httpx.Response(200, request=req))

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        result = await adapter.append_conversation_comment("49", "USER: hi")

    assert result is ConversationLogResult.OK


@pytest.mark.asyncio
async def test_append_returns_ticket_closed_on_422_closed() -> None:
    adapter = _adapter()
    req = httpx.Request("PUT", "https://proton.zendesk.com/api/v2/tickets/49.json")
    body = (
        '{"details":{"status":[{"error":"ClosedTicket",'
        '"description":"Status: closed prevents ticket update"}]}}'
    )
    client = _client_returning(httpx.Response(422, text=body, request=req))

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        result = await adapter.append_conversation_comment("49", "USER: hi")

    assert result is ConversationLogResult.TICKET_CLOSED


@pytest.mark.asyncio
async def test_append_returns_ticket_closed_on_404() -> None:
    adapter = _adapter()
    req = httpx.Request("PUT", "https://proton.zendesk.com/api/v2/tickets/49.json")
    client = _client_returning(httpx.Response(404, text="Not found", request=req))

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        result = await adapter.append_conversation_comment("49", "USER: hi")

    assert result is ConversationLogResult.TICKET_CLOSED


@pytest.mark.asyncio
async def test_append_returns_failed_on_transient_5xx() -> None:
    adapter = _adapter()
    req = httpx.Request("PUT", "https://proton.zendesk.com/api/v2/tickets/49.json")
    client = _client_returning(httpx.Response(503, text="upstream", request=req))

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        result = await adapter.append_conversation_comment("49", "USER: hi")

    # A non-closed error must NOT masquerade as closed (would spawn a dup ticket).
    assert result is ConversationLogResult.FAILED


@pytest.mark.asyncio
async def test_append_returns_failed_on_unrelated_422() -> None:
    adapter = _adapter()
    req = httpx.Request("PUT", "https://proton.zendesk.com/api/v2/tickets/49.json")
    client = _client_returning(httpx.Response(422, text="some other validation error", request=req))

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        result = await adapter.append_conversation_comment("49", "USER: hi")

    assert result is ConversationLogResult.FAILED


@pytest.mark.asyncio
async def test_rotate_conversation_ticket_creates_fresh_ticket_ignoring_cache() -> None:
    adapter = _adapter()

    # Prime the cache with an original ticket via ensure.
    first_resp = MagicMock(status_code=201, raise_for_status=lambda: None)
    first_resp.json = lambda: {"ticket": {"id": 4242}}
    ensure_client = _client_returning(first_resp)
    with patch(
        "chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=ensure_client
    ):
        original = await adapter.ensure_conversation_ticket(
            "whatsapp-+60123", "[WhatsApp] chat", "Yuda", "+60123"
        )
    assert original == "4242"

    # Rotate: must POST a brand-new ticket even though one is cached.
    rot_resp = MagicMock(status_code=201, raise_for_status=lambda: None)
    rot_resp.json = lambda: {"ticket": {"id": 5555}}
    rot_client = _client_returning(rot_resp)
    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=rot_client):
        rotated = await adapter.rotate_conversation_ticket(
            "whatsapp-+60123", "[WhatsApp] chat", "Yuda", "+60123"
        )
        # A later ensure now returns the rotated ticket (cache was updated).
        after = await adapter.ensure_conversation_ticket(
            "whatsapp-+60123", "[WhatsApp] chat", "Yuda", "+60123"
        )

    assert rotated == "5555"
    assert after == "5555"
    assert rot_client.post.call_count == 1  # ensure-after served from updated cache


async def test_post_public_reply_posts_public_comment_with_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None: ...

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None: ...

        async def put(
            self,
            url: str,
            json: object,
            headers: object,
            timeout: object,  # noqa: ASYNC109
        ) -> _Resp:
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", _Client)

    adapter = ZendeskAdapter(get_settings())
    await adapter.post_public_reply("123", "Hello from AI", status="pending")

    assert "/tickets/123.json" in captured["url"]
    comment = captured["json"]["ticket"]["comment"]
    assert comment["body"] == "Hello from AI"
    assert comment["public"] is True
    assert captured["json"]["ticket"]["status"] == "pending"
