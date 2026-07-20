from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.features.chat.adapters.zendesk import ZendeskAdapter


def _adapter() -> ZendeskAdapter:
    settings = SimpleNamespace(
        zendesk_subdomain="proton",
        zendesk_email="agent@proton.test",
        zendesk_api_token="tok",
    )
    return ZendeskAdapter(settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_add_ticket_tag_puts_to_tags_endpoint() -> None:
    adapter = _adapter()
    response = MagicMock(status_code=200, raise_for_status=lambda: None)
    client = MagicMock()
    client.put = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", return_value=client):
        await adapter.add_ticket_tag("4242", "csat_4")

    url = client.put.call_args.args[0]
    assert url == "https://proton.zendesk.com/api/v2/tickets/4242/tags.json"
    assert client.put.call_args.kwargs["json"] == {"tags": ["csat_4"]}
