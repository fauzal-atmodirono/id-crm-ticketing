from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

from chatbot.features.chat.adapters.sunshine_conversations import (
    SunshineConversationsAdapter,
)

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
