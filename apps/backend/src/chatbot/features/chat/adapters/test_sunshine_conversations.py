from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.features.chat.adapters.sunshine_conversations import (
    SunshineConversationsAdapter,
)
from chatbot.features.chat.models import HandoffOpenPayload, Message

_SECRET = "3XShlKXMsP8MfZygXYlfxGgSRW_test_secret"


def _adapter(webhook_secret: str = _SECRET) -> SunshineConversationsAdapter:
    settings = SimpleNamespace(
        zendesk_app_id="app-id",
        zendesk_key_id="key-id",
        zendesk_secret_key="secret-key",
        sunshine_webhook_secret=webhook_secret,
    )
    return SunshineConversationsAdapter(settings)  # type: ignore[arg-type]


def test_accepts_raw_shared_secret_in_x_api_key() -> None:
    """SC v2 sends the raw shared secret as the X-Api-Key header value."""
    adapter = _adapter()
    body = b'{"events":[]}'
    assert adapter.verify_webhook_signature(body, _SECRET) is True


def test_accepts_hmac_signature_for_back_compat() -> None:
    """Legacy/HMAC scheme: X-Api-Key carries hmac_sha256(secret, body)."""
    adapter = _adapter()
    body = b'{"events":[]}'
    sig = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert adapter.verify_webhook_signature(body, sig) is True


def test_rejects_wrong_value() -> None:
    adapter = _adapter()
    assert adapter.verify_webhook_signature(b'{"events":[]}', "nope") is False


def test_rejects_missing_signature() -> None:
    adapter = _adapter()
    assert adapter.verify_webhook_signature(b'{"events":[]}', None) is False


def test_accepts_all_when_no_secret_configured() -> None:
    adapter = _adapter(webhook_secret="")
    assert adapter.verify_webhook_signature(b'{"events":[]}', None) is True


@pytest.mark.asyncio
async def test_business_summary_includes_full_transcript() -> None:
    """The posted handoff summary must carry the WHOLE transcript, not the last 6.

    Regression guard: commit 3eb8ab2 passed the full transcript from the service
    layer, but the adapter independently re-sliced it to ``[-6:]`` — so the agent
    only ever saw the last 6 messages.
    """
    adapter = _adapter()

    # 10 messages — the first 4 fall outside any last-6 window.
    transcript = tuple(
        Message(role="user" if i % 2 == 0 else "assistant", text=f"line-{i:02d}") for i in range(10)
    )
    payload = HandoffOpenPayload(
        session_id="sim-9714",
        customer_name="Test Customer",
        customer_email="test@example.com",
        ai_summary="Customer needs help.",
        transcript=transcript,
    )

    response = MagicMock(status_code=201)
    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    await adapter._post_business_summary(client, "conv-1", payload)

    posted_text = client.post.call_args.kwargs["json"]["content"]["text"]
    for i in range(10):
        assert f"line-{i:02d}" in posted_text, f"message {i} missing from handoff summary"


def _lead_payload() -> HandoffOpenPayload:
    return HandoffOpenPayload(
        session_id="sim-9714",
        customer_name="Yuda",
        customer_email="yudaadi@proton.demo.com",
        ai_summary="Sales lead.",
        transcript=(),
        customer_phone="+601234567890",
        preferred_model="X50",
    )


@pytest.mark.asyncio
async def test_upsert_user_sends_phone_and_model_as_top_level_metadata() -> None:
    """Sunshine's profile schema only keeps givenName/surname/email/avatarUrl/locale.

    Phone and preferred_model must travel as TOP-LEVEL user metadata, not nested
    under profile, or Sunshine silently drops them (observed on user sim-9714).
    """
    adapter = _adapter()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=201))

    await adapter._upsert_user(client, _lead_payload(), "sim-9714")

    body = client.post.call_args.kwargs["json"]
    assert "phones" not in body["profile"]
    assert "metadata" not in body["profile"]
    assert body["metadata"]["preferred_model"] == "X50"
    assert body["metadata"]["customer_phone"] == "+601234567890"


@pytest.mark.asyncio
async def test_upsert_user_patch_on_conflict_includes_metadata() -> None:
    """On 409 the PATCH must also carry the top-level metadata, not just profile."""
    adapter = _adapter()
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=409))
    client.patch = AsyncMock(return_value=MagicMock(status_code=200))

    await adapter._upsert_user(client, _lead_payload(), "sim-9714")

    patch_body = client.patch.call_args.kwargs["json"]
    assert patch_body["metadata"]["preferred_model"] == "X50"
    assert patch_body["metadata"]["customer_phone"] == "+601234567890"
    assert "phones" not in patch_body["profile"]
