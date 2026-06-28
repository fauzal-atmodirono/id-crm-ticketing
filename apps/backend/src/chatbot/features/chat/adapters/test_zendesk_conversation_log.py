from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.platform.config import get_settings


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
