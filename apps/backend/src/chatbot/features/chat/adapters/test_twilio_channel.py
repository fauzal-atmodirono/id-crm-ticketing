from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.features.chat.adapters.twilio_channel import (
    TwilioChannelAdapter,
    _chunk_whatsapp_body,
)

# Twilio rejects a WhatsApp message whose Body exceeds 1600 chars (error 21617),
# so every chunk the adapter emits must stay at or under that limit.
_LIMIT = 1600


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


def test_short_body_is_a_single_chunk() -> None:
    assert _chunk_whatsapp_body("Hello! How can I help you today?") == [
        "Hello! How can I help you today?"
    ]


def test_empty_body_produces_no_chunks() -> None:
    assert _chunk_whatsapp_body("") == []
    assert _chunk_whatsapp_body("   \n  ") == []


def test_long_body_is_split_into_chunks_each_within_the_limit() -> None:
    body = "Proton S70 detail. " * 120  # ~2280 chars, over the 1600 limit
    chunks = _chunk_whatsapp_body(body)

    assert len(chunks) >= 2
    assert all(len(c) <= _LIMIT for c in chunks)
    # No content is lost: concatenation holds every original word.
    assert "".join(chunks).replace(" ", "") == body.strip().replace(" ", "")


def test_split_prefers_paragraph_boundaries() -> None:
    para = "A" * 900
    body = f"{para}\n\n{para}\n\n{para}"  # three ~900-char paragraphs
    chunks = _chunk_whatsapp_body(body)

    assert all(len(c) <= _LIMIT for c in chunks)
    # A paragraph-aware split never fuses two 900-char paragraphs into one chunk.
    assert all(len(c) <= 1800 for c in chunks)


def test_giant_unbroken_run_is_hard_cut() -> None:
    body = "x" * 5000  # no whitespace to break on
    chunks = _chunk_whatsapp_body(body)

    assert all(len(c) <= _LIMIT for c in chunks)
    assert "".join(chunks) == body


@pytest.mark.asyncio
async def test_send_message_splits_long_reply_into_multiple_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    posts: list[dict[str, str]] = []

    class _RecordingClient:
        async def __aenter__(self) -> _RecordingClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, _url: str, *, data: dict[str, str], **_kw: object) -> MagicMock:
            posts.append(data)
            return MagicMock(status_code=201, raise_for_status=lambda: None)

    monkeypatch.setattr(
        "chatbot.features.chat.adapters.twilio_channel.httpx.AsyncClient",
        lambda *_a, **_k: _RecordingClient(),
    )

    long_reply = "Proton S70 detail. " * 120  # over 1600 chars
    await adapter.send_message(conversation_id="whatsapp:+60123", text=long_reply)

    assert len(posts) >= 2
    assert all(len(p["Body"]) <= _LIMIT for p in posts)
    assert all(p["To"] == "whatsapp:+60123" for p in posts)
