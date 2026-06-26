from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter


def _adapter() -> TwilioChannelAdapter:
    settings = SimpleNamespace(
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_whatsapp_number="whatsapp:+60111111111",
    )
    return TwilioChannelAdapter(settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_post_message_calls_twilio_messages_endpoint() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=201, raise_for_status=lambda: None))

    await adapter._post_message(client, "whatsapp:+60123456789", "Hi there")

    url = client.post.call_args.args[0]
    assert url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    sent = client.post.call_args.kwargs["data"]
    assert sent["From"] == "whatsapp:+60111111111"
    assert sent["To"] == "whatsapp:+60123456789"
    assert sent["Body"] == "Hi there"
    # Basic auth = (account_sid, auth_token)
    assert client.post.call_args.kwargs["auth"] == ("AC123", "tok")


@pytest.mark.asyncio
async def test_post_message_includes_media_url_when_given() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=201, raise_for_status=lambda: None))

    await adapter._post_message(
        client, "whatsapp:+60123456789", "Proton X50", media_url="https://img/x50.jpg"
    )

    sent = client.post.call_args.kwargs["data"]
    assert sent["MediaUrl"] == "https://img/x50.jpg"
    assert sent["Body"] == "Proton X50"


@pytest.mark.asyncio
async def test_post_message_omits_media_url_when_absent() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=201, raise_for_status=lambda: None))

    await adapter._post_message(client, "whatsapp:+60123456789", "hi")

    assert "MediaUrl" not in client.post.call_args.kwargs["data"]
